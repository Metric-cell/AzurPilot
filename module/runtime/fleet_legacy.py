"""Pure conversion for the user's explicit one-time local legacy-data import."""
import copy
from datetime import datetime
from functools import lru_cache

from module.config.deep import deep_get
from module.config.fleet_legacy import LEGACY_PATHS
from module.runtime import dock_data, fleet_tech as tech, mind_analysis, mind_calculator as mind


@lru_cache(maxsize=1)
def _known():
    return frozenset(str(row['group_type']) for row in tech.catalog()['ships'])


def _object(value, label):
    if not isinstance(value, dict):
        raise ValueError(f'{label} 必须为对象')
    return value


def _integer(value, low=1, high=125):
    return type(value) is int and low <= value <= high


def _history(value):
    value = _object(value, '历史记录')
    known = _known()
    result = {}
    for field in ('observed', 'current'):
        if field not in value:
            continue
        entries = _object(value[field], field)
        if any(gid not in known or not _integer(level) for gid, level in entries.items()):
            raise ValueError(f'{field} 包含无效群组或等级')
        result[field] = copy.deepcopy(entries)
    if 'absent' in value:
        if not isinstance(value['absent'], list) or any(str(gid) not in known for gid in value['absent']):
            raise ValueError('缺失记录包含无效群组')
        result['absent'] = [str(gid) for gid in value['absent']]
    return result


def _row(value):
    value = copy.deepcopy(_object(value, '舰船条目'))
    if not isinstance(value.get('name'), str) or not 1 <= len(value['name']) <= 100:
        raise ValueError('舰名无效')
    if not _integer(value.get('level'), 0, 125):
        raise ValueError('舰船等级无效')
    for field in ('excluded', 'review'):
        if field in value and type(value[field]) is not bool:
            raise ValueError(f'{field} 必须为布尔值')
    for field in ('rarity', 'base_rarity'):
        if field in value and value[field] not in ('', 'N', 'R', 'SR', 'SSR', 'UR'):
            raise ValueError('舰船稀有度无效')
    if 'source' in value and (not isinstance(value['source'], str) or len(value['source']) > 200):
        raise ValueError('舰船来源无效')
    if value.get('group_type') is not None and (type(value['group_type']) is not int
            or str(value['group_type']) not in _known()):
        raise ValueError('舰船群组无效')
    info = mind.find_ship(value['name'])
    if value.get('group_type') is None and info and not value.get('review'):
        value['group_type'] = info['group_type']
    value.setdefault('review', value.get('group_type') is None or value['level'] == 0)
    return value


def _rows(value):
    if not isinstance(value, list) or len(value) > 5000:
        raise ValueError('舰船清单必须为数组且不超过 5000 条')
    return [_row(row) for row in value]


def _options(record):
    manual, nations = record.get('manual', {}), record.get('nations', {})
    _object(manual, '人工科技')
    _object(nations, '阵营等级')
    public = tech.catalog()
    known = {str(row['group_type']) for row in public['ships']}
    allowed = {'owned', 'full_star', 'lv120', 'level', 'level_toggle'}
    for gid, entry in manual.items():
        if gid not in known or not isinstance(entry, dict) or entry.keys() - allowed:
            raise ValueError('人工科技字段或群组无效')
        for field, value in entry.items():
            if field in ('owned', 'full_star', 'lv120'):
                valid = value is None or type(value) is bool
            elif field == 'level':
                valid = value is None or _integer(value)
            else:
                valid = (isinstance(value, dict) and value.keys() == {'low', 'high'}
                         and _integer(value['low'], 1, 119) and _integer(value['high'], 120, 125))
            if not valid:
                raise ValueError('人工科技字段值无效')
    for nation, level in nations.items():
        if nation not in public['nations'] or not (level is None or _integer(level, 0, len(public['nations'][nation]))):
            raise ValueError('阵营科技等级无效')
    return copy.deepcopy(dict(manual={gid: entry for gid, entry in manual.items() if entry}, nations=nations))


def _merge_options(target, source):
    for gid, values in source.get('manual', {}).items():
        existing = target.setdefault('manual', {}).setdefault(gid, {})
        grades = {'level', 'lv120', 'level_toggle'}
        preserve_grade = bool(grades & existing.keys())
        for field, value in values.items():
            if not (preserve_grade and field in grades):
                existing.setdefault(field, copy.deepcopy(value))
    for nation, value in source.get('nations', {}).items():
        target.setdefault('nations', {}).setdefault(nation, value)


