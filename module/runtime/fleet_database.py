"""Independent, transactional fleet evidence and analysis storage per instance."""
import json
import os
import sqlite3
import stat
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from module.runtime.fleet_tech import revision


class FleetDatabaseConflict(ValueError):
    """The completed scan was built from an outdated snapshot."""


class FleetDatabaseAlreadyImported(ValueError):
    """This instance already completed its explicit legacy fleet import."""


class FleetDatabase:
    SCHEMA_VERSION = 1
    BUSY_TIMEOUT_MS = 10000

    def __init__(self, config_path: Path):
        config_path = Path(config_path)
        self.path = config_path.parent / 'fleet' / (config_path.stem + '.sqlite3')

    @staticmethod
    def _json(value):
        return json.dumps(value, ensure_ascii=False, allow_nan=False)

    @staticmethod
    def _check_file(path, *, directory=False):
        try:
            info = path.lstat()
        except FileNotFoundError:
            return False
        valid = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
        if not valid or path.is_junction():
            raise ValueError(f'Unsafe fleet database path: {path}')
        return True

    def _check_paths(self):
        if not self._check_file(self.path.parent, directory=True):
            return False
        exists = self._check_file(self.path)
        # SQLite opens these itself; refuse existing links before opening SQLite.
        for suffix in ('-journal', '-wal', '-shm'):
            self._check_file(Path(str(self.path) + suffix))
        return exists

    def _connect(self, *, write, create=True):
        exists = self._check_paths()
        if not exists and (not write or not create):
            return None
        if write and create:
            self.path.parent.mkdir(mode=0o700, exist_ok=True)
            if not self._check_paths():
                self._initialize_file()
            self._check_paths()
        uri = self.path.absolute().as_uri() + ('?mode=rw' if write else '?mode=ro')
        connection = sqlite3.connect(uri, uri=True, timeout=self.BUSY_TIMEOUT_MS / 1000,
                                     isolation_level=None)
        try:
            connection.execute(f'PRAGMA busy_timeout={self.BUSY_TIMEOUT_MS}')
            connection.execute('PRAGMA foreign_keys=ON')
            if not write:
                connection.execute('PRAGMA query_only=ON')
        except BaseException:
            connection.close()
            raise
        return connection

    def _initialize_file(self):
        # Publish an initialized database atomically. A report racing the first
        # writer sees either missing storage or a valid empty schema, never a
        # zero-byte SQLite file or a partially created schema.
        descriptor, filename = tempfile.mkstemp(prefix=f'.{self.path.name}.', dir=self.path.parent)
        os.close(descriptor)
        temporary = Path(filename)
        try:
            connection = sqlite3.connect(temporary, isolation_level=None)
            try:
                connection.execute('BEGIN IMMEDIATE')
                self._schema(connection, create=True)
                connection.commit()
            finally:
                connection.close()
            try:
                os.link(temporary, self.path, follow_symlinks=False)
            except FileExistsError:
                # Another process initialized the same instance first.
                self._check_paths()
        finally:
            temporary.unlink()

    def _schema(self, connection, *, create=False):
        version = connection.execute('PRAGMA user_version').fetchone()[0]
        if version == self.SCHEMA_VERSION:
            return
        if version != 0 or not create:
            raise ValueError(f'Unsupported fleet database schema version: {version}')
        if connection.execute("SELECT 1 FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'").fetchone():
            raise ValueError('Unversioned fleet database contains existing tables')
        statements = (
            'CREATE TABLE scan_batches (id INTEGER PRIMARY KEY, revision TEXT NOT NULL, '
            'metadata TEXT NOT NULL, has_history INTEGER NOT NULL)',
            'CREATE TABLE scan_cards (batch_id INTEGER NOT NULL REFERENCES scan_batches(id), '
            'position INTEGER NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(batch_id, position))',
            'CREATE TABLE scan_history_fields (batch_id INTEGER NOT NULL REFERENCES scan_batches(id), '
            'field TEXT NOT NULL, kind TEXT NOT NULL, payload TEXT, PRIMARY KEY(batch_id, field))',
            'CREATE TABLE scan_evidence (batch_id INTEGER NOT NULL REFERENCES scan_batches(id), '
            'field TEXT NOT NULL, position INTEGER NOT NULL, ship_key TEXT, payload TEXT NOT NULL, '
            'PRIMARY KEY(batch_id, field, position))',
            'CREATE TABLE analysis_fields (scope TEXT NOT NULL, field TEXT NOT NULL, '
            'payload TEXT NOT NULL, PRIMARY KEY(scope, field))',
            'CREATE TABLE mind_overrides (ship_key TEXT PRIMARY KEY, payload TEXT NOT NULL)',
            'CREATE TABLE mind_removed (position INTEGER PRIMARY KEY, payload TEXT NOT NULL)',
            'CREATE TABLE mind_extra (position INTEGER PRIMARY KEY, payload TEXT NOT NULL)',
            'CREATE TABLE technology_manual (ship_key TEXT PRIMARY KEY, payload TEXT NOT NULL)',
            'CREATE TABLE technology_nations (nation_key TEXT PRIMARY KEY, payload TEXT NOT NULL)',
            'CREATE TABLE imports (name TEXT PRIMARY KEY, imported_at TEXT NOT NULL)',
            'CREATE TABLE legacy_sections (name TEXT PRIMARY KEY, payload TEXT NOT NULL)',
        )
        for statement in statements:
            connection.execute(statement)
        connection.execute(f'PRAGMA user_version={self.SCHEMA_VERSION}')

    def _snapshot(self, connection):
        batch = connection.execute(
            'SELECT id, metadata, has_history FROM scan_batches ORDER BY id DESC LIMIT 1').fetchone()
        if batch is None:
            return None
        batch_id, metadata, has_history = batch
        snapshot = json.loads(metadata)
        snapshot['ships'] = [json.loads(row[0]) for row in connection.execute(
            'SELECT payload FROM scan_cards WHERE batch_id=? ORDER BY position', (batch_id,))]
        if has_history:
            history = snapshot['history'] = {}
            for field, kind, payload in connection.execute(
                    'SELECT field, kind, payload FROM scan_history_fields WHERE batch_id=?', (batch_id,)):
                rows = connection.execute(
                    'SELECT ship_key, payload FROM scan_evidence WHERE batch_id=? AND field=? '
                    'ORDER BY position', (batch_id, field))
                if kind == 'map':
                    history[field] = {key: json.loads(value) for key, value in rows}
                elif kind == 'list':
                    history[field] = [json.loads(value) for _, value in rows]
                elif kind == 'scalar':
                    history[field] = json.loads(payload)
                else:
                    raise ValueError(f'Invalid fleet history kind: {kind}')
        return snapshot

    def _options(self, connection, scope):
        options = {field: json.loads(payload) for field, payload in connection.execute(
            'SELECT field, payload FROM analysis_fields WHERE scope=?', (scope,))}
        fields = (('overrides', 'mind_overrides', True), ('removed', 'mind_removed', False),
                  ('extra', 'mind_extra', False)) if scope == 'mind' else (
                      ('manual', 'technology_manual', True), ('nations', 'technology_nations', True))
        for field, table, mapping in fields:
            if field in options:
                rows = connection.execute(f'SELECT * FROM {table} ORDER BY 1')
                options[field] = ({key: json.loads(value) for key, value in rows} if mapping else
                                  [json.loads(value) for _, value in rows])
        return options

    def _load(self, connection):
        return dict(snapshot=self._snapshot(connection), mind=self._options(connection, 'mind'),
                    technology=self._options(connection, 'technology'))

    @staticmethod
    def _legacy_imported(connection):
        return connection.execute("SELECT 1 FROM imports WHERE name='legacy_fleet'").fetchone() is not None

    def load(self, *, include_import_status=False):
        """Read without creating storage, migrating configuration, or changing evidence."""
        try:
            return self._read_only(include_import_status=include_import_status)
        except sqlite3.OperationalError as exc:
            if getattr(exc, 'sqlite_errorcode', None) != sqlite3.SQLITE_READONLY_ROLLBACK:
                raise
        # A process died after spilling uncommitted pages. SQLite must roll its
        # hot journal back before even a read can proceed. Only this specific
        # error permits opening existing storage for recovery; clean reads stay
        # read-only, and an instance deleted in the meantime is never recreated.
        connection = self._connect(write=True, create=False)
        if connection is not None:
            try:
                connection.execute('BEGIN IMMEDIATE')
                self._schema(connection)
                connection.rollback()
            finally:
                connection.close()
        return self._read_only(include_import_status=include_import_status)

    def _read_only(self, *, include_import_status=False):
        connection = self._connect(write=False)
        if connection is None:
            state = dict(snapshot=None, mind={}, technology={})
            if include_import_status:
                state['legacy_imported'] = False
            return state
        try:
            connection.execute('BEGIN')
            self._schema(connection)
            state = self._load(connection)
            if include_import_status:
                state['legacy_imported'] = self._legacy_imported(connection)
            return state
        finally:
            connection.close()

    def _write_options(self, connection, scope, options):
        fields = (('overrides', 'mind_overrides', True), ('removed', 'mind_removed', False),
                  ('extra', 'mind_extra', False)) if scope == 'mind' else (
                      ('manual', 'technology_manual', True), ('nations', 'technology_nations', True))
        normalized = {field for field, _, _ in fields}
        connection.execute('DELETE FROM analysis_fields WHERE scope=?', (scope,))
        for field, value in options.items():
            if scope == 'technology' and field not in normalized:
                continue
            connection.execute('INSERT INTO analysis_fields VALUES (?, ?, ?)',
                               (scope, field, 'null' if field in normalized else self._json(value)))
        for field, table, mapping in fields:
            connection.execute(f'DELETE FROM {table}')
            values = options.get(field, {} if mapping else [])
            rows = values.items() if mapping else enumerate(values)
            connection.executemany(f'INSERT INTO {table} VALUES (?, ?)',
                                   ((key, self._json(value)) for key, value in rows))

    @contextmanager
    def edit(self, *, import_legacy=False):
        """Serialize analysis edits; mutations of the yielded snapshot are never saved."""
        connection = self._connect(write=True)
        try:
            connection.execute('BEGIN IMMEDIATE')
            self._schema(connection)
            if import_legacy and self._legacy_imported(connection):
                raise FleetDatabaseAlreadyImported('旧舰船数据已经导入，不能重复导入')
            state = self._load(connection)
            yield state
            self._write_options(connection, 'mind', state['mind'])
            self._write_options(connection, 'technology', state['technology'])
            if import_legacy:
                for snapshot in state.get('legacy_snapshots', []):
                    self._append_snapshot(connection, snapshot)
                connection.executemany('INSERT INTO legacy_sections VALUES (?, ?)',
                                       ((name, self._json(value)) for name, value in
                                        state.get('legacy_sources', {}).items()))
                connection.execute('INSERT INTO imports VALUES (?, ?)',
                                   ('legacy_fleet', datetime.now(timezone.utc).isoformat(timespec='seconds')))
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def save_scan(self, snapshot, expected_revision):
        """Append one completed batch, retaining every physical copy and history row."""
        connection = self._connect(write=True)
        try:
            connection.execute('BEGIN IMMEDIATE')
            self._schema(connection)
            latest = connection.execute(
                'SELECT revision FROM scan_batches ORDER BY id DESC LIMIT 1').fetchone()
            if (latest[0] if latest else revision({})) != expected_revision:
                raise FleetDatabaseConflict('舰船数据库已更新，请重新扫描')
            self._append_snapshot(connection, snapshot)
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _append_snapshot(self, connection, snapshot):
        metadata = {key: value for key, value in snapshot.items() if key not in ('ships', 'history')}
        cursor = connection.execute('INSERT INTO scan_batches (revision, metadata, has_history) '
                                    'VALUES (?, ?, ?)',
                                    (snapshot['revision'], self._json(metadata), 'history' in snapshot))
        batch_id = cursor.lastrowid
        connection.executemany('INSERT INTO scan_cards VALUES (?, ?, ?)',
                               ((batch_id, index, self._json(ship))
                                for index, ship in enumerate(snapshot['ships'])))
        for field, value in snapshot.get('history', {}).items():
            kind = 'map' if isinstance(value, dict) else 'list' if isinstance(value, list) else 'scalar'
            connection.execute('INSERT INTO scan_history_fields VALUES (?, ?, ?, ?)',
                               (batch_id, field, kind, self._json(value) if kind == 'scalar' else None))
            rows = value.items() if kind == 'map' else enumerate(value) if kind == 'list' else ()
            connection.executemany('INSERT INTO scan_evidence VALUES (?, ?, ?, ?, ?)',
                                   ((batch_id, field, index, key if kind == 'map' else
                                     str(item) if field == 'absent' else None, self._json(item))
                                    for index, (key, item) in enumerate(rows)))
