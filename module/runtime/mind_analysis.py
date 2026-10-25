"""心智分析的人工覆盖；不修改共享识别结果或舰船科技记录。"""
import copy

from module.runtime import dock_data
from module.runtime import mind_calculator as mind

FIELDS = ('name', 'level', 'rarity', 'base_rarity', 'excluded', 'review', 'source')


def ship_key(ship):
    info = mind.find_ship(ship['name'])
    if info and not ship.get('review'):
        return f'group:{info["group_type"]}'
    # 未确认身份不跨次继承卡位，避免排序变化把校正应用到其他船。
    return f'review:{mind.revision([ship.get("name"), ship.get("source")])}'


def keyed(ships):
    counts = {}
    for ship in ships:
        key = ship_key(ship)
        count = counts.get(key, 0)
        counts[key] = count + 1
        yield (f'{key}:{count}' if count else key), {field: ship.get(field) for field in FIELDS}


def base_ships(data):
    return dock_data.mind_ships(dock_data.read_snapshot(data))


def settings(data):
    """仅使用数据库中的人工分析设置，不继承旧任务结果。"""
    return copy.deepcopy(data.get('mind') or dict(overrides={}, removed=[], extra=[]))


def ships(data):
    options = settings(data)
    snapshot_revision = dock_data.read_snapshot(data)['revision']
    output = []
    for key, ship in keyed(base_ships(data)):
        applicable = not key.startswith('review:') or options.get('snapshot_revision') == snapshot_revision
        if applicable and key in options.get('removed', []):
            continue
        if applicable:
            ship.update(options.get('overrides', {}).get(key, {}))
        output.append(ship)
    existing = {key for key, _ in keyed(output)}
    output.extend(copy.deepcopy(ship) for key, ship in keyed(options.get('extra', [])) if key not in existing)
    return output


def revision(data):
    rows = ships(data)
    options = settings(data)
    options.pop('updated_at', None)
    return mind.revision([dock_data.read_snapshot(data)['revision'], rows, options])


def save_settings(data, submitted, updated_at):
    """只保存相对底账的差异；下一次扫描自动更新未人工覆盖的等级。"""
    base = dict(keyed(base_ships(data)))
    incoming = list(keyed(submitted))
    previous = settings(data)
    overrides = copy.deepcopy(previous.get('overrides', {}))
    for key in base:
        overrides.pop(key, None)
    extra, present = [], set()
    for key, ship in incoming:
        if key in base:
            present.add(key)
            delta = {field: value for field, value in ship.items() if value != base[key].get(field)}
            if delta:
                overrides[key] = delta
        else:
            extra.append(ship)
    removed = (set(previous.get('removed', [])) - base.keys()) | (base.keys() - present)
    return dict(overrides=overrides, removed=sorted(removed), extra=extra, updated_at=updated_at,
                snapshot_revision=dock_data.read_snapshot(data)['revision'])