def _snapshot(rows, original, source, *, dock=False):
    low, high = original.get('min_level', 1), original.get('max_level', 125)
    if not _integer(low) or not _integer(high) or low > high:
        raise ValueError('旧扫描等级范围无效')
    updated = original.get('updated_at', '')
    if not isinstance(updated, str):
        raise ValueError('旧扫描时间无效')
    for field in ('reached_bottom', 'filters_clear', 'complete'):
        if field in original and type(original[field]) is not bool:
            raise ValueError('旧扫描证明无效')
    if dock:
        for field in ('physical_count', 'expected_count'):
            value = original.get(field)
            if value is not None and not _integer(value, 0, 5000):
                raise ValueError('旧扫描数量无效')
    result = dock_data.build_snapshot(rows, min_level=low, max_level=high,
        reached_bottom=original.get('reached_bottom', False) if dock else False,
        filters_clear=original.get('filters_clear', False) if dock else False,
        physical_count=original.get('physical_count', len(rows)) if dock else len(rows),
        expected_count=original.get('expected_count') if dock else None, updated_at=updated)
    result['import_source'] = source
    if isinstance(original.get('revision'), str):
        result['legacy_revision'] = original['revision']
    original_history = _history(original.get('history', {}))
    observed = result['history']['observed'].copy()
    result['history'].update(original_history)
    for gid, level in observed.items():
        result['history'].setdefault('observed', {})[gid] = max(result['history']['observed'].get(gid, 0), level)
    return result


def _timestamp(snapshot):
    try:
        return datetime.fromisoformat(snapshot['updated_at']).timestamp()
    except (ValueError, OverflowError, OSError):
        return float('-inf')


def _revision(snapshot):
    snapshot.pop('revision', None)
    snapshot['revision'] = tech.revision(snapshot)
    return snapshot


def convert_legacy(data):
    """Convert supplied local data into one current ledger and independent settings."""
    _object(data, '旧配置')
    sources = {}
    for path in LEGACY_PATHS:
        value = deep_get(data, path, {})
        _object(value, path)
        if value:
            sources[path] = copy.deepcopy(value)
    snapshots, technology, observed = [], {}, {}
    records = [sources[path] for path in (LEGACY_PATHS[4], LEGACY_PATHS[3]) if path in sources]
    for record in records:
        _merge_options(technology, _options(record))
        history = _history(record)
        for field in ('observed', 'current'):
            for gid, level in history.get(field, {}).items():
                observed[gid] = max(observed.get(gid, 0), level)
        if 'last_scan' in record:
            scan = _object(record['last_scan'], '旧科技扫描')
            if not isinstance(scan.get('ships', []), list):
                raise ValueError('旧科技扫描清单无效')
            rows = []
            catalog = {row['group_type']: row for row in tech.catalog()['ships']}
            for item in scan.get('ships', []):
                if (not isinstance(item, (list, tuple)) or len(item) != 2 or type(item[0]) is not int
                        or item[0] not in catalog or not _integer(item[1])):
                    raise ValueError('旧科技群组等级无效')
                info = catalog[item[0]]
                rarity = {'PR': 'SSR', 'DR': 'UR'}.get(info['rarity'], info['rarity'])
                rows.append(dict(name=info['name'], group_type=item[0], level=item[1], rarity=rarity,
                                 base_rarity=rarity, review=False, source='旧科技扫描'))
            rows.extend(_rows(record.get('review_ships', [])))
            result = _snapshot(rows, scan, 'legacy_technology')
            for field in ('observed', 'current'):
                for gid, level in result['history'].get(field, {}).items():
                    observed[gid] = max(observed.get(gid, 0), level)
            result['history'].update(history)
            snapshots.append(result)
        elif history:
            result = _snapshot([], record, 'legacy_technology_history')
            for field in ('observed', 'current'):
                for gid, level in result['history'].get(field, {}).items():
                    observed[gid] = max(observed.get(gid, 0), level)
            result['history'].update(history)
            snapshots.append(result)
    saved = sources.get(LEGACY_PATHS[1], {})
    mind_rows = None
    if 'ships' in saved:
        mind_rows = _rows(saved['ships'])
        snapshots.append(_snapshot(mind_rows, saved, 'legacy_mind'))
    snapshots.sort(key=_timestamp)
    dock = sources.get(LEGACY_PATHS[0])
    if dock:
        if 'ships' not in dock:
            raise ValueError('旧共享船坞缺少舰船数组')
        snapshots.append(_snapshot(_rows(dock['ships']), dock, 'legacy_dock', dock=True))
    for snapshot in snapshots:
        for gid, level in snapshot['history'].get('observed', {}).items():
            observed[gid] = max(observed.get(gid, 0), level)
    if snapshots:
        snapshots[-1]['history'].setdefault('observed', {}).update(observed)
        _revision(snapshots[-1])
    state = dict(snapshot=snapshots[-1] if snapshots else None, mind={}, technology={})
    analysis = sources.get(LEGACY_PATHS[2])
    if analysis:
        _object(analysis.get('overrides', {}), '心智覆盖')
        for key, values in analysis.get('overrides', {}).items():
            if not isinstance(key, str) or not isinstance(values, dict) or values.keys() - set(mind_analysis.FIELDS):
                raise ValueError('心智覆盖字段无效')
            _row({'name': '待核对舰船', 'level': 1, **values})
        if not isinstance(analysis.get('removed', []), list) or any(not isinstance(key, str) for key in analysis.get('removed', [])):
            raise ValueError('心智删除记录无效')
        _rows(analysis.get('extra', []))
        options = {key: copy.deepcopy(analysis[key])
                   for key in ('overrides', 'removed', 'extra', 'updated_at', 'snapshot_revision')
                   if key in analysis}
        if (snapshots and options.get('snapshot_revision')
                and options['snapshot_revision'] == snapshots[-1].get('legacy_revision')):
            options['snapshot_revision'] = snapshots[-1]['revision']
    elif mind_rows is not None:
        submitted = [{field: row.get(field, '' if field in ('rarity', 'base_rarity', 'source') else False)
                      for field in mind_analysis.FIELDS} for row in mind_rows]
        options = mind_analysis.save_settings(state, submitted, saved.get('updated_at', ''))
    else:
        options = {}
    useful_mind = any(options.get(key) for key in ('overrides', 'removed', 'extra'))
    if not snapshots and not useful_mind and not any(technology.get(key) for key in ('manual', 'nations')):
        raise ValueError('当前配置中没有可导入的旧舰船数据')
    return dict(snapshot=state['snapshot'], mind=options, technology=technology)


