"""每实例一份精简舰船底账：当前卡片、科技进度与人工设置。"""
import os
import sqlite3
import stat
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from module.runtime import fleet_tech as tech


class FleetDatabaseConflict(ValueError):
    """The current ledger changed while a scan was running."""


class FleetDatabaseAlreadyImported(ValueError):
    """This instance already completed its explicit local JSON import."""


MIND_FIELDS = ('name', 'level', 'rarity', 'base_rarity', 'excluded', 'review', 'source')
MANUAL_FIELDS = ('owned', 'full_star', 'lv120', 'level', 'level_toggle')
MIND_OPTIONS = ('overrides', 'removed', 'extra', 'updated_at', 'snapshot_revision')


class FleetDatabase:
    SCHEMA_VERSION = 2
    BUSY_TIMEOUT_MS = 10000

    def __init__(self, config_path: Path):
        config_path = Path(config_path)
        self.path = config_path.parent / 'fleet' / (config_path.stem + '.sqlite3')

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
            raise ValueError(f'Unsupported fleet database schema version: {version}; '
                             '请先备份旧试验版舰船数据库，再使用新底账重新扫描或显式导入旧配置')
        if connection.execute("SELECT 1 FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'").fetchone():
            raise ValueError('Unversioned fleet database contains existing tables')
        # Execute statements individually: executescript would commit the caller's transaction.
        for statement in Path(__file__).with_name('fleet_schema.sql').read_text(encoding='utf-8').split(';'):
            if statement.strip():
                connection.execute(statement)
        connection.execute('INSERT INTO fleet_info (id) VALUES (1)')
        connection.execute(f'PRAGMA user_version={self.SCHEMA_VERSION}')

    @staticmethod
    def _row(connection, query):
        cursor = connection.execute(query)
        row = cursor.fetchone()
        return dict(zip((item[0] for item in cursor.description), row)) if row else None

    def _snapshot(self, connection):
        info = self._row(connection, 'SELECT * FROM fleet_info WHERE id=1')
        if info['revision'] is None:
            return None
        cursor = connection.execute('SELECT id, group_type, name, level, rarity, base_rarity, '
                                    'extra_exp, review, source FROM ships ORDER BY position')
        columns = [item[0] for item in cursor.description]
        ships = [dict(zip(columns, row)) for row in cursor]
        for ship in ships:
            ship['review'] = bool(ship['review'])
        keys = ('updated_at', 'min_level', 'max_level', 'reached_bottom', 'filters_clear',
                'physical_count', 'expected_count')
        metadata = {key: info[key] for key in keys}
        for key in ('reached_bottom', 'filters_clear'):
            metadata[key] = bool(metadata[key])
        # Compatibility projection only: history is computed, never stored as scan records.
        derived = tech.merge_scan({}, ships, **metadata)
        observed = {str(gid): level for gid, level in connection.execute(
            'SELECT group_type, max_observed_level FROM technology WHERE max_observed_level IS NOT NULL')}
        history = dict(observed=observed, current=derived['current'], absent=derived['absent'])
        return dict(version=1, revision=info['revision'], ships=ships, history=history,
                    complete=bool(info['complete']), review_count=info['review_count'], source='scan', **metadata)

    @staticmethod
    def _unpack(row, names, mask, *, start=0):
        return {name: row[name] for index, name in enumerate(names, start) if mask & (1 << index)}

    def _options(self, connection, scope):
        info = self._row(connection, 'SELECT * FROM fleet_info WHERE id=1')
        if scope == 'mind':
            mask = info['mind_fields']
            values = dict(overrides={}, removed=[], extra=[], updated_at=info['mind_updated_at'],
                          snapshot_revision=info['mind_snapshot_revision'])
            cursor = connection.execute('SELECT * FROM mind_settings ORDER BY ordinal, ship_key')
            columns = [item[0] for item in cursor.description]
            for raw in cursor:
                row = dict(zip(columns, raw))
                ship = self._unpack(row, MIND_FIELDS, row['fields'])
                for key in ('excluded', 'review'):
                    if ship.get(key) is not None:
                        ship[key] = bool(ship[key])
                if row['kind'] == 'override':
                    values['overrides'][row['ship_key']] = ship
                elif row['kind'] == 'removed':
                    values['removed'].append(row['ship_key'])
                else:
                    values['extra'].append(ship)
            return {key: values[key] for index, key in enumerate(MIND_OPTIONS) if mask & (1 << index)}
        values = dict(manual={}, nations={})
        cursor = connection.execute('SELECT * FROM technology WHERE manual_fields != 0 ORDER BY group_type')
        columns = [item[0] for item in cursor.description]
        for raw in cursor:
            row = dict(zip(columns, raw))
            entry = self._unpack(row, MANUAL_FIELDS[:-1], row['manual_fields'], start=1)
            for key in ('owned', 'full_star', 'lv120'):
                if entry.get(key) is not None:
                    entry[key] = bool(entry[key])
            if row['manual_fields'] & (1 << len(MANUAL_FIELDS)):
                entry['level_toggle'] = ({} if row['low_level'] is None and row['high_level'] is None
                                         else dict(low=row['low_level'], high=row['high_level']))
            values['manual'][row['group_type']] = entry
        values['nations'] = dict(connection.execute('SELECT nation_key, level FROM nation_technology ORDER BY nation_key'))
        return {key: values[key] for index, key in enumerate(('manual', 'nations'))
                if info['technology_fields'] & (1 << index)}

    def _load(self, connection):
        return dict(snapshot=self._snapshot(connection), mind=self._options(connection, 'mind'),
                    technology=self._options(connection, 'technology'))

    @staticmethod
    def _legacy_imported(connection):
        return connection.execute('SELECT imported_at IS NOT NULL FROM fleet_info WHERE id=1').fetchone()[0] == 1

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

    @staticmethod
    def _integer(value, label, low=0, high=None, *, nullable=True):
        if value is None and nullable:
            return None
        if type(value) is not int or value < low or (high is not None and value > high):
            raise ValueError(f'{label} must be an integer in range {low}..{high}')
        return value

    @staticmethod
    def _boolean(value, label):
        if value is not None and type(value) is not bool:
            raise ValueError(f'{label} must be boolean or null')
        return value

    def _write_options(self, connection, scope, options):
        if scope == 'mind':
            connection.execute('DELETE FROM mind_settings')
            for kind, key in (('override', 'overrides'), ('removed', 'removed'), ('extra', 'extra')):
                rows = options.get(key, {} if kind == 'override' else [])
                items = rows.items() if kind == 'override' else enumerate(rows)
                for ordinal, (ship_key, value) in enumerate(items):
                    if kind == 'removed':
                        ship_key, value = value, {}
                    elif kind == 'extra':
                        ship_key = str(ship_key)
                    mask = sum(1 << index for index, field in enumerate(MIND_FIELDS) if field in value)
                    values = [value.get(field) for field in MIND_FIELDS]
                    self._integer(value.get('level'), 'mind level', 0, 125)
                    for field in ('excluded', 'review'):
                        self._boolean(value.get(field), field)
                    connection.execute('INSERT INTO mind_settings VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                                       (kind, ship_key, ordinal, mask, *values))
            mask = sum(1 << index for index, key in enumerate(MIND_OPTIONS) if key in options)
            connection.execute('UPDATE fleet_info SET mind_fields=?, mind_updated_at=?, '
                               'mind_snapshot_revision=? WHERE id=1',
                               (mask, options.get('updated_at'), options.get('snapshot_revision')))
            return
        # Only manual columns are replaced. Accumulated automatic levels survive every edit.
        connection.execute('UPDATE technology SET manual_fields=0, owned=NULL, full_star=NULL, '
                           'lv120=NULL, level=NULL, low_level=NULL, high_level=NULL')
        for gid, entry in options.get('manual', {}).items():
            if not isinstance(gid, str) or not gid.isdecimal():
                raise ValueError('technology group_type must be a numeric string')
            for field in ('owned', 'full_star', 'lv120'):
                self._boolean(entry.get(field), field)
            self._integer(entry.get('level'), 'manual level', 1, 125)
            pair = entry.get('level_toggle', {})
            if pair:
                self._integer(pair.get('low'), 'low level', 1, 119, nullable=False)
                self._integer(pair.get('high'), 'high level', 120, 125, nullable=False)
            mask = 1 | sum(1 << index for index, field in enumerate(MANUAL_FIELDS, 1) if field in entry)
            connection.execute('INSERT INTO technology (group_type, manual_fields, owned, full_star, '
                               'lv120, level, low_level, high_level) VALUES (?, ?, ?, ?, ?, ?, ?, ?) '
                               'ON CONFLICT(group_type) DO UPDATE SET manual_fields=excluded.manual_fields, '
                               'owned=excluded.owned, full_star=excluded.full_star, lv120=excluded.lv120, '
                               'level=excluded.level, low_level=excluded.low_level, high_level=excluded.high_level',
                               (gid, mask, entry.get('owned'), entry.get('full_star'), entry.get('lv120'),
                                entry.get('level'), pair.get('low'), pair.get('high')))
        connection.execute('DELETE FROM technology WHERE max_observed_level IS NULL AND manual_fields=0')
        connection.execute('DELETE FROM nation_technology')
        for key, value in options.get('nations', {}).items():
            self._integer(value, 'nation level')
            connection.execute('INSERT INTO nation_technology VALUES (?, ?)', (key, value))
        mask = sum(1 << index for index, key in enumerate(('manual', 'nations')) if key in options)
        connection.execute('UPDATE fleet_info SET technology_fields=? WHERE id=1', (mask,))

    @contextmanager
    def edit(self, *, import_legacy=False):
        """Serialize manual edits; the yielded current card list is read-only to this operation."""
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
                if state.get('legacy_snapshot') is not None:
                    # Import can add past technology achievements, not replace existing current cards.
                    current = self._snapshot(connection)
                    self._write_snapshot(connection, state['legacy_snapshot'], replace_cards=current is None)
                connection.execute('UPDATE fleet_info SET imported_at=? WHERE id=1',
                                   (datetime.now(timezone.utc).isoformat(timespec='seconds'),))
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def save_scan(self, snapshot, expected_revision):
        """Replace current rows atomically; no batch archive or guessed cross-scan identity."""
        connection = self._connect(write=True)
        try:
            connection.execute('BEGIN IMMEDIATE')
            self._schema(connection)
            latest = connection.execute('SELECT revision FROM fleet_info WHERE id=1').fetchone()[0]
            if (latest if latest is not None else tech.revision({})) != expected_revision:
                raise FleetDatabaseConflict('舰船数据库已更新，请重新扫描')
            self._write_snapshot(connection, snapshot)
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _write_snapshot(self, connection, snapshot, *, replace_cards=True):
        if not isinstance(snapshot.get('revision'), str) or not snapshot['revision']:
            raise ValueError('snapshot revision must be a nonempty string')
        low = self._integer(snapshot.get('min_level', 1), 'min level', 1, 125, nullable=False)
        high = self._integer(snapshot.get('max_level', 125), 'max level', low, 125, nullable=False)
        rows = snapshot['ships']
        physical_count = self._integer(snapshot.get('physical_count', len(rows)), 'physical count',
                                       nullable=False)
        expected_count = self._integer(snapshot.get('expected_count'), 'expected count')
        reached_bottom = self._boolean(snapshot.get('reached_bottom', False), 'reached bottom')
        filters_clear = self._boolean(snapshot.get('filters_clear', False), 'filters clear')
        if replace_cards:
            connection.execute('DELETE FROM ships')
            for index, ship in enumerate(rows):
                self._integer(ship.get('group_type'), 'group type', 1)
                self._integer(ship.get('level', 0), 'ship level', 0, 125, nullable=False)
                self._integer(ship.get('extra_exp'), 'extra experience')
                self._boolean(ship.get('review', False), 'review')
                connection.execute('INSERT INTO ships (position, group_type, name, level, rarity, base_rarity, '
                                   'extra_exp, review, source) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
                                   (index, ship.get('group_type'), ship.get('name', ''), ship.get('level', 0),
                                    ship.get('rarity', ''), ship.get('base_rarity', ''), ship.get('extra_exp'),
                                    ship.get('review', False), ship.get('source', '')))
        elif rows != self._snapshot(connection)['ships']:
            raise ValueError('导入不能覆盖已有舰船明细')
        metadata = dict(min_level=low, max_level=high, reached_bottom=bool(reached_bottom),
                        filters_clear=bool(filters_clear), physical_count=physical_count,
                        expected_count=expected_count, updated_at=snapshot.get('updated_at', ''))
        derived = tech.merge_scan({}, rows, **metadata)
        # One maximum per ship type is sufficient to keep earned technology after retirement.
        observed = dict(snapshot.get('history', {}).get('observed', {}))
        for gid, level in derived['observed'].items():
            observed[gid] = max(observed.get(gid, 0), level)
        for gid, level in observed.items():
            self._integer(level, 'observed level', 1, 125, nullable=False)
            if not isinstance(gid, str) or not gid.isdecimal():
                raise ValueError('technology group_type must be a numeric string')
            connection.execute('INSERT INTO technology (group_type, max_observed_level) VALUES (?, ?) '
                               'ON CONFLICT(group_type) DO UPDATE SET max_observed_level='
                               'MAX(COALESCE(technology.max_observed_level, 0), excluded.max_observed_level)', (gid, level))
        connection.execute('UPDATE fleet_info SET revision=?, updated_at=?, min_level=?, max_level=?, '
                           'reached_bottom=?, filters_clear=?, physical_count=?, expected_count=?, '
                           'complete=?, review_count=? WHERE id=1',
                           (snapshot['revision'], metadata['updated_at'], low, high, metadata['reached_bottom'],
                            metadata['filters_clear'], metadata['physical_count'], metadata['expected_count'],
                            derived['last_scan']['complete'], derived['last_scan']['review_count']))
