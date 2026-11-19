import os
import asyncio
import importlib
import sys
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from module.device.connection_attr import ConnectionAttr
from module.runtime.setting import State


class TestAdbProvisioning(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.instance = ConnectionAttr.__new__(ConnectionAttr)

    @staticmethod
    def archive(entries):
        def download(url, target):
            with zipfile.ZipFile(target, 'w') as bundle:
                for name, data in entries.items():
                    bundle.writestr(name, data)
            return str(target)
        return download

    def test_configured_binary_avoids_download(self):
        target = self.root / 'adb'
        target.write_bytes(b'local')
        deploy = SimpleNamespace(AdbExecutable='adb', root_filepath=str(self.root))
        with patch.object(State, 'deploy_config', deploy), \
             patch.object(self.instance, 'download_adb_binary') as download:
            self.assertEqual(self.instance.adb_binary, str(target))
        download.assert_not_called()

    def test_blank_config_uses_project_default(self):
        deploy = SimpleNamespace(AdbExecutable='', root_filepath=str(self.root))
        self.instance.adb_binary_list = []
        expected = str(self.root / '.venv' / 'bin' / 'adb')
        with patch('sys.platform', 'linux'), patch.object(State, 'deploy_config', deploy), \
             patch('module.device.connection_attr.os.path.isfile', return_value=False), \
             patch('module.device.connection_attr.shutil.which', return_value=None), \
             patch.object(self.instance, 'download_adb_binary', return_value=expected) as download:
            self.assertEqual(self.instance.adb_binary, expected)
        download.assert_called_once_with(expected)

    def test_directory_is_not_treated_as_adb(self):
        deploy = SimpleNamespace(AdbExecutable='.', root_filepath=str(self.root))
        self.instance.adb_binary_list = []
        with patch.object(State, 'deploy_config', deploy), \
             patch('module.device.connection_attr.shutil.which', return_value=None), \
             patch.object(self.instance, 'download_adb_binary', return_value='downloaded') as download:
            self.assertEqual(self.instance.adb_binary, 'downloaded')
        download.assert_called_once_with(str(self.root))

    def test_linux_install_selects_only_adb_and_makes_it_executable(self):
        entries = {'platform-tools/adb': b'adb', '../escape': b'bad',
                   'platform-tools/other': b'other'}
        target = self.root / 'bin' / 'adb'
        with patch('sys.platform', 'linux'), \
             patch('deploy.adb_provision.download_file', side_effect=self.archive(entries)) as download:
            self.assertEqual(self.instance.download_adb_binary(str(target)), str(target))
        self.assertEqual(target.read_bytes(), b'adb')
        self.assertTrue(target.stat().st_mode & 0o111)
        self.assertFalse((self.root / 'escape').exists())
        self.assertFalse((target.parent / 'other').exists())
        self.assertEqual(download.call_args.args[0],
                         'https://dl.google.com/android/repository/platform-tools-latest-linux.zip')

    def test_windows_publishes_dlls_before_executable(self):
        names = ['AdbWinApi.dll', 'AdbWinUsbApi.dll', 'adb.exe']
        entries = {f'platform-tools/{name}': name.encode() for name in names}
        target = self.root / 'adb.exe'
        published = []
        real_replace = os.replace

        def replace(source, dest):
            published.append(Path(dest).name)
            real_replace(source, dest)

        with patch('sys.platform', 'win32'), \
             patch('deploy.adb_provision.download_file', side_effect=self.archive(entries)), \
             patch('deploy.adb_provision.os.replace', side_effect=replace):
            self.instance.download_adb_binary(str(target))
        self.assertEqual(published, names)
        for name in names:
            self.assertEqual((self.root / name).read_bytes(), name.encode())

    def test_incomplete_archive_never_publishes_binary_or_dlls(self):
        entries = {'platform-tools/AdbWinApi.dll': b'dll', 'platform-tools/adb.exe': b'adb'}
        target = self.root / 'adb.exe'
        with patch('sys.platform', 'win32'), \
             patch('deploy.adb_provision.download_file', side_effect=self.archive(entries)):
            with self.assertRaisesRegex(ValueError, 'AdbWinUsbApi.dll'):
                self.instance.download_adb_binary(str(target))
        self.assertFalse(target.exists())
        self.assertFalse((self.root / 'AdbWinApi.dll').exists())

    def test_concurrent_install_downloads_once(self):
        target = self.root / 'adb'
        download = self.archive({'platform-tools/adb': b'adb'})
        errors = []
        barrier = threading.Barrier(2)

        def run():
            try:
                barrier.wait()
                self.instance.download_adb_binary(str(target))
            except Exception as exc:
                errors.append(exc)

        with patch('sys.platform', 'linux'), \
             patch('deploy.adb_provision.download_file', side_effect=download) as mocked:
            threads = [threading.Thread(target=run) for _ in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
        self.assertEqual(errors, [])
        self.assertEqual(mocked.call_count, 1)
        self.assertEqual(target.read_bytes(), b'adb')

    def test_download_failure_has_actionable_error(self):
        deploy = SimpleNamespace(AdbExecutable='missing', root_filepath=str(self.root))
        self.instance.adb_binary_list = []
        with patch.object(State, 'deploy_config', deploy), \
             patch('module.device.connection_attr.shutil.which', return_value=None), \
             patch.object(self.instance, 'download_adb_binary', side_effect=OSError('offline')):
            with self.assertRaisesRegex(FileNotFoundError, 'Install Android platform-tools'):
                _ = self.instance.adb_binary

    def test_generic_installer_provisions_before_connecting(self):
        # deploy.emulator is Windows-only; stub its imports while exercising the
        # generic launcher's real manager on the test host.
        with patch.dict(sys.modules, {'winreg': Mock()}), \
             patch.object(asyncio, 'WindowsProactorEventLoopPolicy', create=True), \
             patch.object(asyncio, 'set_event_loop_policy'):
            adb_module = importlib.import_module('deploy.adb')
        manager = adb_module.AdbManager.__new__(adb_module.AdbManager)
        manager.root_filepath = str(self.root)
        manager.AdbExecutable = '.venv/Scripts/adb.exe'
        manager.ReplaceAdb = False
        manager.AutoConnect = True
        entries = {f'platform-tools/{name}': name.encode() for name in
                   ('adb.exe', 'AdbWinApi.dll', 'AdbWinUsbApi.dll')}
        with patch('sys.platform', 'win32'), patch.dict(os.environ, {'PATH': ''}), \
             patch('sys.executable', str(self.root / 'python.exe')), \
             patch('deploy.adb_provision.download_file', side_effect=self.archive(entries)) as download, \
             patch.object(adb_module, 'EmulatorConnect') as emulator:
            manager.adb_install()
        target = self.root / '.venv' / 'Scripts' / 'adb.exe'
        self.assertTrue(target.is_file())
        emulator.assert_called_once_with(adb=str(target))
        emulator.return_value.brute_force_connect.assert_called_once()
        download.assert_called_once()

    def test_generic_installer_disabled_adb_does_not_resolve(self):
        with patch.dict(sys.modules, {'winreg': Mock()}), \
             patch.object(asyncio, 'WindowsProactorEventLoopPolicy', create=True), \
             patch.object(asyncio, 'set_event_loop_policy'):
            adb_module = importlib.import_module('deploy.adb')
        manager = adb_module.AdbManager.__new__(adb_module.AdbManager)
        manager.ReplaceAdb = manager.AutoConnect = False
        with patch('deploy.adb_provision.resolve_adb') as resolve:
            manager.adb_install()
        resolve.assert_not_called()

    def test_windows_legacy_config_uses_same_provisioner(self):
        from deploy.Windows.config import DeployConfig
        manager = DeployConfig.__new__(DeployConfig)
        manager.root_filepath = str(self.root)
        manager.AdbExecutable = 'adb.exe'
        with patch('deploy.adb_provision.resolve_adb', return_value='resolved') as resolve:
            self.assertEqual(manager.adb, 'resolved')
        resolve.assert_called_once_with(str(self.root), 'adb.exe')
