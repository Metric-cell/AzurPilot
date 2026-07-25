"""WebUI首页和会话运行"""

from module.webui.app_dependencies import (
    Switch,
    _t,
    alas_instance,
    get_localstorage,
    get_window_visibility_state,
    go_app,
    is_oobe_needed,
    lang,
    load_webui_styles,
    put_buttons,
    put_html,
    put_markdown,
    put_text,
    register_thread,
    run_js,
    set_env,
    set_localstorage,
    t,
    threading,
    toast,
    use_scope,
)


from module.webui.app_types import WebUIMixinBase


class HomeMixin(WebUIMixinBase):
    """WebUI首页和会话运行"""

    def show(self) -> None:
        self.mount_shell()
        self.show_home()

    def show_home(self) -> None:
        self.mount_shell()
        self._set_manage_mode(False)
        self._active_aside = "Home"
        self.init_aside(name="Home")
        self.dev_set_menu()
        self.init_menu(name="HomePage")
        self.set_title(t("Gui.MenuDevelop.HomePage"))
        self.alas_name = ""
        if hasattr(self, "alas"):
            del self.alas
        self.set_status(0)

        def set_language(l):
            lang.set_language(l)
            self.show_home()
            self.refresh_aside_labels()

        def set_theme(t):
            self.set_theme(t)
            set_localstorage("aside", "Home")
            go_app("index", new_window=False)

        with use_scope("content"):
            put_text("Select your language / 选择语言").style(
                "text-align: center; font-weight: 600"
            )
            put_buttons(
                [
                    {"label": "简体中文", "value": "zh-CN"},
                    {"label": "喵体中文", "value": "zh-MIAO"},
                    {"label": "繁體中文", "value": "zh-TW"},
                    {"label": "English", "value": "en-US"},
                    {"label": "日本語", "value": "ja-JP"},
                ],
                onclick=lambda l: set_language(l),
            ).style("text-align: center")
            put_text("Change theme / 更改主题").style("text-align: center")
            put_buttons(
                [
                    {"label": "Light", "value": "default", "color": "light"},
                    {"label": "Dark", "value": "dark", "color": "dark"},
                    {
                        "label": "高级材质",
                        "value": "advanced_material",
                        "color": "primary",
                    },
                    {
                        "label": "高级材质（暗色）",
                        "value": "dark_advanced_material",
                        "color": "dark",
                    },
                ],
                onclick=lambda t: set_theme(t),
            ).style("text-align: center")
            put_html('<div class="alas-home-marker" aria-hidden="true"></div>')
            # show something
            put_markdown(
                """
            AzurPilot 是基于上游项目 Alas (AzurLaneAutoScript) 的修改版本，采用 GPL-3.0 许可证，免费开源。如果你在任何渠道付费购买，那你一定是个大傻逼，请申请退款。
            AzurPilot is a modified version based on the upstream project Alas (AzurLaneAutoScript), licensed under GPL-3.0, free and open-source. If you paid through any channel, please request a refund.
            AzurPilotは上流プロジェクトAlas (AzurLaneAutoScript) の改変版で、GPL-3.0ライセンスの無料オープンソースです。購入された場合は、返金をリクエストしてください。
            AzurPilot는 상류 프로젝트 Alas(AzurLaneAutoScript)의 수정 버전이며, GPL-3.0 라이선스의 무료 오픈 소스입니다. 구매하셨다면 환불을 요청해 주세요.
            AzurPilot 是基於上游專案 Alas (AzurLaneAutoScript) 的修改版本，採用 GPL-3.0 許可證，免費開源。如果您透過任何管道付費購買，請申請退款。

            上游项目 / Upstream / 上流プロジェクト / 상류 프로젝트 / 上游專案：`https://github.com/LmeSzinc/AzurLaneAutoScript`
            本项目 / This project / 本プロジェクト / 본 프로젝트 / 本專案：`https://github.com/wess09/AzurPilot`

            如需支持，请联系 / For support, please contact / サポートについてはこちらへ / 지원이 필요하면 아래로 / 如需支援請聯繫：`https://addgroup.nanoda.work/`
            """
            ).style("text-align: center")

        if lang.TRANSLATE_MODE:
            lang.reload()

            def _disable():
                lang.TRANSLATE_MODE = False
                self.show_home()

            toast(
                _t("Gui.Toast.DisableTranslateMode"),
                duration=0,
                position="right",
                onclick=_disable,
            )

    def run(self, initial_page="home") -> None:
        # setup gui
        set_env(title="AzurPilot", output_animation=False)
        run_js(
            "document.head.append(Object.assign(document.createElement('link'), { rel: 'manifest', href: '/static/assets/spa/manifest.json' }))"
        )
        load_webui_styles(theme=self.theme, is_mobile=self.is_mobile)

        # 加载静态 JS 工具文件（公告弹窗、截图查看器、自动刷新等）
        # 替代原来的多个 run_js() 运行时注入
        run_js(
            "var s=document.createElement('script');"
            "s.src='/static/assets/gui/js/alas-utils.js';"
            "document.head.appendChild(s);"
        )

        aside = get_localstorage("aside")

        # OOBE 初次设置向导：无用户配置时引导完成基本设置
        if is_oobe_needed():
            from module.webui.oobe import OOBEWizard

            OOBEWizard(self).start()
            return

        self.mount_shell()
        if initial_page == "manage":
            self.ui_manage()
        else:
            self.show_home()

        # init config watcher
        self._init_alas_config_watcher()

        # save config
        _thread_save_config = threading.Thread(target=self._alas_thread_update_config)
        register_thread(_thread_save_config)
        _thread_save_config.start()

        visibility_state_switch = Switch(
            status={
                True: [
                    lambda: self.__setattr__("visible", True),
                    lambda: (
                        self.alas_update_overview_task()
                        if self.page == "Overview"
                        else 0
                    ),
                    lambda: self.task_handler._task.__setattr__("delay", 15),
                ],
                False: [
                    lambda: self.__setattr__("visible", False),
                    lambda: self.task_handler._task.__setattr__("delay", 1),
                ],
            },
            get_state=get_window_visibility_state,
            name="visibility_state",
        )

        self.state_switch = Switch(
            status=self.set_status,
            get_state=lambda: getattr(getattr(self, "alas", -1), "state", 0),
            name="state",
        )

        self.task_handler.add(self.state_switch.g(), 2)
        self.task_handler.add(self.set_aside_status, 2)
        self.task_handler.add(visibility_state_switch.g(), 15)

        # 启动任务处理器
        self.task_handler.start()

        # Return to previous page

        if initial_page == "home" and aside in alas_instance():
            self.ui_alas(aside)
