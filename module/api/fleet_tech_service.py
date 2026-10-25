"""舰队科技的实例隔离、事务编辑与心智清单同步接口。"""
from module.config.transaction import config_transaction
from module.api.protocol import ApiError
from module.runtime import fleet_tech as tech
from module.runtime import dock_data
from module.runtime.fleet_database import FleetDatabase


class FleetTechService:
    def __init__(self, configs):
        self.configs = configs

    def catalog(self, instance):
        self.configs.path(instance)
        return tech.public_catalog()

    def report(self, instance):
        data = FleetDatabase(self.configs.path(instance)).load(include_import_status=True)
        snapshot = dock_data.read_snapshot(data)
        fields = self.configs.read(instance)[0].get('DockScan', {}).get('Dock', {})
        min_level = snapshot['min_level'] if dock_data.has_snapshot(data) else fields.get('MinLevel', 1)
        max_level = snapshot['max_level'] if dock_data.has_snapshot(data) else fields.get('MaxLevel', 125)
        record = dock_data.technology_record(data)
        # last_scan 是已确认底账的有效群组投影，不以全图鉴或旧累计记录补齐成员。
        groups = [gid for gid, _ in (record.get('last_scan') or {}).get('ships', [])]
        return dict(instance=instance, min_level=min_level, max_level=max_level,
                    legacy_imported=data['legacy_imported'],
                    **tech.report(record, group_types=groups))

    def patch(self, instance, expected_revision, changes, nations):
        path = self.configs.path(instance)
        with self.configs.lock, config_transaction(path):
            self.configs.path(instance)
            database = FleetDatabase(path)
            with database.edit() as data:
                record = dock_data.technology_record(data)
                if tech.revision(record) != expected_revision:
                    raise ApiError('CONFLICT', '科技记录已更新，请重新载入后再编辑')
                known = {row['group_type'] for row in tech.catalog()['ships']}
                manual = record.setdefault('manual', {})
                seen = set()
                for change in changes:
                    gid = change.group_type
                    if gid not in known or gid in seen:
                        raise ApiError('INVALID_PARAMS', '舰船群组不存在或重复')
                    seen.add(gid)
                    key = str(gid)
                    if change.toggle is not None:
                        if (change.target is None or change.restore
                                or change.model_fields_set - {'group_type', 'restore', 'toggle', 'target'}):
                            raise ApiError('INVALID_PARAMS', '快捷切换不能同时指定人工值或恢复自动记录')
                        tech.toggle(record, gid, change.toggle, change.target)
                        continue
                    if 'target' in change.model_fields_set:
                        raise ApiError('INVALID_PARAMS', '快捷切换目标必须同时指定操作')
                    if change.restore:
                        if change.model_fields_set - {'group_type', 'restore'}:
                            raise ApiError('INVALID_PARAMS', '恢复自动记录不能同时指定人工值')
                        manual.pop(key, None)
                        continue
                    values = change.model_dump(exclude_unset=True, exclude={'group_type', 'restore', 'toggle', 'target'})
                    entry = manual.setdefault(key, {})
                    if 'level' in values or 'lv120' in values:
                        entry.pop('level_toggle', None)
                    entry.update(values)
                    # 设置任一正向里程碑也确认已获得，避免不一致的组合。
                    if values.get('level') or values.get('lv120') is True or values.get('full_star') is True:
                        if 'owned' in values and values['owned'] is not True:
                            raise ApiError('INVALID_PARAMS', '未获得不能同时设置已完成状态')
                        entry['owned'] = True
                    if 'level' in values and 'lv120' not in values:
                        entry.pop('lv120', None)
                    if values.get('owned') is False or ('owned' in values and values['owned'] is None):
                        entry.update(level=None, lv120=False if values['owned'] is False else None,
                                     full_star=False if values['owned'] is False else None)
                for key, value in nations.items():
                    if key not in tech.catalog()['nations'] or (value is not None and not 0 <= value <= len(tech.catalog()['nations'][key])):
                        raise ApiError('INVALID_PARAMS', '阵营科技等级无效')
                    record.setdefault('nations', {})[key] = value
                data['technology'] = {key: record[key] for key in ('manual', 'nations') if key in record}
        return self.report(instance)

    def sync_mind(self, instance):
        """保留旧接口为只读报告；两个分析已经共用数据库，不再复制数据。"""
        return self.report(instance)
