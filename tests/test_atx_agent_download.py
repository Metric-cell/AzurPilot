"""Device initialization must use administrator-provisioned executables only."""

import os
import tempfile
import unittest
from unittest.mock import Mock, patch
from types import SimpleNamespace

from module.device.method import utils


class TestAtxAgentProvisioning(unittest.TestCase):
    def setUp(self):
        self.init = utils.PatchedIniter.__new__(utils.PatchedIniter)
        self.init._device = Mock()

    def test_missing_binary_fails_without_push(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, 'AZURPILOT_ATX_AGENT'):
                self.init.push_url('https://example.invalid/agent', tgz=True, extract_name='atx-agent')
        self.init._device.sync.push.assert_not_called()

    def test_provisioned_binary_is_pushed(self):
        with tempfile.NamedTemporaryFile() as binary:
            with patch.dict(os.environ, {'AZURPILOT_ATX_AGENT': binary.name}):
                result = self.init.push_url('https://example.invalid/agent', tgz=True, extract_name='atx-agent')
            self.assertEqual(result, '/data/local/tmp/atx-agent')
            self.init._device.sync.push.assert_called_once_with(binary.name, result, mode=0o755)

    def test_other_helpers_cannot_be_downloaded(self):
        with self.assertRaisesRegex(RuntimeError, 'remote downloads are disabled'):
            self.init.push_url('https://example.invalid/minitouch')
        self.init._device.sync.push.assert_not_called()

    def test_missing_apks_fail_before_agent_setup(self):
        self.init.is_apk_outdated = Mock(return_value=True)
        self.init.setup_atx_agent = Mock()
        with self.assertRaisesRegex(RuntimeError, 'helper APKs'):
            self.init.install()
        self.init.setup_atx_agent.assert_not_called()

    def test_existing_apks_allow_agent_setup(self):
        self.init.is_apk_outdated = Mock(return_value=False)
        self.init.setup_atx_agent = Mock()
        self.init.install()
        self.init.setup_atx_agent.assert_called_once_with()


class TestAdbProvisioning(unittest.TestCase):
    def test_missing_adb_fails_without_network_or_installation(self):
        from module.device.connection_attr import ConnectionAttr
        from module.runtime.setting import State

        instance = ConnectionAttr.__new__(ConnectionAttr)
        deploy = SimpleNamespace(AdbExecutable='missing-adb', root_filepath='/nonexistent')
        with patch.object(State, 'deploy_config', deploy), \
             patch('module.device.connection_attr.os.path.exists', return_value=False), \
             patch('module.device.connection_attr.shutil.which', return_value=None):
            with self.assertRaisesRegex(FileNotFoundError, 'Install Android platform-tools'):
                _ = instance.adb_binary


if __name__ == '__main__':
    unittest.main()
