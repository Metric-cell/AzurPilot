import re
import unittest
from pathlib import Path
from unittest.mock import patch

from module.config.time_source import LocalTimeSource


PROJECT_ROOT = Path(__file__).resolve().parents[1]

REMOVED_NETWORK_MODULES = (
    "module/api/android_update.py",
    "module/api/stock_exchange_service.py",
    "module/api/stock_exchange_recovery.py",
    "module/api/stock_exchange_history.py",
    "deploy/geo.py",
    "deploy/git_over_cdn/client.py",
    "mcp_server_sse.py",
    "module/mcp",
    "module/base/api_client.py",
    "module/api/update_service.py",
    "module/api/announcement_service.py",
    "module/runtime/discord_presence.py",
    "module/runtime/mcp_auth.py",
    "module/runtime/remote_access.py",
    "module/runtime/updater.py",
    "module/statistics/cl1_data_submitter.py",
    "module/statistics/daily_summary.py",
    "module/statistics/daily_summary_store.py",
    "module/statistics/daily_summary_text.py",
    "module/webui/discord_presence.py",
    "module/webui/remote_access.py",
    "module/webui/updater.py",
)

REMOVED_IMPORTS = (
    "module.api.android_update",
    "module.api.stock_exchange_service",
    "module.api.stock_exchange_recovery",
    "module.api.stock_exchange_history",
    "deploy.git_over_cdn.client",
    "module.base.api_client",
    "module.api.update_service",
    "module.api.announcement_service",
    "module.runtime.discord_presence",
    "module.runtime.mcp_auth",
    "module.runtime.remote_access",
    "module.runtime.updater",
    "mcp_server_sse",
    "module.mcp",
    "module.statistics.cl1_data_submitter",
    "module.statistics.daily_summary",
    "module.statistics.daily_summary_store",
    "module.statistics.daily_summary_text",
    "module.webui.discord_presence",
    "module.webui.remote_access",
    "module.webui.updater",
)

REMOVED_CONFIG_FIELDS = (
    "AutoUpdate",
    "BugReport",
    "CheckUpdateInterval",
    "DailySummary",
    "DiscordRichPresence",
    "EnableRemoteAccess",
    "GitOverCdn",
    "RemoteAccessMode",
    "TelemetryReport",
)

FORBIDDEN_RUNTIME_TOKENS = (
    "stock.nanoda.work",
    "challenges.cloudflare.com/turnstile",
    "alas-apiv2.nanoda.work",
    "ip9.com.cn/get",
    "microsoft-clarity-script",
    "www.clarity.ms",
    "api.yppp.net",
)


def _runtime_python_files():
    for relative_root in ("module", "deploy"):
        yield from (PROJECT_ROOT / relative_root).rglob("*.py")
    yield PROJECT_ROOT / "alas.py"
    yield PROJECT_ROOT / "gui.py"


