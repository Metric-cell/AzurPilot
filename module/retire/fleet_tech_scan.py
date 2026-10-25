"""旧舰船科技扫描入口兼容；分析与扫描数据采集独立。"""
from module.retire.dock_scan import DockScan


class FleetTechScan(DockScan):
    """兼容旧任务名称，统一写入共享船坞数据。"""
