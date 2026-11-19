import json
import os
import stat
from unittest.mock import patch

import pytest

from deploy import atomic
from module.api.config_service import ConfigService
from module.api.dock_service import DockService
from module.api.protocol import ConfigChange
from module.config.utils import write_file
from tests.test_api import fixture


pytestmark = pytest.mark.skipif(os.name == 'nt', reason='POSIX file mode test')


def assert_private(path):
    assert stat.S_IMODE(path.stat().st_mode) & 0o077 == 0


def test_private_atomic_temp_and_runtime_config(tmp_path):
    original_replace = atomic.replace_tmp
    observed = []

    def inspect_temp(temp, target):
        assert_private(tmp_path / os.path.basename(temp))
        observed.append(True)
        original_replace(temp, target)

    previous = os.umask(0o022)
    try:
        with patch.object(atomic, 'replace_tmp', side_effect=inspect_temp):
            for suffix in ('json', 'yaml'):
                path = tmp_path / ('runtime.' + suffix)
                write_file(str(path), {'Alas': {'EmulatorInfo': {'RemoteSSHPublicKey': 'synthetic-key'}}})
                assert_private(path)
        assert len(observed) == 2
    finally:
        os.umask(previous)


def test_instance_import_copy_patch_reset_and_backup_are_private(tmp_path):
    service = ConfigService(fixture(tmp_path))
    original = service.read_json(service.path('testpilot'))
    original['Alas']['EmulatorInfo']['RemoteSSHPublicKey'] = 'synthetic-key'
    previous = os.umask(0o022)
    try:
        service.save_import('uploaded', json.dumps(original))
        assert_private(service.import_directory / 'uploaded.json')
        service.create('imported', import_file='uploaded')
        assert_private(service.path('imported'))
        service.create('copied', source='imported')
        assert_private(service.path('copied'))
        result = service.patch('copied', None, [ConfigChange(path='Alas.Emulator.Serial', value='auto')])
        assert_private(service.path('copied'))
        result = service.reset_fleet_training('copied', result['revision'])
        assert_private(service.path('copied'))
        with patch.object(DockService, 'report'):
            DockService(service).configure('copied', 1, 125)
        assert_private(service.path('copied'))
        result = service.get('copied')
        service.delete('copied', result['revision'])
        backup, = (service.directory / 'backup').glob('copied-*.json')
        assert_private(backup)
        assert json.loads(backup.read_text())['Alas']['EmulatorInfo']['RemoteSSHPublicKey'] == 'synthetic-key'
    finally:
        os.umask(previous)


def test_identity_registration_and_migration_backup_protect_legacy_config(tmp_path):
    from module.persistence.database import BusinessDatabase
    from module.runtime.game_data import GameDataProtector

    root = fixture(tmp_path)
    profile = root / 'config' / 'testpilot.json'
    data = json.loads(profile.read_text())
    data['Alas']['EmulatorInfo']['RemoteSSHPublicKey'] = 'synthetic-key'
    profile.write_text(json.dumps(data))
    profile.chmod(0o644)
    previous = os.umask(0o022)
    try:
        BusinessDatabase(root / 'config').ensure_ready()
        backup, = (root / 'config' / 'storage-backups').glob('pre-v1-*')
        assert_private(backup)
        assert (backup / 'config' / profile.name).read_bytes() == profile.read_bytes()
        GameDataProtector(root).resolve('testpilot')
        assert_private(profile)
    finally:
        os.umask(previous)
