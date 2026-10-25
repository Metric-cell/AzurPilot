"""Typed fleet ledger persistence, independent of device recognition and JSON config."""
import copy
from contextlib import closing
import multiprocessing
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from module.runtime.fleet_database import FleetDatabase, FleetDatabaseAlreadyImported, FleetDatabaseConflict
from module.runtime.fleet_tech import revision


def snapshot(tag='first'):
    return dict(version=1, revision=tag, ships=[
        dict(group_type=10102, level=125, name='杜威', rarity='R', base_rarity='R'),
        dict(group_type=10102, level=100, name='杜威', rarity='R', base_rarity='R'),
        dict(level=0, name='未识别舰船', review=True, source='unknown')],
        history=dict(observed={'10102': 125, '10103': 110},
                     current={'10102': 125}, absent=['10103']),
        updated_at=tag, min_level=1, max_level=125, expected_count=None,
        physical_count=3, complete=False, source='scan')


def increment_worker(config_path, barrier, count):
    database = FleetDatabase(Path(config_path))
    barrier.wait(timeout=15)
    for _ in range(count):
        with database.edit() as state:
            rows = state['mind'].setdefault('extra', [{'name': 'manual', 'level': 1}])
            rows[0]['level'] += 1


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
            state['legacy_snapshot'] = snapshot(key)
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
        with closing(sqlite3.connect(self.db.path)) as connection:
            for table, expected in counts.items():
                self.assertEqual(connection.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0],
                                 expected, table)

    def test_schema_is_five_typed_ledger_tables(self):
        self.db.save_scan(snapshot(), revision({}))
        with closing(sqlite3.connect(self.db.path)) as connection:
            tables = {row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
            self.assertEqual(tables, {'ships', 'fleet_info', 'technology', 'nation_technology', 'mind_settings'})
            columns = {row[1]: row for row in connection.execute('PRAGMA table_info(ships)')}
            self.assertEqual(set(columns), {'id', 'group_type', 'name', 'level', 'rarity', 'base_rarity',
                                            'extra_exp', 'review', 'source', 'position'})
            self.assertEqual(columns['id'][2], 'INTEGER')
            self.assertEqual(columns['id'][5], 1)
            self.assertEqual(columns['extra_exp'][2], 'INTEGER')
            self.assertEqual(columns['extra_exp'][3], 0)
            sql = connection.execute("SELECT sql FROM sqlite_master WHERE name='ships'").fetchone()[0]
            self.assertIn('AUTOINCREMENT', sql.upper())
            for table in tables:
                for column in connection.execute(f'PRAGMA table_info({table})'):
                    self.assertNotIn(column[1], {'payload', 'metadata', 'json'})
                    self.assertNotIn('json', column[1].lower())
            self.assertEqual(connection.execute('PRAGMA user_version').fetchone()[0], 2)

    def test_duplicate_physical_rows_have_distinct_ids_and_nullable_experience(self):
        first = snapshot()
        first['ships'][0]['extra_exp'] = 12345
        first['ships'][1]['extra_exp'] = 0
        self.db.save_scan(first, revision({}))
        stored = self.db.load()['snapshot']
        self.assertEqual(stored['revision'], 'first')
        rows = stored['ships']
        self.assertEqual([row['name'] for row in rows], ['杜威', '杜威', '未识别舰船'])
        self.assertEqual([row['level'] for row in rows], [125, 100, 0])
        self.assertEqual(len({row['id'] for row in rows}), 3)
        self.assertTrue(all(type(row['id']) is int and row['id'] > 0 for row in rows))
        self.assertEqual([row['extra_exp'] for row in rows], [12345, 0, None])
        with closing(sqlite3.connect(self.db.path)) as connection:
            self.assertEqual(connection.execute('SELECT extra_exp FROM ships ORDER BY position').fetchall(),
                             [(12345,), (0,), (None,)])
        self.assert_counts(ships=3, fleet_info=1)

    def test_scan_stores_only_ledger_fields_and_latest_typed_metadata(self):
        first = snapshot()
        first['raw_evidence'] = object()
        first['ships'][0]['recognition_debug'] = object()
        first['ships'][0]['slot'] = {'page': 1, 'row': 2}
        self.db.save_scan(first, revision({}))
        stored = self.db.load()['snapshot']
        self.assertNotIn('raw_evidence', stored)
        self.assertNotIn('recognition_debug', stored['ships'][0])
        self.assertNotIn('slot', stored['ships'][0])
        self.assertEqual(stored['min_level'], 1)
        self.assertEqual(stored['max_level'], 125)
        self.assertEqual(stored['physical_count'], 3)
        self.assertEqual(stored['complete'], False)
        self.assertEqual(stored['source'], 'scan')
        partial = snapshot('partial')
        partial.update(ships=[dict(group_type=10102, name='杜威', level=100)], min_level=100,
                       max_level=110, complete=False, physical_count=1)
        self.db.save_scan(partial, 'first')
        stored = self.db.load()['snapshot']
        self.assertEqual(stored['revision'], 'partial')
        self.assertEqual(len(stored['ships']), 1)
        self.assertEqual(stored['history']['absent'], [])
        self.assertEqual(stored['history']['observed']['10102'], 125)
        self.assert_counts(ships=1, fleet_info=1)

    def test_replacement_uses_fresh_ids_and_never_guesses_duplicate_experience(self):
        first = snapshot()
        first['ships'] = [dict(group_type=10102, name='杜威', level=100, extra_exp=100),
                          dict(group_type=10102, name='杜威', level=100, extra_exp=900)]
        self.db.save_scan(first, revision({}))
        first_ids = {row['id'] for row in self.db.load()['snapshot']['ships']}
        second = snapshot('second')
        second['ships'] = [dict(group_type=10102, name='杜威', level=100),
                           dict(group_type=10102, name='杜威', level=101)]
        self.db.save_scan(second, 'first')
        rows = self.db.load()['snapshot']['ships']
        self.assertTrue(all(row['id'] > max(first_ids) for row in rows))
        self.assertEqual([row['extra_exp'] for row in rows], [None, None])
        self.assert_counts(ships=2, fleet_info=1)
        empty = dict(second, revision='empty', ships=[], physical_count=0, complete=True)
        self.db.save_scan(empty, 'second')
        self.assertEqual(self.db.load()['snapshot']['ships'], [])
        self.assert_counts(ships=0, fleet_info=1)
        self.db.save_scan(snapshot('third'), 'empty')
        self.assertTrue(all(row['id'] > max(first_ids) + 2
                            for row in self.db.load()['snapshot']['ships']))

    def test_technology_keeps_maximum_observed_level_when_latest_list_is_empty(self):
        self.db.save_scan(snapshot(), revision({}))
        first = self.db.load()['snapshot']
        self.assertEqual(first['history']['observed']['10102'], 125)
        self.assertEqual(first['history']['observed']['10103'], 110)
        lower = snapshot('lower')
        lower['ships'] = [dict(name='杜威', group_type=10102, level=90),
                          dict(name='unconfirmed', group_type=10104, level=125, review=True)]
        lower['history'] = {'observed': {'10102': 90}, 'current': {'10102': 125, '10104': 125}}
        self.db.save_scan(lower, 'first')
        history = self.db.load()['snapshot']['history']
        self.assertEqual(history['observed']['10102'], 125)
        self.assertEqual(history['current'], {'10102': 90})
        self.assertNotIn('10104', history['observed'])
        empty = dict(lower, revision='empty', ships=[], history={}, complete=True, physical_count=0,
                     reached_bottom=True, filters_clear=True, expected_count=0)
        self.db.save_scan(empty, 'lower')
        history = self.db.load()['snapshot']['history']
        self.assertEqual(history['observed'], {'10102': 125, '10103': 110})
        self.assertEqual(history['current'], {})
        self.assertIn('10102', history['absent'])
        with closing(sqlite3.connect(self.db.path)) as connection:
            observed = dict(connection.execute('SELECT group_type, max_observed_level FROM technology'))
            self.assertEqual(observed['10102'], 125)
            self.assertEqual(observed['10103'], 110)
        self.assertEqual(self.db.load()['technology'], {})

    def test_analysis_settings_are_typed_and_independent_of_automatic_scan_data(self):
        self.db.save_scan(snapshot(), revision({}))
        before = self.db.load()['snapshot']
        mind = dict(overrides={'group:10102': {'excluded': True}, 'review:a': {'level': 90}},
                    removed=['group:10103', 'review:b'],
                    extra=[{'name': 'manual', 'level': 100, 'rarity': 'SR', 'base_rarity': 'R',
                            'excluded': False, 'review': True, 'source': 'manual entry'}],
                    snapshot_revision='first', updated_at='now')
        technology = dict(manual={'10102': {'owned': False, 'level': 100, 'full_star': False,
                                           'lv120': False, 'level_toggle': {'low': 100, 'high': 120}}},
                          nations={'1': 2, '3': 0})
        with self.db.edit() as state:
            state['mind'] = copy.deepcopy(mind)
            state['technology'] = copy.deepcopy(technology)
            state['technology']['observed'] = {'10102': 1}
            state['snapshot']['ships'].clear()
        loaded = self.db.load()
        self.assertEqual(loaded['snapshot'], before)
        self.assertEqual(loaded['mind'], mind)
        self.assertEqual(loaded['technology'], technology)
        self.assert_counts(ships=3, nation_technology=2)
        self.db.save_scan(snapshot('second'), 'first')
        self.assertEqual(self.db.load()['mind'], mind)
        self.assertEqual(self.db.load()['technology'], technology)
        with self.db.edit() as state:
            state['mind'] = dict(overrides={}, removed=[], extra=[])
            state['technology'] = dict(manual={}, nations={})
        loaded = self.db.load()
        self.assertEqual(loaded['mind'], dict(overrides={}, removed=[], extra=[]))
        self.assertEqual(loaded['technology'], dict(manual={}, nations={}))
        self.assertEqual(loaded['snapshot']['history']['observed']['10102'], 125)
        self.assert_counts(nation_technology=0, mind_settings=0)

    def test_scan_revision_requires_a_nonempty_string_and_preserves_cas(self):
        self.db.save_scan(snapshot(), revision({}))
        committed = self.db.load()
        for invalid in (None, 123, True, ''):
            with self.subTest(revision=invalid):
                broken = snapshot('broken')
                broken['revision'] = invalid
                with self.assertRaises(ValueError):
                    self.db.save_scan(broken, 'first')
                self.assertEqual(self.db.load(), committed)
        self.db.save_scan(snapshot('explicit-source-revision'), 'first')
        self.assertEqual(self.db.load()['snapshot']['revision'], 'explicit-source-revision')
        with self.assertRaises(FleetDatabaseConflict):
            self.db.save_scan(snapshot('stale'), 'first')

    def test_scan_rejects_counts_and_proof_flags_that_change_meaning_after_sqlite_coercion(self):
        self.db.save_scan(snapshot(), revision({}))
        committed = self.db.load()
        invalid = {
            'physical_count': (None, -1, True, 1.0, '1'),
            'expected_count': (-1, True, 1.0, '1'),
            'reached_bottom': (1, 'true'),
            'filters_clear': (1, 'true'),
        }
        for field, values in invalid.items():
            for value in values:
                with self.subTest(field=field, value=value):
                    scan = snapshot('invalid-proof')
                    scan.update(ships=[dict(group_type=10102, name='杜威', level=100)],
                                reached_bottom=True, filters_clear=True, physical_count=1,
                                expected_count=1)
                    scan[field] = value
                    with self.assertRaises(ValueError):
                        self.db.save_scan(scan, 'first')
                    self.assertEqual(self.db.load(), committed)
        valid = snapshot('complete')
        valid.update(ships=[dict(group_type=10102, name='杜威', level=100)],
                     reached_bottom=True, filters_clear=True, physical_count=1, expected_count=1)
        self.db.save_scan(valid, 'first')
        stored = self.db.load()['snapshot']
        self.assertTrue(stored['complete'])
        self.assertTrue(stored['history']['absent'])
        self.assertIs(type(stored['physical_count']), int)
        self.assertIs(type(stored['expected_count']), int)

    def test_empty_manual_level_toggle_roundtrips_and_does_not_block_later_edits(self):
        technology = {'manual': {'10102': {'level_toggle': {}}}}
        with self.db.edit() as state:
            state['technology'] = copy.deepcopy(technology)
        self.assertEqual(self.db.load()['technology'], technology)
        with self.db.edit() as state:
            state['mind'] = {'extra': [{'name': 'manual', 'level': 100}]}
        self.assertEqual(self.db.load()['technology'], technology)
        self.db.save_scan(snapshot(), revision({}))
        self.assertEqual(self.db.load()['technology'], technology)
        committed = self.db.load()
        for pair in ({'low': 100}, {'high': 120}, {'low': None, 'high': None}):
            with self.subTest(pair=pair):
                with self.assertRaises(ValueError):
                    with self.db.edit() as state:
                        state['technology']['manual']['10102']['level_toggle'] = pair
                self.assertEqual(self.db.load(), committed)

    def test_manual_nullable_values_remain_distinct_from_unspecified_fields(self):
        technology = {'manual': {'10102': {'owned': None, 'full_star': None,
                                           'lv120': None, 'level': None},
                                 '10103': {'owned': False}}, 'nations': {'1': 0}}
        with self.db.edit() as state:
            state['technology'] = copy.deepcopy(technology)
        self.assertEqual(self.db.load()['technology'], technology)
        self.db.save_scan(snapshot(), revision({}))
        self.assertEqual(self.db.load()['technology'], technology)
        with self.db.edit() as state:
            state['technology']['manual']['10102'] = {'owned': True}
        self.assertEqual(self.db.load()['technology']['manual']['10102'], {'owned': True})
        self.assertEqual(self.db.load()['snapshot']['history']['observed']['10102'], 125)

    def test_ordinary_edits_ignore_unknown_fields_and_import_only_keys(self):
        with self.db.edit() as state:
            state['mind'] = {'counter': 9}
            state['technology'] = dict(observed={'10102': 125}, current={}, absent=[])
            state['legacy_snapshot'] = snapshot('ignored')
            state['legacy_snapshots'] = [snapshot('also-ignored')]
            state['legacy_sources'] = {'raw': {'keep': 'nothing'}}
        self.assertEqual(self.db.load(include_import_status=True),
                         dict(snapshot=None, mind={}, technology={}, legacy_imported=False))
        self.assert_counts(ships=0, technology=0, mind_settings=0)

    def test_import_preserves_current_rows_and_marks_once_without_archives(self):
        self.db.save_scan(snapshot('active'), revision({}))
        active = self.db.load()['snapshot']
        with self.assertRaises(ValueError):
            with self.db.edit(import_legacy=True) as state:
                state['legacy_snapshot'] = snapshot('replacement')
        self.assertEqual(self.db.load()['snapshot'], active)
        self.assertFalse(self.db.load(include_import_status=True)['legacy_imported'])
        imported = copy.deepcopy(active)
        imported['revision'] = 'imported'
        imported['history']['observed']['10105'] = 120
        options = {'manual': {'10102': {'owned': False}}, 'nations': {'1': 3}}
        with self.db.edit(import_legacy=True) as state:
            state['technology'] = options
            state['mind'] = {'extra': [{'name': 'manual', 'level': 100}]}
            state['legacy_snapshot'] = imported
            state['legacy_snapshots'] = [snapshot('obsolete-list-ignored')]
            state['legacy_sources'] = {'raw': object()}
        expected = self.db.load()
        self.assertEqual(expected['snapshot']['revision'], 'imported')
        self.assertEqual(expected['snapshot']['ships'], active['ships'])
        self.assertEqual(expected['snapshot']['history']['observed']['10105'], 120)
        self.assertEqual(expected['technology'], options)
        self.assertTrue(self.db.load(include_import_status=True)['legacy_imported'])
        self.assert_counts(ships=3, fleet_info=1)
        with closing(sqlite3.connect(self.db.path)) as connection:
            imported_at = connection.execute('SELECT imported_at FROM fleet_info').fetchone()[0]
            self.assertRegex(imported_at, r'^\d{4}-\d\d-\d\dT.*\+00:00$')
        before = (self.db.path.read_bytes(), self.db.path.stat().st_mtime_ns)
        with self.assertRaises(FleetDatabaseAlreadyImported):
            with self.db.edit(import_legacy=True):
                self.fail('a repeated import must not yield editable state')
        self.assertEqual((self.db.path.read_bytes(), self.db.path.stat().st_mtime_ns), before)
        self.assertEqual(self.db.load(), expected)
        with self.db.edit() as state:
            state['technology']['nations']['1'] = 2
        self.assertTrue(self.db.load(include_import_status=True)['legacy_imported'])
        with self.assertRaises(FleetDatabaseConflict):
            self.db.save_scan(snapshot('stale'), 'active')
        self.db.save_scan(snapshot('new-scan'), 'imported')
        self.assertEqual(self.db.load()['snapshot']['revision'], 'new-scan')
        self.assert_counts(ships=3, fleet_info=1)

    def test_empty_import_and_failed_import_retry(self):
        with self.assertRaisesRegex(ValueError, 'cancelled'):
            with self.db.edit(import_legacy=True) as state:
                state['mind'] = {'extra': [{'name': 'manual', 'level': 100}]}
                raise ValueError('cancelled')
        self.assertEqual(self.db.load(include_import_status=True),
                         dict(snapshot=None, mind={}, technology={}, legacy_imported=False))
        with self.db.edit(import_legacy=True):
            pass
        self.assertTrue(self.db.load(include_import_status=True)['legacy_imported'])
        self.assertIsNone(self.db.load()['snapshot'])

    def test_import_snapshot_failure_rolls_back_settings_snapshot_and_marker(self):
        self.db.save_scan(snapshot('active'), revision({}))
        committed = self.db.load()
        broken = copy.deepcopy(committed['snapshot'])
        broken['revision'] = 'broken'
        broken['history']['observed']['10105'] = object()
        with self.assertRaises((TypeError, ValueError, sqlite3.Error)):
            with self.db.edit(import_legacy=True) as state:
                state['mind'] = {'extra': [{'name': 'manual', 'level': 100}]}
                state['technology'] = {'manual': {'10102': {'owned': True}}}
                state['legacy_snapshot'] = broken
        self.assertEqual(self.db.load(include_import_status=True), dict(committed, legacy_imported=False))
        self.assert_counts(ships=3)
        with self.db.edit(import_legacy=True) as state:
            imported = copy.deepcopy(committed['snapshot'])
            imported['revision'] = 'imported'
            state['legacy_snapshot'] = imported
        self.assertEqual(self.db.load()['snapshot']['revision'], 'imported')

    def test_edit_exception_and_partial_write_failure_roll_back(self):
        self.db.save_scan(snapshot(), revision({}))
        with self.db.edit() as state:
            state['mind'] = {'extra': [{'name': 'manual', 'level': 100}]}
        before = self.db.load()
        with self.assertRaisesRegex(RuntimeError, 'cancelled'):
            with self.db.edit() as state:
                state['mind']['extra'][0]['level'] = 101
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
                    state['mind']['extra'][0]['level'] = 102
        self.assertEqual(self.db.load(), before)
        with self.db.edit() as state:
            state['mind']['extra'][0]['level'] += 1
        self.assertEqual(self.db.load()['mind']['extra'][0]['level'], 101)

    def test_first_edit_rollback_leaves_readable_empty_database(self):
        with self.assertRaisesRegex(RuntimeError, 'cancelled'):
            with self.db.edit() as state:
                state['mind'] = {'extra': [{'name': 'manual', 'level': 100}]}
                raise RuntimeError('cancelled')
        self.assertEqual(self.db.load(), dict(snapshot=None, mind={}, technology={}))
        self.assert_counts(ships=0, mind_settings=0)

    def test_scan_conflict_and_invalid_experience_roll_back(self):
        self.db.save_scan(snapshot(), revision({}))
        committed = self.db.load()
        with self.assertRaises(FleetDatabaseConflict):
            self.db.save_scan(snapshot('stale'), revision({}))
        for invalid in (-1, 1.5, '100', True, object()):
            with self.subTest(extra_exp=repr(invalid)):
                broken = snapshot('broken')
                broken['ships'][1]['extra_exp'] = invalid
                with self.assertRaises((TypeError, ValueError, sqlite3.Error)):
                    self.db.save_scan(broken, 'first')
                self.assertEqual(self.db.load(), committed)
        self.assert_counts(ships=3, fleet_info=1)
        self.db.save_scan(snapshot('second'), 'first')
        self.assertEqual(self.db.load()['snapshot']['revision'], 'second')
        self.assert_counts(ships=3, fleet_info=1)

    def test_read_recovers_hot_journal_after_crashed_editor(self):
        self.db.save_scan(snapshot(), revision({}))
        with self.db.edit() as state:
            state['mind'] = {'extra': [{'name': 'manual', 'level': 100}]}
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
        self.assert_counts(ships=3, mind_settings=1, nation_technology=1)
        self.assertEqual(self.db.load(), committed)

    def test_schema_one_is_refused_without_changing_legacy_rows(self):
        self.db.path.parent.mkdir()
        with closing(sqlite3.connect(self.db.path)) as connection, connection:
            connection.execute('CREATE TABLE scan_cards (payload TEXT NOT NULL)')
            connection.execute("INSERT INTO scan_cards VALUES ('legacy data')")
            connection.execute('PRAGMA user_version=1')
        before = self.db.path.read_bytes()
        for action in (self.db.load, lambda: self.db.save_scan(snapshot(), revision({}))):
            with self.assertRaisesRegex(ValueError, '1'):
                action()
            self.assertEqual(self.db.path.read_bytes(), before)
        with self.assertRaisesRegex(ValueError, '1'):
            with self.db.edit(import_legacy=True):
                pass
        self.assertEqual(self.db.path.read_bytes(), before)
        with closing(sqlite3.connect(self.db.path)) as connection:
            self.assertEqual(connection.execute('SELECT payload FROM scan_cards').fetchone()[0], 'legacy data')

    def test_cross_process_editors_serialize_without_lost_updates(self):
        self.run_processes(increment_worker, [(15,), (15,), (15,)])
        self.assertEqual(self.db.load()['mind']['extra'][0]['level'], 46)

    def test_cross_process_scans_accept_only_one_expected_revision(self):
        context = multiprocessing.get_context('spawn')
        queue = context.Queue()
        self.addCleanup(queue.close)
        self.run_processes(scan_worker, [('one', queue), ('two', queue)])
        self.assertEqual(sorted([queue.get(timeout=5), queue.get(timeout=5)]), ['conflict', 'saved'])
        self.assert_counts(ships=3, fleet_info=1)

    def test_cross_process_explicit_import_succeeds_only_once(self):
        context = multiprocessing.get_context('spawn')
        queue = context.Queue()
        self.addCleanup(queue.close)
        self.run_processes(import_worker, [('10102', queue), ('10103', queue)])
        self.assertEqual(sorted([queue.get(timeout=5), queue.get(timeout=5)]),
                         ['already_imported', 'imported'])
        loaded = self.db.load(include_import_status=True)
        self.assertTrue(loaded['legacy_imported'])
        self.assertIn(loaded['technology']['manual'],
                      ({'10102': {'owned': True}}, {'10103': {'owned': True}}))
        self.assertEqual(list(loaded['technology']['manual']), [loaded['snapshot']['revision']])
        self.assert_counts(ships=3, fleet_info=1)

    def test_missing_reads_ignore_legacy_configuration_and_create_nothing(self):
        self.config.write_text('{"DockScan":{"Dock":{"Result":{"ships":[1]}}}}')
        original = self.config.read_bytes()
        self.assertEqual(self.db.load(), dict(snapshot=None, mind={}, technology={}))
        self.assertEqual(self.db.load(include_import_status=True),
                         dict(snapshot=None, mind={}, technology={}, legacy_imported=False))
        self.assertFalse(self.db.path.parent.exists())
        self.assertEqual(self.config.read_bytes(), original)
        self.assertEqual(self.db.path, self.config.parent / 'fleet' / 'pilot.sqlite3')


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
        self.assertEqual(self.db.load()['snapshot']['revision'], 'first')
        self.assertEqual(len(self.db.load()['snapshot']['ships']), 3)
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


if __name__ == '__main__':
    unittest.main()
