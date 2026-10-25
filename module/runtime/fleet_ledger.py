"""舰船底账的类型校验与待确认差异，不操作设备。"""
import copy
from collections import Counter

CARD_FIELDS = ('group_type', 'name', 'level', 'rarity', 'base_rarity', 'extra_exp', 'excluded', 'review', 'source')
MANUAL_FIELDS = ('owned', 'full_star', 'lv120', 'level', 'level_toggle')
RARITIES = ('', 'N', 'R', 'SR', 'SSR', 'UR')


def integer(value, label, low=0, high=None, *, nullable=False):
    if value is None and nullable:
        return None
    if type(value) is not int or value < low or (high is not None and value > high):
        raise ValueError(f'{label} 必须是有效整数')
    return value


def card(values, base=None):
    result = dict(group_type=None, name='', level=0, rarity='', base_rarity='', extra_exp=None,
                  excluded=False, review=False, source='')
    if base:
        result.update({key: base[key] for key in CARD_FIELDS if key in base})
    result.update({key: values[key] for key in CARD_FIELDS if key in values})
    if not isinstance(result['name'], str) or not 1 <= len(result['name']) <= 100:
        raise ValueError('舰船名称不能为空且不能超过 100 字')
    if result['group_type'] is None and not result['review']:
        from module.runtime import fleet_tech, mind_calculator
        info = mind_calculator.find_ship(result['name'])
        result['group_type'] = (info['group_type'] if info else
                                fleet_tech.catalog()['aliases'].get(mind_calculator.normalize_name(result['name'])))
    integer(result['group_type'], '舰船类型', 1, nullable=True)
    integer(result['level'], '等级', 0, 125)
    integer(result['extra_exp'], '零散经验', nullable=True)
    for key in ('rarity', 'base_rarity'):
        if result[key] not in RARITIES:
            raise ValueError('稀有度无效')
    for key in ('excluded', 'review'):
        if type(result[key]) is not bool:
            raise ValueError(f'{key} 必须是布尔值')
    if not isinstance(result['source'], str) or len(result['source']) > 200:
        raise ValueError('来源不能超过 200 字')
    return result


def observed_fields(raw):
    # No experience observation means leave an existing manual experience untouched.
    return tuple(key for key in CARD_FIELDS if key in raw)


def changed(before, after, fields):
    return [key for key in fields if key != 'source' and before.get(key) != after.get(key)]


def proposals(current, incoming, *, complete):
    """Exact multiset matches first; uncertain duplicate identity needs the player's choice."""
    remaining = {row['id']: row for row in current}
    waiting, output = [], []
    for raw in incoming:
        values = card(raw)
        fields = observed_fields(raw)
        gid = values['group_type'] if not values['review'] else None
        matches = [row for row in remaining.values() if gid is not None and row['group_type'] == gid
                   and not changed(row, values, fields)]
        if matches:
            del remaining[min(matches, key=lambda row: row['id'])['id']]
        else:
            waiting.append((values, fields, gid))
    counts = Counter(gid for _, _, gid in waiting if gid is not None)
    unresolved = set()
    for values, fields, gid in waiting:
        matches = [row for row in remaining.values() if gid is not None and row['group_type'] == gid]
        if len(matches) == 1 and counts[gid] == 1:
            target = matches[0]['id']
            del remaining[target]
            operation = 'update'
        elif matches:
            operation, target = 'resolve', None
            unresolved.add(gid)
        else:
            operation, target = 'add', None
        output.append(dict(operation=operation, ship_id=target, values=values, fields=fields,
                           candidates=[row['id'] for row in matches] if operation == 'resolve' else []))
    if complete:
        for row in remaining.values():
            if row['group_type'] is not None and row['group_type'] not in unresolved and not row['review']:
                output.append(dict(operation='remove', ship_id=row['id'], values=card(row), fields=()))
    return output


def legacy_cards(converted):
    """Fold old calculator edits into proposed ordinary card fields, never a shadow store."""
    from module.runtime import fleet_tech, mind_calculator, mind_analysis

    snapshot = copy.deepcopy(converted.get('snapshot'))
    rows = copy.deepcopy(snapshot.get('ships', [])) if snapshot else []
    aliases = fleet_tech.catalog()['aliases']
    for row in rows:
        row.setdefault('name', '未识别舰船')
        row.setdefault('group_type', aliases.get(mind_calculator.normalize_name(row['name'])))
    options = converted.get('mind', {})
    for key, override in options.get('overrides', {}).items():
        if key.startswith('group:'):
            gid = int(key.split(':')[1])
            matches = [row for row in rows if row.get('group_type') == gid]
            if matches:
                target = max(matches, key=lambda row: row.get('level', 0))
                target.update({field: value for field, value in override.items() if field in CARD_FIELDS})
    for key in options.get('removed', []):
        if key.startswith('group:'):
            gid = int(key.split(':')[1])
            for row in rows:
                if row.get('group_type') == gid:
                    row['excluded'] = True
    for raw in options.get('extra', []):
        row = copy.deepcopy(raw)
        info = mind_calculator.find_ship(row.get('name', ''))
        row['group_type'] = info['group_type'] if info else aliases.get(mind_calculator.normalize_name(row.get('name', '')))
        rows.append(row)
    # Review-specific fixes only bind to the exact old snapshot they were made against.
    if snapshot and options.get('snapshot_revision') == snapshot.get('revision'):
        for key, override in options.get('overrides', {}).items():
            if key.startswith('review:'):
                for row in rows:
                    if mind_analysis.ship_key(row) == key:
                        row.update({field: value for field, value in override.items() if field in CARD_FIELDS})
    return rows
