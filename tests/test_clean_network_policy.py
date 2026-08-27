import re
import unittest
from pathlib import Path
from unittest.mock import patch

from module.config.time_source import LocalTimeSource


PROJECT_ROOT = Path(__file__).resolve().parents[1]

REMOVED_NETWORK_MODULES = (
    "deploy/geo.py",
    "deploy/git_over_cdn/client.py",
    "mcp_server_sse.py",
    "module/base/api_client.py",
    "module/statistics/cl1_data_submitter.py",
    "module/statistics/daily_summary.py",
    "module/statistics/daily_summary_store.py",
    "module/statistics/daily_summary_text.py",
    "module/webui/discord_presence.py",
    "module/webui/remote_access.py",
    "module/webui/updater.py",
)

REMOVED_IMPORTS = (
    "deploy.git_over_cdn.client",
    "module.base.api_client",
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
    "alas-apiv2.nanoda.work",
    "ip9.com.cn/get",
    "microsoft-clarity-script",
    "www.clarity.ms",
)


def _runtime_python_files():
    for relative_root in ("module", "deploy"):
        yield from (PROJECT_ROOT / relative_root).rglob("*.py")
    yield PROJECT_ROOT / "alas.py"
    yield PROJECT_ROOT / "gui.py"


class TestCleanNetworkPolicy(unittest.TestCase):
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
        for path in _runtime_python_files():
            source = path.read_text(encoding="utf-8")
            for token in FORBIDDEN_RUNTIME_TOKENS:
                if token in source:
                    violations.append(
                        f"{path.relative_to(PROJECT_ROOT)}: {token}"
                    )

        self.assertEqual([], violations)

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
