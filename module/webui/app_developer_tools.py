"""WebUI调试工具和远程访问"""

from module.webui.app_dependencies import (
    DEFAULT_CONFIG_NAME,
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
        put_button(
            label=t("预览更新提示"),
            onclick=self._preview_update_notice,
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
            if State.restart_event is not None:
                toast(t("Gui.Toast.AlasRestart"), duration=0, color="error")
                clearup()
                State.restart_event.set()
            else:
                toast(t("Gui.Toast.ReloadEnabled"), color="error")

        put_button(label=t("重启Alas"), onclick=_force_restart, scope="develop_detail")

        def _test_notify_update():
            from module.notify.notify import notify_webui

            instance = getattr(self, "alas_name", DEFAULT_CONFIG_NAME)
            notify_webui(
                instance=instance,
                title="发现更新喵！",
                content="测试更新推送逻辑，启动器应显示专用标题。",
                update=True,
            )
            toast("已发送更新测试通知", color="success")

        def _test_notify_announcement():
            from module.notify.notify import notify_webui

            instance = getattr(self, "alas_name", DEFAULT_CONFIG_NAME)
            notify_webui(
                instance=instance,
                title="新公告喵！",
                content="测试公告推送逻辑，启动器应显示专用标题。",
                updata=False,
            )
            toast("已发送公告测试通知", color="info")

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
                    "label": "测试更新推送 (updata=True)",
                    "value": "update",
                    "color": "danger",
                },
                {
                    "label": "测试公告推送 (updata=False)",
                    "value": "announcement",
                    "color": "info",
                },
                {
                    "label": "测试错误推送",
                    "value": "error",
                    "color": "danger",
                },
            ],
            onclick=[
                _test_notify_update,
                _test_notify_announcement,
                _test_notify_error,
            ],
            scope="develop_detail",
        )
