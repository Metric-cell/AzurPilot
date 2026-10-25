"""共享船坞数据与扫描范围接口；浏览和配置不会启动游戏任务。"""
import json

from deploy.atomic import atomic_write
from module.api.protocol import ApiError
from module.config.transaction import config_transaction
from module.runtime import dock_data


class DockService:
    def __init__(self, configs):
        self.configs = configs

    def report(self, instance):
        data, _ = self.configs.read(instance)
        fields = data.get('DockScan', {}).get('Dock', {})
        report = dock_data.report_snapshot(data)
        # 控制项显示下一次扫描的配置，快照覆盖范围另附在 scan_range 中。
        return dict(report, instance=instance,
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
