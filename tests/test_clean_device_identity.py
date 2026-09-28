"""clean 的本地数据库身份不随容器和机器标识挂载变化。"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from module.base import device_id


class CleanDeviceIdentityTests(unittest.TestCase):
    def test_existing_local_identity_survives_host_change(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'module/base').mkdir(parents=True)
            (root / 'log').mkdir()
            identity = '0123456789abcdef' * 2
            (root / 'log/device_id.json').write_text(json.dumps({'device_id': identity}))
            with patch.object(device_id, '__file__', str(root / 'module/base/device_id.py')), \
                    patch.object(device_id, 'generate_device_id') as generate, \
                    patch.object(device_id, '_start_refresh_timer') as refresh:
                self.assertEqual(identity, device_id._init_device_id())
                generate.assert_not_called()
                refresh.assert_called_once_with(identity, root / 'log/device_id.json')
