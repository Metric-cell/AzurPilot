"""玩家编辑的舰船底账；设备读取仅产生待确认更新。"""
import copy
import os
import sqlite3
import stat
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from module.runtime import fleet_tech as tech
from module.runtime import fleet_ledger as ledger


class FleetDatabaseConflict(ValueError):
    """The canonical ledger or pending reading changed during an edit."""


class FleetDatabaseAlreadyImported(ValueError):
    """The optional local legacy reading was already staged."""


class FleetDatabase:
    SCHEMA_VERSION = 3
    BUSY_TIMEOUT_MS = 10000

    def __init__(self, config_path):
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
    def _rows(connection, query, args=()):
        cursor = connection.execute(query, args)
        columns = [item[0] for item in cursor.description]
        return [dict(zip(columns, row)) for row in cursor]

    def _info(self, connection):
        return self._rows(connection, 'SELECT * FROM fleet_info WHERE id=1')[0]

    def _cards(self, connection):
        rows = self._rows(connection, 'SELECT id, ' + ', '.join(ledger.CARD_FIELDS) + ' FROM ships ORDER BY position')
        for row in rows:
            for field in ('review', 'excluded'):
                row[field] = bool(row[field])
        return rows

    def _snapshot(self, connection):
        info = self._info(connection)
        if info['revision'] is None:
            return None
        rows = self._cards(connection)
        observed = dict(connection.execute('SELECT group_type, max_observed_level FROM technology '
                                           'WHERE max_observed_level IS NOT NULL'))
        current = {}
        for row in rows:
            if not row['review'] and row['group_type'] is not None and 1 <= row['level'] <= 125:
                key = str(row['group_type'])
                current[key] = max(current.get(key, 0), row['level'])
        return dict(version=1, revision=info['revision'], ships=rows, history=dict(
            observed=observed, current=current, absent=[]), updated_at=info['updated_at'],
            min_level=1, max_level=125, reached_bottom=False, filters_clear=False,
            physical_count=len(rows), expected_count=None, complete=False,
            review_count=sum(row['review'] for row in rows), source='ledger')

    @staticmethod
    def _manual(row):
        result = {field: (bool(row[field]) if field in ('owned', 'full_star', 'lv120') and row[field] is not None
                          else row[field]) for bit, field in enumerate(ledger.MANUAL_FIELDS[:-1], 1)
                  if row['manual_fields'] & (1 << bit)}
        if row['manual_fields'] & (1 << len(ledger.MANUAL_FIELDS)):
            result['level_toggle'] = ({} if row['low_level'] is None and row['high_level'] is None
                                      else dict(low=row['low_level'], high=row['high_level']))
        return result

    def _options(self, connection, scope='technology'):
        if scope != 'technology':
            return {}
        values = dict(manual={}, nations={})
        for row in self._rows(connection, 'SELECT * FROM technology WHERE manual_fields != 0'):
            values['manual'][row['group_type']] = self._manual(row)
        values['nations'] = dict(connection.execute('SELECT nation_key, level FROM nation_technology'))
        mask = self._info(connection)['technology_fields']
        return {key: values[key] for index, key in enumerate(('manual', 'nations')) if mask & (1 << index)}

    @staticmethod
    def _legacy_imported(connection):
        return connection.execute('SELECT imported_at IS NOT NULL FROM fleet_info WHERE id=1').fetchone()[0] == 1

    def _load(self, connection, *, include_import_status=False):
        info = self._info(connection)
        value = dict(snapshot=self._snapshot(connection), technology=self._options(connection), mind={},
                     pending_revision=info['pending_revision'], pending_changes=self._differences(connection))
        if include_import_status:
            value['legacy_imported'] = self._legacy_imported(connection)
        return value

    def load(self, *, include_import_status=False):
        try:
            return self._read_only(include_import_status=include_import_status)
        except sqlite3.OperationalError as exc:
            if getattr(exc, 'sqlite_errorcode', None) != sqlite3.SQLITE_READONLY_ROLLBACK:
                raise
        # A crashed writer may leave a hot rollback journal. Only this recovery
        # condition opens existing storage for writing, never creates a missing file.
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
            value = dict(snapshot=None, mind={}, technology={}, pending_revision=None, pending_changes=[])
            if include_import_status:
                value['legacy_imported'] = False
            return value
        try:
            connection.execute('BEGIN')
            self._schema(connection)
            return self._load(connection, include_import_status=include_import_status)
        finally:
            connection.close()

    @contextmanager
    def _transaction(self):
        connection = self._connect(write=True)
        try:
            connection.execute('BEGIN IMMEDIATE')
            self._schema(connection)
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _revision(self, connection):
        return self._info(connection)['revision'] or tech.revision({})

    def _check_revision(self, connection, expected):
        if self._revision(connection) != expected:
            raise FleetDatabaseConflict('舰船底账已更新，请重新载入后操作')

    @staticmethod
    def _now():
        return datetime.now(timezone.utc).isoformat(timespec='seconds')

    def _touch(self, connection):
        connection.execute('UPDATE fleet_info SET revision=?, updated_at=? WHERE id=1', (tech.revision(uuid4().hex), self._now()))

    def _write_options(self, connection, scope, options):
        if scope != 'technology':
            return
        connection.execute('UPDATE technology SET manual_fields=0, owned=NULL, full_star=NULL, '
                           'lv120=NULL, level=NULL, low_level=NULL, high_level=NULL')
        for gid, entry in options.get('manual', {}).items():
            self._write_technology(connection, 'technology', gid, None, entry)
        connection.execute('DELETE FROM technology WHERE max_observed_level IS NULL AND manual_fields=0')
        connection.execute('DELETE FROM nation_technology')
        for key, value in options.get('nations', {}).items():
            ledger.integer(value, '阵营等级', nullable=True)
            connection.execute('INSERT INTO nation_technology VALUES (?, ?)', (key, value))
        mask = sum(1 << index for index, key in enumerate(('manual', 'nations')) if key in options)
        connection.execute('UPDATE fleet_info SET technology_fields=? WHERE id=1', (mask,))

    @staticmethod
    def _write_technology(connection, table, gid, observed, entry):
        if not isinstance(gid, str) or not gid.isdecimal():
            raise ValueError('舰船群组必须是数字编号')
        ledger.integer(observed, '累计科技等级', 1, 125, nullable=True)
        ledger.integer(entry.get('level'), '人工等级', 1, 125, nullable=True)
        for field in ('owned', 'full_star', 'lv120'):
            if entry.get(field) is not None and type(entry[field]) is not bool:
                raise ValueError('人工科技状态必须是布尔值或空值')
        pair = entry.get('level_toggle', {})
        if not isinstance(pair, dict):
            raise ValueError('等级切换端点无效')
        if pair:
            ledger.integer(pair.get('low'), '低等级端点', 1, 119)
            ledger.integer(pair.get('high'), '高等级端点', 120, 125)
        mask = (1 | sum(1 << bit for bit, field in enumerate(ledger.MANUAL_FIELDS, 1) if field in entry)) if entry else 0
        connection.execute(f'INSERT INTO {table} (group_type, max_observed_level, manual_fields, owned, '
                           'full_star, lv120, level, low_level, high_level) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) '
                           'ON CONFLICT(group_type) DO UPDATE SET max_observed_level='
                           f'CASE WHEN excluded.max_observed_level IS NULL THEN {table}.max_observed_level '
                           f'ELSE MAX(COALESCE({table}.max_observed_level, 0), excluded.max_observed_level) END, '
                           'manual_fields=excluded.manual_fields, owned=excluded.owned, full_star=excluded.full_star, '
                           'lv120=excluded.lv120, level=excluded.level, low_level=excluded.low_level, high_level=excluded.high_level',
                           (gid, observed, mask, entry.get('owned'), entry.get('full_star'), entry.get('lv120'),
                            entry.get('level'), pair.get('low'), pair.get('high')))

    @contextmanager
    def edit(self, *, import_legacy=False):
        if import_legacy:
            raise ValueError('读取旧数据请使用待确认更新入口')
        with self._transaction() as connection:
            state = self._load(connection)
            original = copy.deepcopy(state['technology'])
            yield state
            if original != state['technology']:
                self._write_options(connection, 'technology', state['technology'])
                self._touch(connection)

    @staticmethod
    def _insert_card(connection, values):
        position = connection.execute('SELECT COALESCE(MAX(position), -1)+1 FROM ships').fetchone()[0]
        return connection.execute('INSERT INTO ships (position, ' + ', '.join(ledger.CARD_FIELDS) + ') VALUES ('
                                  + ', '.join('?' for _ in range(len(ledger.CARD_FIELDS) + 1)) + ')',
                                  (position, *(values[field] for field in ledger.CARD_FIELDS))).lastrowid

    @staticmethod
    def _update_card(connection, ship_id, values):
        connection.execute('UPDATE ships SET ' + ', '.join(f'{field}=?' for field in ledger.CARD_FIELDS)
                           + ' WHERE id=?', (*(values[field] for field in ledger.CARD_FIELDS), ship_id))

    def _accumulate(self, connection):
        known = {row['group_type'] for row in tech.catalog()['ships']}
        for row in self._cards(connection):
            if row['group_type'] in known and not row['review'] and 1 <= row['level'] <= 125:
                connection.execute('INSERT INTO technology (group_type, max_observed_level) VALUES (?, ?) '
                                   'ON CONFLICT(group_type) DO UPDATE SET max_observed_level='
                                   'MAX(COALESCE(technology.max_observed_level, 0), excluded.max_observed_level)',
                                   (str(row['group_type']), row['level']))

    def patch_cards(self, changes, expected_revision):
        with self._transaction() as connection:
            self._check_revision(connection, expected_revision)
            current = {row['id']: row for row in self._cards(connection)}
            seen = set()
            for change in changes:
                ship_id = change.get('id')
                if ship_id is not None:
                    ledger.integer(ship_id, '内部序号', 1)
                    if ship_id not in current or ship_id in seen:
                        raise ValueError('内部序号不存在或重复')
                    seen.add(ship_id)
                if change.get('delete'):
                    if ship_id is None or change.keys() - {'id', 'delete'}:
                        raise ValueError('删除只允许指定内部序号')
                    connection.execute('DELETE FROM ships WHERE id=?', (ship_id,))
                else:
                    values = ledger.card(change, current.get(ship_id))
                    if ship_id is None:
                        self._insert_card(connection, values)
                    else:
                        self._update_card(connection, ship_id, values)
            if changes:
                self._accumulate(connection)
                self._touch(connection)

    def _differences(self, connection):
        cards = {row['id']: row for row in self._cards(connection)}
        output = []
        for row in self._rows(connection, 'SELECT * FROM pending_ships ORDER BY id'):
            values = {field: bool(row[field]) if field in ('excluded', 'review') else row[field]
                      for field in ledger.CARD_FIELDS}
            before = cards.get(row['ship_id'])
            operation = row['operation']
            fields = [field for bit, field in enumerate(ledger.CARD_FIELDS) if row['fields'] & (1 << bit)]
            if operation == 'remove':
                if before is None:
                    continue
                delta = ['delete']
            elif operation == 'update' and before:
                delta = ledger.changed(before, values, fields)
                if not delta:
                    continue
            else:
                if operation == 'update':
                    operation = 'resolve'
                delta = fields or list(ledger.CARD_FIELDS)
            eligible = {value[0] for value in connection.execute(
                'SELECT ship_id FROM pending_targets WHERE proposal_id=?', (row['id'],))}
            candidates = [dict(id=value['id'], name=value['name'], level=value['level']) for value in cards.values()
                          if value['id'] in eligible and value['group_type'] == values['group_type']]
            output.append(dict(id=f'ship:{row["id"]}', kind='ship', operation=operation, ship_id=row['ship_id'],
                               group_type=values['group_type'], name=values['name'], before=before,
                               after=None if operation == 'remove' else values, fields=delta,
                               candidates=candidates if operation == 'resolve' else []))
        current = self._options(connection)
        observed = dict(connection.execute('SELECT group_type, max_observed_level FROM technology'))
        names = {str(row['group_type']): row['name'] for row in tech.catalog()['ships']}
        for row in self._rows(connection, 'SELECT * FROM pending_technology ORDER BY group_type'):
            gid = row['group_type']
            before = dict(current.get('manual', {}).get(gid, {}), observed_level=observed.get(gid))
            after = self._manual(row)
            if row['max_observed_level'] is not None and row['max_observed_level'] > (observed.get(gid) or 0):
                after['observed_level'] = row['max_observed_level']
            fields = [field for field in after if field not in before or before[field] != after[field]]
            if fields:
                output.append(dict(id=f'technology:{gid}', kind='technology', operation='update', ship_id=None,
                                   group_type=int(gid), name=names.get(gid, gid), before=before,
                                   after=after, fields=fields, candidates=[]))
        nations = current.get('nations', {})
        for key, value in connection.execute('SELECT nation_key, level FROM pending_nations ORDER BY nation_key'):
            if key not in nations or nations[key] != value:
                output.append(dict(id=f'nation:{key}', kind='nation', operation='update', ship_id=None,
                                   group_type=None, name=key, before=dict(level=nations.get(key)),
                                   after=dict(level=value), fields=['level'], candidates=[]))
        return output

    def _stage(self, connection, rows, *, complete, observed, options, source, updated_at):
        for table in ('pending_ships', 'pending_technology', 'pending_nations'):
            connection.execute(f'DELETE FROM {table}')
        for proposal in ledger.proposals(self._cards(connection), rows, complete=complete):
            mask = sum(1 << ledger.CARD_FIELDS.index(field) for field in proposal['fields'])
            values = proposal['values']
            cursor = connection.execute('INSERT INTO pending_ships (operation, ship_id, fields, '
                               + ', '.join(ledger.CARD_FIELDS) + ') VALUES ('
                               + ', '.join('?' for _ in range(len(ledger.CARD_FIELDS) + 3)) + ')',
                               (proposal['operation'], proposal['ship_id'], mask,
                                *(values[field] for field in ledger.CARD_FIELDS)))
            connection.executemany('INSERT INTO pending_targets VALUES (?, ?)',
                                   ((cursor.lastrowid, target) for target in proposal.get('candidates', [])))
        for gid in observed.keys() | options.get('manual', {}).keys():
            self._write_technology(connection, 'pending_technology', gid, observed.get(gid),
                                   options.get('manual', {}).get(gid, {}))
        for key, value in options.get('nations', {}).items():
            ledger.integer(value, '阵营等级', nullable=True)
            connection.execute('INSERT INTO pending_nations VALUES (?, ?)', (key, value))
        has_changes = bool(self._differences(connection))
        connection.execute('UPDATE fleet_info SET pending_revision=?, pending_source=?, pending_updated_at=? WHERE id=1',
                           (tech.revision(uuid4().hex) if has_changes else None, source, updated_at))
        self._prune_pending(connection)

    def _prune_pending(self, connection):
        visible = {row['id'] for row in self._differences(connection)}
        for table, prefix, key in (('pending_ships', 'ship', 'id'), ('pending_technology', 'technology', 'group_type'),
                                   ('pending_nations', 'nation', 'nation_key')):
            for value, in connection.execute(f'SELECT {key} FROM {table}').fetchall():
                if f'{prefix}:{value}' not in visible:
                    connection.execute(f'DELETE FROM {table} WHERE {key}=?', (value,))
        connection.execute('UPDATE fleet_info SET pending_revision=? WHERE id=1', (tech.revision(uuid4().hex) if visible else None,))

    def save_scan(self, snapshot, expected_revision):
        """A completed reading stages differences, never overwrites the player's ledger."""
        metadata = {field: snapshot.get(field, default) for field, default in (
            ('min_level', 1), ('max_level', 125), ('reached_bottom', False), ('filters_clear', False),
            ('physical_count', len(snapshot['ships'])), ('expected_count', None), ('updated_at', ''))}
        ledger.integer(metadata['min_level'], '最低等级', 1, 125)
        ledger.integer(metadata['max_level'], '最高等级', metadata['min_level'], 125)
        ledger.integer(metadata['physical_count'], '卡片数量')
        ledger.integer(metadata['expected_count'], '船坞数量', nullable=True)
        for field in ('reached_bottom', 'filters_clear'):
            if type(metadata[field]) is not bool:
                raise ValueError('扫描完整性证明必须为布尔值')
        rows = snapshot['ships']
        derived = tech.merge_scan({}, rows, **metadata)
        with self._transaction() as connection:
            self._check_revision(connection, expected_revision)
            self._stage(connection, rows, complete=derived['last_scan']['complete'],
                        observed=derived['observed'], options={}, source='scan', updated_at=metadata['updated_at'])

    def stage_legacy(self, converted, expected_revision):
        rows = ledger.legacy_cards(converted)
        observed = copy.deepcopy((converted.get('snapshot') or {}).get('history', {}).get('observed', {}))
        for row in rows:
            if row.get('group_type') and not row.get('review') and 1 <= row.get('level', 0) <= 125:
                key = str(row['group_type'])
                observed[key] = max(observed.get(key, 0), row['level'])
        with self._transaction() as connection:
            self._check_revision(connection, expected_revision)
            if self._legacy_imported(connection):
                raise FleetDatabaseAlreadyImported('本实例旧数据已经读取')
            self._stage(connection, rows, complete=False, observed=observed, options=converted.get('technology', {}),
                        source='legacy', updated_at=self._now())
            connection.execute('UPDATE fleet_info SET imported_at=? WHERE id=1', (self._now(),))

    def review(self, decisions, expected_revision, pending_revision):
        with self._transaction() as connection:
            self._check_revision(connection, expected_revision)
            if pending_revision is None or self._info(connection)['pending_revision'] != pending_revision:
                raise FleetDatabaseConflict('待采纳数据已变化，请重新载入')
            differences = {row['id']: row for row in self._differences(connection)}
            seen, used_targets, accepted = set(), set(), False
            for decision in decisions:
                key = decision['id']
                if key in seen or key not in differences:
                    raise ValueError('差异不存在或重复')
                seen.add(key)
                if type(decision.get('accept')) is not bool:
                    raise ValueError('必须明确选择采纳或忽略')
                row = differences[key]
                if decision['accept']:
                    accepted = True
                    if row['kind'] == 'ship':
                        target = row['ship_id']
                        if row['operation'] == 'resolve':
                            if 'ship_id' not in decision:
                                raise ValueError('同名舰船需要选择现有内部序号或明确新增')
                            target = decision['ship_id']
                            if target is not None and target not in {item['id'] for item in row['candidates']}:
                                raise ValueError('选择的舰船记录不在候选范围内')
                        elif 'ship_id' in decision:
                            raise ValueError('只有需要匹配的差异才能指定目标')
                        if target is not None:
                            ledger.integer(target, '目标内部序号', 1)
                            if target in used_targets:
                                raise ValueError('不能把多张卡片更新到同一内部序号')
                            used_targets.add(target)
                            connection.execute('DELETE FROM pending_targets WHERE ship_id=?', (target,))
                        if row['operation'] == 'remove':
                            connection.execute('DELETE FROM ships WHERE id=?', (target,))
                        elif target is None:
                            self._insert_card(connection, ledger.card(row['after']))
                        else:
                            current = next((item for item in self._cards(connection) if item['id'] == target), None)
                            if current is None:
                                raise FleetDatabaseConflict('目标舰船已被修改，请重新载入')
                            pending = self._rows(connection, 'SELECT fields FROM pending_ships WHERE id=?', (int(key.split(':')[1]),))[0]
                            values = {field: row['after'][field] for bit, field in enumerate(ledger.CARD_FIELDS)
                                      if pending['fields'] & (1 << bit)}
                            self._update_card(connection, target, ledger.card(values, current))
                    elif row['kind'] == 'technology':
                        options = self._options(connection)
                        gid = str(row['group_type'])
                        entry = options.setdefault('manual', {}).setdefault(gid, {})
                        entry.update({field: row['after'][field] for field in row['fields'] if field != 'observed_level'})
                        if not entry:
                            options['manual'].pop(gid, None)
                        self._write_options(connection, 'technology', options)
                        if row['after'].get('observed_level'):
                            connection.execute('INSERT INTO technology (group_type, max_observed_level) VALUES (?, ?) '
                                               'ON CONFLICT(group_type) DO UPDATE SET max_observed_level='
                                               'MAX(COALESCE(technology.max_observed_level, 0), excluded.max_observed_level)',
                                               (gid, row['after']['observed_level']))
                    else:
                        connection.execute('INSERT INTO nation_technology VALUES (?, ?) ON CONFLICT(nation_key) '
                                           'DO UPDATE SET level=excluded.level', (row['name'], row['after']['level']))
                        connection.execute('UPDATE fleet_info SET technology_fields=technology_fields | 2 WHERE id=1')
                table, column = {'ship': ('pending_ships', 'id'), 'technology': ('pending_technology', 'group_type'),
                                 'nation': ('pending_nations', 'nation_key')}[row['kind']]
                connection.execute(f'DELETE FROM {table} WHERE {column}=?', (key.split(':', 1)[1],))
            if accepted:
                self._accumulate(connection)
                self._touch(connection)
            self._prune_pending(connection)