def merge_legacy(state, converted):
    """Merge missing settings and positive evidence; keep the current physical snapshot active."""
    result = copy.deepcopy(state)
    _merge_options(result.setdefault('technology', {}), converted['technology'])
    current = result.setdefault('mind', {})
    incoming = converted['mind']
    original_extras = copy.deepcopy(current.get('extra', []))
    original_overrides = copy.deepcopy(current.get('overrides', {}))
    source_snapshot = converted['snapshot']
    previous_snapshot = result.get('snapshot')
    valid_review_source = bool(source_snapshot and incoming.get('snapshot_revision') == source_snapshot['revision'])
    if previous_snapshot:
        original_revision = source_snapshot.get('legacy_revision') if source_snapshot else None
        same_source = bool(source_snapshot and (previous_snapshot.get('revision') == source_snapshot['revision']
            or original_revision and original_revision in (previous_snapshot.get('revision'), previous_snapshot.get('legacy_revision'))))
        current_binding = current.get('snapshot_revision')
        valid_review_source = valid_review_source and same_source and current_binding in (None, previous_snapshot['revision'])
    removed = set(current.get('removed', []))
    preferred = set(current.get('overrides', {})) | {key for key, _ in mind_analysis.keyed(current.get('extra', []))}
    for key, value in incoming.items():
        if key == 'overrides':
            for ship, fields in value.items():
                if ship in removed or ship.startswith('review:') and not valid_review_source:
                    continue
                target = current.setdefault(key, {}).setdefault(ship, {})
                for field, item in fields.items():
                    target.setdefault(field, copy.deepcopy(item))
        elif key in ('extra', 'removed'):
            target = current.setdefault(key, [])
            if key == 'extra':
                existing = {ship for ship, _ in mind_analysis.keyed(target)}
                target.extend(copy.deepcopy(row) for ship, row in mind_analysis.keyed(value) if ship not in existing and ship not in removed)
            else:
                target.extend(item for item in value if item not in target and item not in preferred
                              and (not item.startswith('review:') or valid_review_source))
        else:
            current.setdefault(key, copy.deepcopy(value))
    active = result.get('snapshot')
    if active:
        active = copy.deepcopy(active)
        observed = active.setdefault('history', {}).setdefault('observed', {})
        if source_snapshot:
            for gid, level in source_snapshot.get('history', {}).get('observed', {}).items():
                observed[gid] = max(observed.get(gid, 0), level)
        old_revision = active.get('revision')
        _revision(active)
        if (current.get('snapshot_revision') == old_revision
                or valid_review_source and current.get('snapshot_revision') == incoming.get('snapshot_revision')):
            current['snapshot_revision'] = active['revision']
    elif source_snapshot:
        active = copy.deepcopy(source_snapshot)
    result['snapshot'] = active
    # Manual extra rows predate the imported base and keep their effective values.
    base = dict(mind_analysis.keyed(mind_analysis.base_ships(result)))
    for (key, _), row in zip(mind_analysis.keyed(original_extras), original_extras):
        if not key.startswith('group:') or key not in base:
            continue
        target = current.setdefault('overrides', {}).setdefault(key, {})
        for field in mind_analysis.FIELDS:
            if field in row and row[field] != base[key].get(field):
                if field not in original_overrides.get(key, {}):
                    target[field] = copy.deepcopy(row[field])
    result['legacy_snapshot'] = active
    return result
