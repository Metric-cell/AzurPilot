"""WebUIASGI生命周期管理"""

from module.webui.app_dependencies import (
    ProcessManager,
    State,
    lang,
    logger,
    start_ocr_server_process,
    stop_ocr_server_process,
    task_handler,
)

from module.webui.app_helpers import (
    is_demo_mode,
)


def startup() -> None:
    """初始化 WebUI 进程级后台服务。"""
    State.init()
    lang.reload()
    task_handler.start()
    if State.deploy_config.StartOcrServer and not is_demo_mode():
        start_ocr_server_process(State.deploy_config.OcrServerPort)


def clearup() -> None:
    """停止 WebUI 进程级资源，避免热重载遗留子进程。"""
    logger.info("Start clearup")
    stop_ocr_server_process()
    for alas in ProcessManager._processes.values():
        alas.stop()
    State.clearup()
    task_handler.stop()
    logger.info("Alas closed.")
