"""共享船坞数据与扫描范围接口；浏览和配置不会启动游戏任务。"""
import json

from deploy.atomic import atomic_write
from module.api.protocol import ApiError
from module.config.transaction import config_transaction
from module.config.deep import deep_pop
from module.config.fleet_legacy import LEGACY_PATHS
from module.runtime import dock_data
from module.runtime.fleet_database import FleetDatabase, FleetDatabaseAlreadyImported
from module.logger import logger


class DockService:
    def __init__(self, configs):
        self.configs = configs

    def report(self, instance):
        data, _ = self.configs.read(instance)
        fields = data.get('DockScan', {}).get('Dock', {})
        state = FleetDatabase(self.configs.path(instance)).load(include_import_status=True)
        report = dock_data.report_snapshot(state)
        # 控制项显示下一次扫描的配置，快照覆盖范围另附在 scan_range 中。
        return dict(report, instance=instance, legacy_imported=state['legacy_imported'],
                    scan_range=dict(min_level=report['min_level'], max_level=report['max_level']),
                    min_level=fields.get('MinLevel', 1), max_level=fields.get('MaxLevel', 125))

    def configure(self, instance, min_level, max_level):
        if not 1 <= min_level <= max_level <= 125:
            raise ApiError('INVALID_PARAMS', '扫描等级范围必须满足 1 ≤ 最低等级 ≤ 最高等级 ≤ 125')
        with self.configs.lock, config_transaction(self.configs.path(instance)):
            data, _ = self.configs.read(instance)
            fields = data.setdefault('DockScan', {}).setdefault('Dock', {})
            fields.update(MinLevel=min_level, MaxLevel=max_level)
            atomic_write(str(self.configs.path(instance)), json.dumps(data, ensure_ascii=False, indent=2))
            return self.report(instance)

    def import_legacy(self, instance, expected_revision):
        """用户显式迁移当前实例的本地旧 JSON；正常读取从不调用此入口。"""
        from module.runtime.fleet_legacy import convert_legacy, merge_legacy

        path = self.configs.path(instance)
        with self.configs.lock, config_transaction(path):
            self.configs.path(instance)
            database = FleetDatabase(path)
            try:
                with database.edit(import_legacy=True) as state:
                    if dock_data.read_snapshot(state)['revision'] != expected_revision:
                        raise ApiError('CONFLICT', '舰船数据库已更新，请重新载入后迁移')
                    # 仅迁移按钮读取原文件，不经过模板或旧字段回退。
                    original = json.loads(path.read_text(encoding='utf-8-sig'))
                    converted = convert_legacy(original)
                    state.update(merge_legacy(state, converted))
            except FleetDatabaseAlreadyImported as exc:
                raise ApiError('ALREADY_IMPORTED', '本实例已完成旧数据迁移') from exc
            except (ValueError, TypeError, KeyError) as exc:
                raise ApiError('INVALID_PARAMS', f'旧舰船数据无法迁移：{exc}') from exc
            # 先提交包含原始旧内容归档的 SQLite，再清理配置；清理失败也不会丢数据。
            for key in LEGACY_PATHS:
                deep_pop(original, key)
            try:
                atomic_write(str(path), json.dumps(original, ensure_ascii=False, indent=2))
            except OSError as exc:
                logger.warning(f'舰船数据已迁移，旧配置内容保留为备份：{exc}')
        return self.report(instance)
