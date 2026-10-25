"""旧心智扫描入口兼容；统一使用共享船坞扫描与同一份底账。"""
from module.retire.dock_scan import DockScan


class MindCalculatorScan(DockScan):
    """兼容旧任务名称，不再单独保存心智扫描结果。"""
