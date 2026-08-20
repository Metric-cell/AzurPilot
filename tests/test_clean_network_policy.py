import re
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]

REMOVED_NETWORK_MODULES = (
    "deploy/geo.py",
    "deploy/git_over_cdn/client.py",
    "mcp_server_sse.py",
    "module/base/api_client.py",
    "module/statistics/cl1_data_submitter.py",
    "module/webui/discord_presence.py",
    "module/webui/remote_access.py",
    "module/webui/updater.py",
)

REMOVED_IMPORTS = (
    "deploy.git_over_cdn.client",
    "module.base.api_client",
    "module.statistics.cl1_data_submitter",
    "module.webui.discord_presence",
    "module.webui.remote_access",
    "module.webui.updater",
)

REMOVED_CONFIG_FIELDS = (
    "AutoUpdate",
    "BugReport",
    "CheckUpdateInterval",
    "DiscordRichPresence",
    "EnableRemoteAccess",
    "GitOverCdn",
    "RemoteAccessMode",
    "TelemetryReport",
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


if __name__ == "__main__":
    unittest.main()