class TestCleanNetworkPolicy(unittest.TestCase):
    def test_android_routes_do_not_restore_updates(self):
        from module.api.android import routes
        with patch.dict('os.environ', {'AZURPILOT_ANDROID': '1', 'AZURPILOT_ANDROID_TOKEN': 'test-token'}):
            paths = [route.path for route in routes(None, None)]
        self.assertIn('/android/status', paths)
        self.assertFalse(any('/update/' in path for path in paths))

    def test_saved_stock_binding_does_not_start_upload_service(self):
        import tempfile
        from starlette.testclient import TestClient
        from module.api.app import create_app
        from tests.test_api import fixture
        with tempfile.TemporaryDirectory() as directory:
            root = fixture(directory)
            binding = root / 'cache/stock-exchange/bindings.json'
            binding.parent.mkdir(parents=True)
            binding.write_text('{"testpilot": {"uploadToken": "test-token"}}')
            with patch('urllib.request.urlopen', side_effect=AssertionError('禁止自动上传')) as outbound:
                with TestClient(create_app(root=root, password='', manage_runtime=False)) as client:
                    self.assertEqual(200, client.get('/healthz').status_code)
                    router = client.app.state.gateway.router
                    self.assertFalse(hasattr(router, 'stock_exchange'))
                    self.assertNotIn('stock.status', router.methods)
                    self.assertNotIn('stock.request', router.methods)
                outbound.assert_not_called()

    def test_android_frontend_does_not_download_updates(self):
        import tempfile
        from deploy.frontend import ensure_frontend
        with tempfile.TemporaryDirectory() as directory, \
                patch.dict('os.environ', {'AZURPILOT_ANDROID': '1'}), \
                patch('urllib.request.urlopen') as download, \
                patch('subprocess.run') as build:
            with self.assertRaisesRegex(RuntimeError, '预构建前端'):
                ensure_frontend(directory)
            download.assert_not_called()
            build.assert_not_called()

    def test_removed_network_modules_stay_removed(self):
        restored = [
            relative_path
            for relative_path in REMOVED_NETWORK_MODULES
            if (PROJECT_ROOT / relative_path).exists()
        ]

        self.assertEqual([], restored)

    def test_runtime_does_not_import_removed_network_integrations(self):
        violations = []
        for path in _runtime_python_files():
            source = path.read_text(encoding="utf-8")
            for removed_import in REMOVED_IMPORTS:
                if removed_import in source:
                    violations.append(
                        f"{path.relative_to(PROJECT_ROOT)}: {removed_import}"
                    )

        self.assertEqual([], violations)

    def test_removed_network_settings_stay_out_of_active_config(self):
        config_paths = (
            PROJECT_ROOT / "deploy/config.py",
            PROJECT_ROOT / "deploy/Windows/config.py",
            PROJECT_ROOT / "module/config/argument/argument.yaml",
            PROJECT_ROOT / "module/config/config_generated.py",
        )
        field_pattern = re.compile(
            r"^\s*(%s)\s*(?::[^=\n]+)?[=:]"
            % "|".join(map(re.escape, REMOVED_CONFIG_FIELDS)),
            flags=re.MULTILINE,
        )
        violations = []
        for path in config_paths:
            match = field_pattern.search(path.read_text(encoding="utf-8"))
            if match:
                violations.append(
                    f"{path.relative_to(PROJECT_ROOT)}: {match.group(1)}"
                )

        self.assertEqual([], violations)

    def test_removed_public_endpoints_stay_out_of_runtime(self):
        violations = []
        paths = list(_runtime_python_files())
        paths.append(PROJECT_ROOT / "frontend/index.html")
        for root in ("frontend/src", "frontend/public"):
            paths.extend(
                path for path in (PROJECT_ROOT / root).rglob("*")
                if path.suffix in (".ts", ".tsx", ".js", ".css", ".html", ".json")
            )
        for path in paths:
            source = path.read_text(encoding="utf-8")
            for token in FORBIDDEN_RUNTIME_TOKENS:
                if token in source:
                    violations.append(
                        f"{path.relative_to(PROJECT_ROOT)}: {token}"
                    )

        self.assertEqual([], violations)

    def test_react_frontend_does_not_restore_update_requests(self):
        violations = []
        for path in (PROJECT_ROOT / "frontend/src").rglob("*"):
            if path.suffix not in (".ts", ".tsx", ".json") or path.name == "i18n.ts":
                continue
            source = path.read_text(encoding="utf-8")
            if re.search(r"['\"]updater\.(?:status|commits|fetch|apply|cancel)['\"]", source):
                violations.append(str(path.relative_to(PROJECT_ROOT)))
        self.assertEqual([], violations)

    def test_react_wallpaper_is_local(self):
        source = (PROJECT_ROOT / "frontend/src/components/Wallpaper.tsx").read_text(
            encoding="utf-8"
        )
        self.assertNotRegex(source, r"https?://")
        background = (PROJECT_ROOT / "frontend/src/app/background.ts").read_text(encoding="utf-8")
        self.assertIn("DEFAULT_BACKGROUND_URL = '/wallpaper.jpg'", background)
        self.assertTrue((PROJECT_ROOT / "frontend/public/wallpaper.jpg").is_file())

    def test_time_source_uses_only_the_local_clock(self):
        source = (PROJECT_ROOT / "module/config/time_source.py").read_text(
            encoding="utf-8"
        )
        forbidden_tokens = (
            "import socket",
            "socket.",
            "getaddrinfo",
            "sendto(",
            "recvfrom(",
            "NTP_PACKET",
            "NTP_SERVERS",
        )

        self.assertEqual(
            [],
            [token for token in forbidden_tokens if token in source],
        )

    def test_local_time_source_preserves_the_shared_time_api(self):
        source = LocalTimeSource()

        with patch("module.config.time_source.time_.time", return_value=123.5):
            self.assertEqual(123.5, source.timestamp())
        self.assertFalse(source.refresh(force=True))
        self.assertEqual(
            {
                "enabled": False,
                "synced": False,
                "server": "-",
                "offset": 0.0,
                "refresh_interval": 0,
                "last_sync_elapsed": None,
            },
            source.status(),
        )


if __name__ == "__main__":
    unittest.main()
