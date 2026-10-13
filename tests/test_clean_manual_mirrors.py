"""验证裁剪版显式镜像部署，不发起网络或修改真实配置。"""
import unittest
from pathlib import Path
from unittest.mock import patch

from deploy import config, git
from deploy.Windows import config as windows_config
from deploy.Windows import git as windows_git


class TestCleanManualMirrors(unittest.TestCase):
    def test_manual_aliases_do_not_query_network(self):
        for module in (config, windows_config):
            for alias, expected in (
                ('cn', 'https://github.com/wess09/AzurPilot'),
                ('global', 'https://github.com/wess09/AzurPilot'),
                ('cnb', 'https://cnb.cool/AzurPilot/AzurPilot'),
                ('gitcode', 'https://gitcode.com/ddl2/AzurPilot'),
            ):
                with self.subTest(module=module.__name__, alias=alias), patch('urllib.request.urlopen') as outbound:
                    settings = object.__new__(module.DeployConfig)
                    settings.Repository = alias
                    settings.config = {}
                    settings.config_redirect()
                    self.assertEqual(expected, settings.Repository)
                    self.assertFalse(hasattr(module, 'get_country_code'))
                    self.assertFalse(hasattr(module, 'choose_fallback_repository'))
                    outbound.assert_not_called()

    def test_manual_mirror_peer_preserves_github_and_handles_empty(self):
        for module in (git, windows_git):
            for source, expected in (
                ('https://cnb.cool/AzurPilot/AzurPilot.git', 'https://gitcode.com/ddl2/AzurPilot'),
                ('https://gitcode.com/ddl2/AzurPilot/', 'https://cnb.cool/AzurPilot/AzurPilot'),
                ('https://github.com/wess09/AzurPilot', None),
                ('', None),
                (None, None),
            ):
                with self.subTest(module=module.__name__, source=source):
                    self.assertEqual(expected, module.peer_fallback_repository(source))

    def test_domestic_templates_use_explicit_manual_repository(self):
        root = Path(__file__).resolve().parents[1]
        templates = list((root / 'config').glob('deploy.template*-cn.yaml'))
        self.assertTrue(templates)
        for template in templates:
            with self.subTest(template=template.name):
                text = template.read_text()
                self.assertIn('Repository: https://cnb.cool/AzurPilot/AzurPilot', text)
                self.assertNotIn('git://git.pull', text)
