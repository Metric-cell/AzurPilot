"""WebUI调试工具和远程访问"""

from deploy.atomic import atomic_write
from module.logger import logger

from module.webui.app_dependencies import (
    Optional,
    ProcessManager,
    State,
    alas_instance,
    load_config,
    put_button,
    put_buttons,
    put_scope,
    raise_exception,
    t,
    toast,
    use_scope,
)
from module.webui.app_lifecycle import clearup


from module.webui.app_types import WebUIMixinBase


def prepare_webui_restart() -> bool:
    """保存当前运行实例，供新 WebUI 在重启后恢复。"""
    try:
        names = [
            f"{alas.config_name}\n" for alas in ProcessManager.running_instances()
        ]
        atomic_write("./config/reloadalas", "".join(names))
    except Exception as exc:
        logger.exception_context(
            title='无法准备 WebUI 手动重启',
            exc=exc,
            impact='继续重启会导致当前运行的 AzurPilot 实例无法自动恢复。',
            action='检查 config 目录写入权限后重试。',
            level=50,
        )
        return False
    return True


def request_webui_restart() -> bool:
    """请求手动重启，且不打断正在执行的更新事务。"""
    if State.restart_event is None:
        return False
    if not State.restart_lock.acquire(blocking=False):
        logger.info("自动更新事务正在进行，忽略本次手动重启请求")
        return False

    try:
        if State._restart_requested:
            return True
        if not prepare_webui_restart():
            return False

        State._restart_requested = True
        try:
            if not clearup():
                logger.warning("WebUI 清理未完成，将由父进程终止完整进程树")
        except Exception as exc:
            logger.exception_context(
                title='WebUI 手动重启清理失败',
                exc=exc,
                impact='父进程仍会终止旧 WebUI 进程树。',
                action='检查 WebUI 清理日志，确认是否有残留资源。',
                level=50,
            )

        try:
            State.restart_event.set()
        except Exception as exc:
            State._restart_requested = False
            logger.exception_context(
                title='无法通知父进程执行 WebUI 手动重启',
                exc=exc,
                impact='当前 WebUI 不会退出，已保存的实例恢复标记将保留。',
                action='检查父子进程事件状态后重新发起重启。',
                level=50,
            )
            return False
        return True
    finally:
        State.restart_lock.release()


class DeveloperToolsMixin(WebUIMixinBase):
    """WebUI调试工具和远程访问"""

    @use_scope("content", clear=True)
    def dev_utils(self) -> None:
        self.init_menu(name="Utils")
        self.set_title(t("Gui.MenuDevelop.Utils"))
        put_scope("develop_detail")
        put_button(
            label=t("GUI测试 抛出异常事件"),
            onclick=raise_exception,
            scope="develop_detail",
        )
        def _get_debug_target_instance() -> Optional[str]:
            if getattr(self, "alas_name", ""):
                return self.alas_name
            all_instances = alas_instance()
            if all_instances:
                return all_instances[0]
            return None

        def _refresh_debug_status():
            self.set_aside_status()
            if hasattr(self, "state_switch"):
                try:
                    self.state_switch.switch()
                except Exception:
                    pass

        def _mock_icon_state(state: int, seconds: int = 10):
            target = _get_debug_target_instance()
            if not target:
                toast("未找到可用实例，无法模拟图标状态", color="warning")
                return
            ProcessManager.get_manager(target).set_state_override(
                state, duration=seconds
            )
            _refresh_debug_status()
            toast(f"已为 {target} 模拟状态 {state}（{seconds}s）", color="info")

        def _clear_mock_icon_state():
            target = _get_debug_target_instance()
            if not target:
                toast("未找到可用实例，无法清除模拟状态", color="warning")
                return
            ProcessManager.get_manager(target).clear_state_override()
            _refresh_debug_status()
            toast(f"已清除 {target} 的图标状态模拟", color="success")

        put_buttons(
            buttons=[
                {"label": "模拟运行图标(10s)", "value": 1, "color": "success"},
                {"label": "模拟错误图标(10s)", "value": 3, "color": "danger"},
                {"label": "模拟更新图标(10s)", "value": 4, "color": "warning"},
            ],
            onclick=lambda state: _mock_icon_state(state, 10),
            scope="develop_detail",
        )
        put_button(
            label="清除图标模拟状态",
            onclick=_clear_mock_icon_state,
            color="secondary",
            scope="develop_detail",
        )

        def _force_restart():
            if State.restart_event is None:
                toast(t("Gui.Toast.ReloadEnabled"), color="error")
                return
            if request_webui_restart():
                toast(t("Gui.Toast.AlasRestart"), duration=0, color="error")
            else:
                toast("自动更新正在进行或无法保存运行实例，已取消重启", color="error")

        put_button(label=t("重启Alas"), onclick=_force_restart, scope="develop_detail")

        def _test_notify_error():
            from module.notify import handle_notify

            instance = _get_debug_target_instance()
            if not instance:
                toast("未找到可用实例，无法发送错误推送测试", color="warning")
                return
            config = load_config(instance)
            success = handle_notify(
                config.Error_OnePushConfig,
                title=f"AzurPilot <{instance}> 崩溃",
                content=f"<{instance}> 开发者错误推送测试",
            )
            if success:
                toast("已发送错误推送测试", color="success")
            else:
                toast("错误推送测试发送失败，请检查错误推送设置", color="error")

        put_buttons(
            buttons=[
                {
                    "label": "测试错误推送",
                    "value": "error",
                    "color": "danger",
                },
            ],
            onclick=[_test_notify_error],
            scope="develop_detail",
        )
