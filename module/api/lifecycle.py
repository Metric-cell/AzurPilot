"""运行服务生命周期管理模块。

管理与界面会话解耦的后端服务生命周期，包含定时任务处理器及调度器工作进程的启动与清理。
"""

from module.logger import logger
from module.runtime.process_manager import ProcessManager
from module.runtime.setting import State
from module.runtime.startup_memory import record_running
from module.runtime.task_handler import TaskHandler

task_handler = TaskHandler()


def startup(runs=None):
    """初始化共享进程登记与可选后台服务。

    启动指定实例的调度器进程。

    Args:
        runs (list[str], optional): 随 WebUI 启动自动运行的实例名称列表。默认为 None。
    """
    State.init()
    task_handler.start()
    ProcessManager.restart_processes(instances=runs, ev=None)


def clearup():
    """逐项停止服务，即使某项失败也继续回收其他工作进程。

    依次停止定时任务及所有活跃的调度器实例，
    并将运行状态记录到启动记忆中。

    Returns:
        bool: 全部服务成功回收返回 True，存在异常或残留返回 False。
    """
    with State.cleanup_lock:
        if State._clearup:
            return True
        actions = [task_handler.stop]
        success = True
        try:
            running = ProcessManager.running_instances()
            actions.extend(instance.stop for instance in running)
            record_running(instance.config_name for instance in running)
        except Exception:
            logger.exception('无法枚举运行进程，保留共享状态供父监督器回收')
            success = False
        for action in actions:
            try:
                success = (action() is not False) and success
            except Exception:
                logger.exception('运行服务资源回收失败')
                success = False
        if success:
            State.clearup()
        return success
