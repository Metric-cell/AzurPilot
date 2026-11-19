"""ADB connection failures for an explicitly configured SSH emulator."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from module.device.connection import Connection
from module.device.connection_attr import ConnectionAttr
from module.exception import EmulatorNotRunningError, RequestHumanTakeover


def setup_connection(monkeypatch, *, enabled=True, serial='127.0.0.1:5555', message=None):
    config = SimpleNamespace(Emulator_Serial=serial, EmulatorInfo_Emulator='SSH',
                             EmulatorInfo_EnableRemoteSSH=enabled, DEVICE_OVER_HTTP=False,
                             Emulator_PackageName='com.example.game', SERVER='cn')
    client = SimpleNamespace(connect=Mock(return_value=message or
                                        'failed to connect to 127.0.0.1:5555: Connection refused'))

    def init(self, config):
        self.config = config
        self.serial = config.Emulator_Serial
        self.adb_client = client
        self.adb = object()

    monkeypatch.setattr(ConnectionAttr, '__init__', init)
    monkeypatch.setattr(Connection, 'detect_device', Mock())
    monkeypatch.setattr(Connection, 'list_device', Mock(return_value=[]))
    monkeypatch.setattr(Connection, 'check_mumu_app_keep_alive', Mock())
    monkeypatch.setattr('module.device.connection.set_server', Mock())
    return config, client


@pytest.mark.parametrize('message', [
    'failed to connect to 127.0.0.1:5555: Connection refused',
    'cannot connect to 127.0.0.1:5555: Operation timed out',
])
def test_ssh_cold_tcp_connection_surfaces_offline_after_retries(monkeypatch, message):
    config, client = setup_connection(monkeypatch, message=message)
    with pytest.raises(EmulatorNotRunningError):
        Connection(config)
    assert client.connect.call_count == 3


def test_disabled_ssh_preserves_non_ssh_connection_behavior(monkeypatch):
    config, client = setup_connection(monkeypatch, enabled=False)
    connection = Connection(config)
    assert connection.serial == config.Emulator_Serial
    assert client.connect.call_count == 3


def test_success_and_bad_port_keep_existing_behavior(monkeypatch):
    config, client = setup_connection(monkeypatch, message='connected to 127.0.0.1:5555')
    Connection(config)
    assert client.connect.call_count == 1
    client.connect.reset_mock(return_value=True)
    client.connect.return_value = 'bad port number'
    with pytest.raises(RequestHumanTakeover):
        Connection(config)
    assert client.connect.call_count == 1
