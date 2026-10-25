"""SQLite persistence tests, independent of device recognition and JSON configuration."""
import copy
from contextlib import closing
import json
import multiprocessing
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from module.runtime.fleet_database import FleetDatabase, FleetDatabaseAlreadyImported, FleetDatabaseConflict
from module.runtime.fleet_tech import revision


def snapshot(tag='first', previous=None):
    value = dict(version=1, revision=tag, ships=[
        dict(group_type=10102, level=125, slot=3, name='杜威'),
        dict(group_type=10102, level=100, slot={'page': 1, 'row': 2}, name='杜威'),
        dict(level=0, slot=5, review=True, source='unknown')],
        history=dict(observed={'10102': 125, '10103': 110},
                     current={'10102': 125}, absent=['10103', '10104']),
        updated_at=tag, min_level=1, max_level=125, expected_count=None,
        physical_count=3, complete=False, source='scan')
    if previous:
        value['history']['observed'].update(previous['history']['observed'])
    return value


def increment_worker(config_path, barrier, count):
    database = FleetDatabase(Path(config_path))
    barrier.wait(timeout=15)
    for _ in range(count):
        with database.edit() as state:
            state['mind']['counter'] = state['mind'].get('counter', 0) + 1


def scan_worker(config_path, barrier, tag, queue):
    database = FleetDatabase(Path(config_path))
    barrier.wait(timeout=15)
    try:
        database.save_scan(snapshot(tag), revision({}))
    except FleetDatabaseConflict:
        queue.put('conflict')
    else:
        queue.put('saved')


def crash_editor_worker(config_path):
    database = FleetDatabase(Path(config_path))
    original = database._write_options

    def crash_after_spilling(connection, scope, options):
        connection.execute('PRAGMA cache_size=2')
        connection.execute('PRAGMA cache_spill=ON')
        original(connection, scope, options)
        os._exit(23)

    database._write_options = crash_after_spilling
    with database.edit() as state:
        state['mind'] = {'overrides': {str(index): {'source': 'x' * 4096} for index in range(400)}}
        state['technology'] = {'manual': {'10102': {'owned': False}}}


def import_worker(config_path, barrier, key, queue):
    database = FleetDatabase(Path(config_path))
    barrier.wait(timeout=15)
    try:
        with database.edit(import_legacy=True) as state:
            state['technology'] = {'manual': {key: {'owned': True}}}
            state['legacy_snapshots'] = [snapshot(key)]
            state['legacy_sources'] = {'FleetTechScan.FleetTech.Record': {'manual': {key: {'owned': True}}}}
    except FleetDatabaseAlreadyImported:
        queue.put('already_imported')
    else:
        queue.put('imported')


class FleetDatabaseTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.config = Path(self.temp.name) / 'pilot.json'
        self.db = FleetDatabase(self.config)

    def assert_counts(self, **counts):
        with closing(sqlite3.connect(self.db.path)) as connection, connection:
            for table, expected in counts.items():
                self.assertEqual(connection.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0],
                                 expected, table)

    def test_missing_reads_ignore_legacy_configuration_and_create_nothing(self):
        self.config.write_text('{"DockScan":{"Dock":{"Result":{"ships":[1]}}}}')
        original = self.config.read_bytes()
        self.assertEqual(self.db.load(), dict(snapshot=None, mind={}, technology={}))
        self.assertEqual(self.db.load(include_import_status=True),
                         dict(snapshot=None, mind={}, technology={}, legacy_imported=False))
        self.assertFalse(self.db.path.parent.exists())
        self.assertEqual(self.config.read_bytes(), original)
        self.assertEqual(self.db.path, self.config.parent / 'fleet' / 'pilot.sqlite3')

    def test_roundtrip_keeps_duplicate_physical_copies_and_normalized_history(self):
        first = snapshot()
        self.db.save_scan(first, revision({}))
        self.assertEqual(self.db.load(), dict(snapshot=first, mind={}, technology={}))
        second = snapshot('second', first)
        second['ships'] = []
        second['history']['current'] = {}
        second['history']['absent'].append('10102')
        self.db.save_scan(second, first['revision'])
        self.assertEqual(self.db.load()['snapshot'], second)
        self.assert_counts(scan_batches=2, scan_cards=3, scan_evidence=10,
                           scan_history_fields=6)
        with closing(sqlite3.connect(self.db.path)) as connection, connection:
            metadata = connection.execute('SELECT metadata FROM scan_batches').fetchall()
            self.assertTrue(all('"ships"' not in row[0] and '"history"' not in row[0]
                                for row in metadata))
            self.assertEqual(connection.execute('PRAGMA user_version').fetchone()[0], 1)

    def test_optional_snapshot_fields_preserve_empty_and_missing(self):
        for index, history in enumerate((None, {}, {'observed': {}, 'absent': []})):
            value = dict(revision=str(index), ships=[])
            if history is not None:
                value['history'] = history
            expected = revision({}) if index == 0 else str(index - 1)
            self.db.save_scan(value, expected)
            self.assertEqual(self.db.load()['snapshot'], value)

    def test_analysis_rows_roundtrip_without_mutating_scan(self):
        first = snapshot()
        self.db.save_scan(first, revision({}))
        mind = dict(overrides={'group:10102': {'excluded': True}, 'review:a': {'level': 90}},
                    removed=['group:10103', 'review:b'], extra=[{'name': 'manual', 'level': 100}],
                    snapshot_revision='first', updated_at='now')
        technology = dict(manual={'10102': {'owned': False, 'level_toggle': {'low': 100, 'high': 120}}},
                          nations={'1': 2, '3': 0})
        with self.db.edit() as state:
            state['mind'] = copy.deepcopy(mind)
            state['technology'] = copy.deepcopy(technology)
            state['snapshot']['ships'].clear()
        self.assertEqual(self.db.load(), dict(snapshot=first, mind=mind, technology=technology))
        self.assert_counts(mind_overrides=2, mind_removed=2, mind_extra=1,
                           technology_manual=1, technology_nations=2, scan_batches=1)
        self.db.save_scan(snapshot('second'), 'first')
        self.assertEqual(self.db.load()['mind'], mind)
        self.assertEqual(self.db.load()['technology'], technology)
        with self.db.edit() as state:
            state['mind'] = dict(overrides={}, removed=[], extra=[])
            state['technology'] = dict(manual={}, nations={})
        self.assertEqual(self.db.load()['mind'], dict(overrides={}, removed=[], extra=[]))
        self.assert_counts(mind_overrides=0, mind_removed=0, mind_extra=0,
                           technology_manual=0, technology_nations=0)

    def test_technology_edits_cannot_write_automatic_evidence(self):
        with self.db.edit() as state:
            state['technology'] = dict(manual={}, observed={'10102': 125}, current={}, absent=[])
        self.assertEqual(self.db.load()['technology'], dict(manual={}))
        self.assertIsNone(self.db.load()['snapshot'])

    def test_read_only_load_does_not_change_any_database_file(self):
        self.db.save_scan(snapshot(), revision({}))
        before = {path.name: (path.read_bytes(), path.stat().st_mtime_ns)
                  for path in self.db.path.parent.iterdir()}
        for _ in range(3):
            self.db.load()
            self.assertFalse(self.db.load(include_import_status=True)['legacy_imported'])
        after = {path.name: (path.read_bytes(), path.stat().st_mtime_ns)
                 for path in self.db.path.parent.iterdir()}
        self.assertEqual(before, after)

    def test_explicit_import_marks_once_and_repeat_writes_nothing(self):
        self.db.save_scan(snapshot(), revision({}))
        self.assertFalse(self.db.load(include_import_status=True)['legacy_imported'])
        options = {'manual': {'10102': {'owned': False}}, 'nations': {'1': 3}}
        with self.db.edit(import_legacy=True) as state:
            state['technology'] = options
            state['mind'] = {'counter': 1}
        expected = dict(snapshot=snapshot(), mind={'counter': 1}, technology=options)
        self.assertEqual(self.db.load(), expected)
        self.assertEqual(self.db.load(include_import_status=True), dict(expected, legacy_imported=True))
        before = (self.db.path.read_bytes(), self.db.path.stat().st_mtime_ns)
        with self.assertRaises(FleetDatabaseAlreadyImported):
            with self.db.edit(import_legacy=True):
                self.fail('a repeated import must not yield editable state')
        self.assertEqual((self.db.path.read_bytes(), self.db.path.stat().st_mtime_ns), before)
        self.assertEqual(self.db.load(), expected)
        self.assertTrue(self.db.load(include_import_status=True)['legacy_imported'])
        with closing(sqlite3.connect(self.db.path)) as connection:
            name, imported_at = connection.execute('SELECT name, imported_at FROM imports').fetchone()
            self.assertEqual(name, 'legacy_fleet')
            self.assertRegex(imported_at, r'^\d{4}-\d\d-\d\dT.*\+00:00$')
        # Ordinary analysis edits neither remove nor reapply the import marker.
        with self.db.edit() as state:
            state['technology']['nations']['1'] = 2
        self.assertTrue(self.db.load(include_import_status=True)['legacy_imported'])
        other = FleetDatabase(self.config.with_name('other.json'))
        self.assertFalse(other.load(include_import_status=True)['legacy_imported'])
        with other.edit(import_legacy=True):
            pass
        self.assertTrue(other.load(include_import_status=True)['legacy_imported'])

    def test_explicit_import_archives_sources_and_preserves_supplied_active_snapshot(self):
        active = snapshot('active')
        self.db.save_scan(active, revision({}))
        old = snapshot('legacy')
        old['ships'][0]['level'] = 120
        old['history']['observed']['10105'] = 120
        sources = {
            'DockScan.Dock.Result': copy.deepcopy(old),
            'MindCalculatorScan.MindCalculator.Result': {'ships': [{'name': 'manual', 'level': 100}]},
            'MindCalculatorScan.MindCalculator.Analysis': {'removed': ['review:original']},
            'MindCalculatorScan.MindCalculator.FleetTechRecord': {'observed': {'10105': 120}},
            'FleetTechScan.FleetTech.Record': {'manual': {'10105': {'owned': False}}},
        }
        preserved = copy.deepcopy(active)
        preserved['history']['observed']['10105'] = 120
        preserved['revision'] = 'active-with-legacy-history'
        with self.db.edit(import_legacy=True) as state:
            self.assertEqual(state['snapshot'], active)
            # The converter chooses order and supplies the active copy last.
            state['legacy_snapshots'] = [old, preserved]
            state['legacy_sources'] = sources
            state['mind'] = {'extra': [{'name': 'manual', 'level': 100}]}
            state['technology'] = {'manual': {'10105': {'owned': False}}, 'nations': {'1': 2}}
        result = self.db.load(include_import_status=True)
        self.assertEqual(result['snapshot'], preserved)
        self.assertEqual(result['snapshot']['ships'], active['ships'])
        self.assertEqual(result['snapshot']['history']['current'], active['history']['current'])
        self.assertEqual(result['mind'], {'extra': [{'name': 'manual', 'level': 100}]})
        self.assertEqual(result['technology'], {'manual': {'10105': {'owned': False}}, 'nations': {'1': 2}})
        self.assertTrue(result['legacy_imported'])
        self.assertNotIn('legacy_sources', result)
        self.assertNotIn('legacy_snapshots', result)
        self.assert_counts(scan_batches=3, scan_cards=9, legacy_sections=5, imports=1)
        with closing(sqlite3.connect(self.db.path)) as connection:
            self.assertEqual({name: json.loads(payload) for name, payload in connection.execute(
                'SELECT name, payload FROM legacy_sections')}, sources)
        with self.assertRaises(FleetDatabaseConflict):
            self.db.save_scan(snapshot('stale'), active['revision'])
        self.db.save_scan(snapshot('new-scan'), preserved['revision'])
        self.assertEqual(self.db.load()['snapshot'], snapshot('new-scan'))
        self.assert_counts(scan_batches=4, legacy_sections=5, imports=1)

    def test_explicit_import_rolls_back_batches_sources_settings_and_marker_together(self):
        self.db.save_scan(snapshot('active'), revision({}))
        with self.db.edit() as state:
            state['mind'] = {'counter': 1}
        committed = self.db.load()
        for failure in ('snapshot', 'archive'):
            with self.subTest(failure=failure):
                snapshots = [snapshot('old-first'), snapshot('old-second')]
                sources = {'FleetTechScan.FleetTech.Record': {'manual': {'10102': {'owned': True}}}}
                if failure == 'snapshot':
                    snapshots[1]['ships'][1]['invalid'] = object()
                else:
                    sources['MindCalculatorScan.MindCalculator.Analysis'] = object()
                with self.assertRaises(TypeError):
                    with self.db.edit(import_legacy=True) as state:
                        state['mind'] = {'counter': 2}
                        state['technology'] = {'manual': {'10102': {'owned': True}}}
                        state['legacy_snapshots'] = snapshots
                        state['legacy_sources'] = sources
                self.assertEqual(self.db.load(include_import_status=True),
                                 dict(committed, legacy_imported=False))
                self.assert_counts(scan_batches=1, scan_cards=3, legacy_sections=0,
                                   imports=0, technology_manual=0)
        with self.db.edit(import_legacy=True) as state:
            state['legacy_sources'] = {'FleetTechScan.FleetTech.Record': {}}
        self.assertTrue(self.db.load(include_import_status=True)['legacy_imported'])

    def test_ordinary_edits_cannot_append_import_snapshots_or_archive_sources(self):
        with self.db.edit() as state:
            state['legacy_snapshots'] = [snapshot('ignored')]
            state['legacy_sources'] = {'FleetTechScan.FleetTech.Record': {'manual': {}}}
        self.assertIsNone(self.db.load()['snapshot'])
        self.assertFalse(self.db.load(include_import_status=True)['legacy_imported'])
        self.assert_counts(scan_batches=0, legacy_sections=0, imports=0)

    def test_failed_explicit_import_rolls_back_data_and_allows_retry(self):
        self.db.save_scan(snapshot(), revision({}))
        committed = self.db.load()
        with self.assertRaisesRegex(ValueError, 'invalid input'):
            with self.db.edit(import_legacy=True) as state:
                state['technology'] = {'nations': {'1': 3}}
                raise ValueError('invalid input')
        self.assertEqual(self.db.load(), committed)
        self.assertFalse(self.db.load(include_import_status=True)['legacy_imported'])
        # A failure after writing mind rows must roll those back with the marker.
        with self.assertRaises(TypeError):
            with self.db.edit(import_legacy=True) as state:
                state['mind'] = {'counter': 1}
                state['technology'] = {'manual': {'10102': {'not_json': object()}}}
        self.assertEqual(self.db.load(), committed)
        self.assert_counts(imports=0)
        with self.db.edit(import_legacy=True) as state:
            state['technology'] = {'nations': {'1': 3}}
        self.assertEqual(self.db.load()['technology'], {'nations': {'1': 3}})
        self.assertTrue(self.db.load(include_import_status=True)['legacy_imported'])
        self.assert_counts(imports=1)

    def test_edit_exception_and_partial_write_failure_roll_back(self):
        self.db.save_scan(snapshot(), revision({}))
        with self.db.edit() as state:
            state['mind'] = dict(counter=1)
        before = self.db.load()
        with self.assertRaisesRegex(RuntimeError, 'cancelled'):
            with self.db.edit() as state:
                state['mind']['counter'] = 2
                raise RuntimeError('cancelled')
        self.assertEqual(self.db.load(), before)
        original = self.db._write_options

        def fail_second_write(connection, scope, options):
            original(connection, scope, options)
            if scope == 'technology':
                raise RuntimeError('storage failure')

        with patch.object(self.db, '_write_options', side_effect=fail_second_write):
            with self.assertRaisesRegex(RuntimeError, 'storage failure'):
                with self.db.edit() as state:
                    state['mind']['counter'] = 3
        self.assertEqual(self.db.load(), before)
        with self.db.edit() as state:
            state['mind']['counter'] += 1
        self.assertEqual(self.db.load()['mind']['counter'], 2)

    def test_first_edit_rollback_leaves_readable_empty_database(self):
        with self.assertRaisesRegex(RuntimeError, 'cancelled'):
            with self.db.edit() as state:
                state['mind']['counter'] = 1
                raise RuntimeError('cancelled')
        self.assertEqual(self.db.load(), dict(snapshot=None, mind={}, technology={}))
        self.assert_counts(scan_batches=0, analysis_fields=0)

    def test_first_database_is_published_with_complete_schema(self):
        original = os.link
        observed = []

        def publish(source, destination, **kwargs):
            observed.append(self.db.load())
            original(source, destination, **kwargs)
            observed.append(self.db.load())

        with patch('module.runtime.fleet_database.os.link', side_effect=publish):
            self.db.save_scan(snapshot(), revision({}))
        self.assertEqual(observed, [dict(snapshot=None, mind={}, technology={})] * 2)
        self.assertEqual(self.db.load()['snapshot'], snapshot())
        self.assertEqual([path.name for path in self.db.path.parent.iterdir()], ['pilot.sqlite3'])

    def test_initial_schema_failure_never_publishes_incomplete_database(self):
        original = self.db._schema

        def fail_schema(connection, **kwargs):
            original(connection, **kwargs)
            raise RuntimeError('initialization failed')

        with patch.object(self.db, '_schema', side_effect=fail_schema):
            with self.assertRaisesRegex(RuntimeError, 'initialization failed'):
                with self.db.edit():
                    pass
        self.assertFalse(self.db.path.exists())
        self.assertEqual(list(self.db.path.parent.iterdir()), [])
        self.assertEqual(self.db.load(), dict(snapshot=None, mind={}, technology={}))

    def test_scan_conflict_and_partial_card_failure_roll_back(self):
        first = snapshot()
        self.db.save_scan(first, revision({}))
        with self.assertRaises(FleetDatabaseConflict):
            self.db.save_scan(snapshot('stale'), revision({}))
        broken = snapshot('broken')
        broken['ships'][1]['not_json'] = object()
        with self.assertRaises(TypeError):
            self.db.save_scan(broken, first['revision'])
        self.assertEqual(self.db.load()['snapshot'], first)
        self.assert_counts(scan_batches=1, scan_cards=3, scan_evidence=5)
        self.db.save_scan(snapshot('second'), first['revision'])
        self.assert_counts(scan_batches=2)

    def test_read_recovers_hot_journal_after_crashed_editor(self):
        self.db.save_scan(snapshot(), revision({}))
        with self.db.edit() as state:
            state['mind'] = {'counter': 1}
            state['technology'] = {'manual': {'10102': {'owned': True}}, 'nations': {'1': 2}}
        committed = self.db.load()
        context = multiprocessing.get_context('spawn')
        process = context.Process(target=crash_editor_worker, args=(str(self.config),))
        process.start()
        self.addCleanup(lambda: process.terminate() if process.is_alive() else None)
        process.join(timeout=15)
        self.assertFalse(process.is_alive(), 'crashing editor deadlocked')
        self.assertEqual(process.exitcode, 23)
        journal = Path(str(self.db.path) + '-journal')
        self.assertTrue(journal.is_file())
        self.assertEqual(journal.read_bytes()[:8], bytes.fromhex('d9d505f920a163d7'))
        self.assertEqual(self.db.load(include_import_status=True), dict(committed, legacy_imported=False))
        self.assertFalse(journal.exists())
        self.assert_counts(scan_batches=1, scan_cards=3, mind_overrides=0,
                           technology_manual=1, technology_nations=1)
        self.assertEqual(self.db.load(), committed)

    def test_read_recovery_never_recreates_deleted_storage(self):
        error = sqlite3.OperationalError('attempt to write a readonly database')
        error.sqlite_errorcode = sqlite3.SQLITE_READONLY_ROLLBACK
        empty = dict(snapshot=None, mind={}, technology={})
        with patch.object(self.db, '_read_only', side_effect=[error, empty]):
            self.assertEqual(self.db.load(), empty)
        self.assertFalse(self.db.path.parent.exists())

    def test_instances_have_separate_databases(self):
        other = FleetDatabase(self.config.with_name('other.json'))
        self.db.save_scan(snapshot(), revision({}))
        self.assertIsNone(other.load()['snapshot'])
        with other.edit() as state:
            state['technology']['nations'] = {'1': 3}
        self.assertEqual(self.db.load()['technology'], {})
        self.assertIsNone(other.load()['snapshot'])

    def test_corrupt_and_unsupported_databases_are_not_reset(self):
        self.db.path.parent.mkdir()
        self.db.path.write_bytes(b'this is not sqlite')
        before = self.db.path.read_bytes()
        with self.assertRaises(sqlite3.DatabaseError):
            self.db.load()
        with self.assertRaises(sqlite3.DatabaseError):
            with self.db.edit():
                pass
        self.assertEqual(self.db.path.read_bytes(), before)
        self.db.path.unlink()
        with closing(sqlite3.connect(self.db.path)) as connection, connection:
            connection.execute('PRAGMA user_version=999')
        before = self.db.path.read_bytes()
        with self.assertRaisesRegex(ValueError, '999'):
            self.db.load()
        with self.assertRaisesRegex(ValueError, '999'):
            self.db.save_scan(snapshot(), revision({}))
        self.assertEqual(self.db.path.read_bytes(), before)

    def test_unversioned_existing_tables_are_not_overwritten(self):
        self.db.path.parent.mkdir()
        with closing(sqlite3.connect(self.db.path)) as connection, connection:
            connection.execute('CREATE TABLE existing (value TEXT)')
            connection.execute("INSERT INTO existing VALUES ('keep')")
        with self.assertRaisesRegex(ValueError, 'Unsupported.*0'):
            with self.db.edit():
                pass
        with closing(sqlite3.connect(self.db.path)) as connection, connection:
            self.assertEqual(connection.execute('SELECT value FROM existing').fetchone()[0], 'keep')

    def test_corrupt_payload_errors_propagate(self):
        self.db.save_scan(snapshot(), revision({}))
        with closing(sqlite3.connect(self.db.path)) as connection, connection:
            connection.execute("UPDATE scan_cards SET payload='invalid' WHERE position=0")
        with self.assertRaises(ValueError):
            self.db.load()

    def test_refuses_directory_database_and_sidecar_symlinks(self):
        external = Path(self.temp.name) / 'external'
        external.mkdir()
        self.db.path.parent.symlink_to(external, target_is_directory=True)
        for action in (self.db.load, lambda: self.db.save_scan(snapshot(), revision({}))):
            with self.assertRaisesRegex(ValueError, 'Unsafe'):
                action()
        self.assertEqual(list(external.iterdir()), [])
        self.db.path.parent.unlink()
        self.db.path.parent.mkdir()
        target = external / 'target.sqlite3'
        target.write_bytes(b'preserve')
        self.db.path.symlink_to(target)
        with self.assertRaisesRegex(ValueError, 'Unsafe'):
            self.db.load()
        with self.assertRaisesRegex(ValueError, 'Unsafe'):
            with self.db.edit():
                pass
        self.db.path.unlink()
        self.db.save_scan(snapshot(), revision({}))
        for suffix in ('-journal', '-wal', '-shm'):
            sidecar = Path(str(self.db.path) + suffix)
            sidecar.symlink_to(target)
            with self.assertRaisesRegex(ValueError, 'Unsafe'):
                self.db.load()
            with self.assertRaisesRegex(ValueError, 'Unsafe'):
                self.db.save_scan(snapshot('second'), 'first')
            sidecar.unlink()
        self.assertEqual(target.read_bytes(), b'preserve')

    def test_refuses_windows_directory_junctions(self):
        self.db.path.parent.mkdir()
        # On Windows a junction's lstat mode is a directory. Simulate that
        # distinction so the rejection remains covered on POSIX CI too.
        with patch.object(Path, 'is_junction', return_value=True):
            with self.assertRaisesRegex(ValueError, 'Unsafe'):
                self.db.load()
            with self.assertRaisesRegex(ValueError, 'Unsafe'):
                self.db.save_scan(snapshot(), revision({}))
            with self.assertRaisesRegex(ValueError, 'Unsafe'):
                with self.db.edit():
                    pass
        self.assertEqual(list(self.db.path.parent.iterdir()), [])

    def run_processes(self, target, arguments):
        context = multiprocessing.get_context('spawn')
        barrier = context.Barrier(len(arguments))
        processes = [context.Process(target=target, args=(str(self.config), barrier, *args))
                     for args in arguments]
        for process in processes:
            process.start()
            self.addCleanup(lambda child=process: child.terminate() if child.is_alive() else None)
        for process in processes:
            process.join(timeout=30)
            self.assertFalse(process.is_alive(), 'database writer deadlocked')
            self.assertEqual(process.exitcode, 0)

    def test_cross_process_editors_serialize_without_lost_updates(self):
        self.run_processes(increment_worker, [(15,), (15,), (15,)])
        self.assertEqual(self.db.load()['mind']['counter'], 45)

    def test_cross_process_scans_accept_only_one_expected_revision(self):
        context = multiprocessing.get_context('spawn')
        queue = context.Queue()
        self.addCleanup(queue.close)
        self.run_processes(scan_worker, [('one', queue), ('two', queue)])
        self.assertEqual(sorted([queue.get(timeout=5), queue.get(timeout=5)]), ['conflict', 'saved'])
        self.assert_counts(scan_batches=1, scan_cards=3)

    def test_cross_process_explicit_import_succeeds_only_once(self):
        context = multiprocessing.get_context('spawn')
        queue = context.Queue()
        self.addCleanup(queue.close)
        self.run_processes(import_worker, [('10102', queue), ('10103', queue)])
        self.assertEqual(sorted([queue.get(timeout=5), queue.get(timeout=5)]),
                         ['already_imported', 'imported'])
        self.assertTrue(self.db.load(include_import_status=True)['legacy_imported'])
        self.assertIn(self.db.load()['technology']['manual'],
                      ({'10102': {'owned': True}}, {'10103': {'owned': True}}))
        self.assert_counts(imports=1, technology_manual=1, scan_batches=1, scan_cards=3, legacy_sections=1)
        self.assertEqual(list(self.db.load()['technology']['manual']), [self.db.load()['snapshot']['revision']])


if __name__ == '__main__':
    unittest.main()
