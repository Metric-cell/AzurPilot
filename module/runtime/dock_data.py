"""独立舰船数据库的纯分析投影；不读取旧任务配置中的舰船数据。"""
import copy
from module.runtime import fleet_tech as tech
from module.runtime import mind_calculator as mind

HISTORY_FIELDS = ('observed', 'current', 'absent')


def has_snapshot(data):
    value = data.get('snapshot')
    return isinstance(value, dict) and isinstance(value.get('ships'), list)


def _history(record):
    return {key: copy.deepcopy(record[key]) for key in HISTORY_FIELDS if key in record}


def build_snapshot(ships, *, min_level, max_level, reached_bottom, filters_clear,
                   physical_count, expected_count, updated_at, previous=None):
    """保留每个物理副本，完整性与科技扫描的缺失保护使用同一规则。"""
    if (type(min_level) is not int or type(max_level) is not int
            or not 1 <= min_level <= max_level <= 125):
        raise ValueError('扫描等级范围必须满足 1 ≤ 最低等级 ≤ 最高等级 ≤ 125')
    rows = copy.deepcopy(ships)
    for index, row in enumerate(rows):
        for field in ('excluded', 'automatic_excluded', 'status', 'mind', 'gold'):
            row.pop(field, None)
        row.setdefault('slot', index)
    merged = tech.merge_scan((previous or {}).get('history', {}), rows, min_level=min_level,
                             max_level=max_level, reached_bottom=reached_bottom,
                             filters_clear=filters_clear, physical_count=physical_count,
                             expected_count=expected_count, updated_at=updated_at)
    scan = merged['last_scan']
    output = dict(version=1, ships=rows, updated_at=updated_at, min_level=min_level,
                  max_level=max_level, reached_bottom=bool(reached_bottom),
                  filters_clear=bool(filters_clear), physical_count=physical_count,
                  expected_count=expected_count, complete=scan['complete'],
                  review_count=scan['review_count'], history=_history(merged), source='scan')
    output['revision'] = tech.revision(output)
    return output


def read_snapshot(data):
    """仅接受数据库快照；无扫描时返回空状态，不回退到配置 JSON。"""
    if has_snapshot(data):
        output = copy.deepcopy(data['snapshot'])
        output['source'] = 'scan'
        output.setdefault('revision', tech.revision(output))
        return output
    return dict(version=1, ships=[], updated_at='', min_level=1, max_level=125,
                reached_bottom=False, filters_clear=False, physical_count=0,
                expected_count=None, complete=False, review_count=0,
                history={}, revision=tech.revision({}), source='empty')


def mind_ships(snapshot):
    """把已确认群组映射为心智限定名；歧义卡位仍独立留待核对。"""
    by_group = {}
    for info in mind.catalog()['ships'].values():
        by_group.setdefault(info['group_type'], []).append(info)
    output = []
    for row in snapshot.get('ships', []):
        level = row.get('level', 0)
        if (type(level) is int and 1 <= level <= 125 and not row.get('review')
                and not snapshot.get('min_level', 1) <= level <= snapshot.get('max_level', 125)):
            continue
        matches = by_group.get(row.get('group_type'), [])
        info = next((item for item in matches if item['raw_name'] == row.get('name')), None)
        if info is None and matches:
            info = next((item for item in matches if item['group'] != '改造'), matches[0])
        name = info['name'] if info else row.get('name', '未识别舰船')
        level = row.get('level', 0)
        valid_level = type(level) is int and 1 <= level <= 125
        output.append(dict(name=name[:100], level=level if valid_level else 0,
                           rarity=info['rarity'] if info else row.get('rarity', ''),
                           base_rarity=info['base_rarity'] if info else row.get('base_rarity', ''),
                           excluded=False, review=bool(row.get('review')) or not info or not valid_level,
                           source=row.get('source', '')[:200]))
    return mind.highest_ships(output)


def technology_record(data):
    """合并数据库中的扫描证据与独立科技设置，不改写来源。"""
    record = tech.read_record(data)
    if not has_snapshot(data):
        return record
    snapshot = read_snapshot(data)
    record['snapshot_revision'] = snapshot['revision']
    history = snapshot.get('history', {})
    for gid, level in history.get('observed', {}).items():
        observed = record.setdefault('observed', {})
        observed[gid] = max(observed.get(gid, 0), level)
    record.setdefault('current', {}).update(history.get('current', {}))
    positive = set(history.get('current', {})) - set(history.get('absent', []))
    record['absent'] = sorted((set(record.get('absent', [])) | set(history.get('absent', []))) - positive, key=int)
    record = tech.merge_scan(record, snapshot['ships'], **{key: snapshot[key] for key in
                             ('min_level', 'max_level', 'reached_bottom', 'filters_clear',
                              'physical_count', 'expected_count', 'updated_at')})
    # 科技成就不会因退役或本轮只剩低级副本而消失；原始船坞缺失证据留在快照中。
    # 合并自动正向历史后再由 progress() 应用人工覆盖（包括显式否定）。
    record['current'] = {gid: max(level, record.get('current', {}).get(gid, 0))
                         for gid, level in record.get('observed', {}).items()}
    record['absent'] = []
    return record


def report_snapshot(data):
    snapshot = read_snapshot(data)
    return {key: snapshot[key] for key in ('revision', 'updated_at', 'min_level', 'max_level',
                                          'ships', 'complete', 'review_count', 'source')}
