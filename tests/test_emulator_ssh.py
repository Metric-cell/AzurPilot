from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from module.device.platform import ssh
from module.device.platform.platform_base import PlatformBase


def config(**kwargs):
    values = dict(EmulatorInfo_Emulator='SSH', EmulatorInfo_EnableRemoteSSH=True,
                  EmulatorInfo_RemoteSSHHost='example.org', EmulatorInfo_RemoteSSHPort=22,
                  EmulatorInfo_RemoteSSHUser='alice', EmulatorInfo_RemoteSSHPublicKey='',
                  EmulatorInfo_RemoteStartCommand='start', EmulatorInfo_RemoteStopCommand='stop')
    values.update(kwargs)
    return SimpleNamespace(**values)


def test_runner_argv_and_host_key_policy(monkeypatch):
    run = Mock(return_value=SimpleNamespace(returncode=0))
    monkeypatch.setattr(ssh.subprocess, 'run', run)
    assert ssh.run_emulator_ssh(config(), 'start; remote shell')
    args = run.call_args.args[0]
    assert args[-3:] == ['--', 'alice@example.org', 'start; remote shell']
    assert 'StrictHostKeyChecking=accept-new' in args
    assert 'ClearAllForwardings=yes' in args
    assert run.call_args.kwargs['timeout'] == 30
    assert run.call_args.kwargs['stderr'] == ssh.subprocess.DEVNULL


@pytest.mark.parametrize('field,value', [('EmulatorInfo_EnableRemoteSSH', 'true'),
    ('EmulatorInfo_RemoteSSHHost', '-oProxyCommand=evil'), ('EmulatorInfo_RemoteSSHPort', 0),
    ('EmulatorInfo_RemoteSSHUser', 'evil@host'), ('EmulatorInfo_RemoteSSHHost', 'host name')])
def test_reject_invalid_or_disabled(monkeypatch, field, value):
    run = Mock()
    monkeypatch.setattr(ssh.subprocess, 'run', run)
    assert not ssh.run_emulator_ssh(config(**{field: value}), 'start')
    run.assert_not_called()


def test_platform_ssh_availability_does_not_discover_local_instance(monkeypatch):
    platform = object.__new__(PlatformBase)
    platform.config = config()
    monkeypatch.setattr(PlatformBase, 'emulator_instance', property(lambda self: pytest.fail('local lookup')))
    run = Mock(return_value=True)
    monkeypatch.setattr('module.device.platform.platform_base.run_emulator_ssh', run)
    assert platform.emulator_available
    assert platform.emulator_start()
    assert platform.emulator_stop()
    assert [call.args[1] for call in run.call_args_list] == ['start', 'stop']
    platform.config.EmulatorInfo_EnableRemoteSSH = False
    assert not platform.emulator_available


def test_manager_fallback_and_early_dispatch(monkeypatch):
    from alas import AzurLaneAutoScript
    app = object.__new__(AzurLaneAutoScript)
    app.__dict__['config'] = SimpleNamespace(data={'EmulatorManager': {'EmulatorManager': {
        'EnableRemoteSSH': True, 'RemoteSSHHost': 'example.org', 'RemoteSSHPort': 22,
        'RemoteSSHUser': 'bob', 'RemoteCommand': 'legacy'}}})
    run = Mock(return_value=True)
    monkeypatch.setattr(ssh, 'run_ssh_command', run)
    assert app._run('EmulatorManager') is True
    assert run.call_args.kwargs['command'] == 'legacy'
    app.config.EmulatorInfo_EnableRemoteSSH = True
    app.config.EmulatorInfo_RemoteStartCommand = 'preferred'
    assert app.emulator_manager()
    assert run.call_args.kwargs['command'] == 'preferred'


def test_windows_key_protected_before_writing(monkeypatch, tmp_path):
    events = []
    monkeypatch.setattr(ssh, '_is_windows', lambda: True)
    def protect(path):
        assert list(__import__('pathlib').Path(path).iterdir()) == []
        events.append('protected')
    monkeypatch.setattr(ssh, '_protect_windows_directory', protect)
    def run(args, **kwargs):
        assert events == ['protected']
        key_path = args[args.index('-i') + 1]
        assert __import__('pathlib').Path(key_path).read_text() == 'secret\n'
        events.append('run')
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(ssh.subprocess, 'run', run)
    assert ssh.run_emulator_ssh(config(EmulatorInfo_RemoteSSHPublicKey='secret'), 'start')
    assert events == ['protected', 'run']


def test_windows_key_protection_failure_never_runs_ssh(monkeypatch):
    monkeypatch.setattr(ssh, '_is_windows', lambda: True)
    def fail(path):
        raise ValueError('ACL failure')
    monkeypatch.setattr(ssh, '_protect_windows_directory', fail)
    run = Mock()
    monkeypatch.setattr(ssh.subprocess, 'run', run)
    assert not ssh.run_emulator_ssh(config(EmulatorInfo_RemoteSSHPublicKey='secret'), 'start')
    run.assert_not_called()


def test_ssh_failed_command_and_optional_null_key(monkeypatch):
    run = Mock(return_value=SimpleNamespace(returncode=255))
    monkeypatch.setattr(ssh.subprocess, 'run', run)
    assert not ssh.run_emulator_ssh(config(EmulatorInfo_RemoteSSHPublicKey=None), 'start')
    assert '-i' not in run.call_args.args[0]


def test_offline_device_reaches_ssh_start(monkeypatch):
    from unittest.mock import PropertyMock
    from module.device.connection import Connection
    from module.device.device import Device
    from module.exception import EmulatorNotRunningError
    platform = object.__new__(PlatformBase)
    platform.config = config()
    run = Mock(return_value=True)
    monkeypatch.setattr('module.device.platform.platform_base.run_emulator_ssh', run)
    monkeypatch.setattr(Connection, '__init__', Mock(side_effect=EmulatorNotRunningError))
    monkeypatch.setattr(Device, 'platform', PropertyMock(return_value=platform))
    with pytest.raises(EmulatorNotRunningError):
        Device(config())
    assert run.call_count == 3


def test_mac_routes_before_native_retry(monkeypatch):
    from module.device.platform.platform_mac import PlatformMac
    platform = object.__new__(PlatformMac)
    platform.config = config()
    run = Mock(return_value=True)
    monkeypatch.setattr('module.device.platform.platform_base.run_emulator_ssh', run)
    monkeypatch.setattr(platform, '_emulator_function_wrapper', Mock(side_effect=AssertionError('native path')))
    assert platform.emulator_start()
    assert platform.emulator_stop()
    assert run.call_count == 2


def test_cached_device_platform_uses_reloaded_config(monkeypatch):
    from module.device.device import Device
    device = object.__new__(Device)
    device.config = config(EmulatorInfo_RemoteSSHHost='old.example')
    platform = object.__new__(PlatformBase)
    platform.config = device.config
    device._platform = platform
    run = Mock(return_value=SimpleNamespace(returncode=0))
    monkeypatch.setattr(ssh.subprocess, 'run', run)
    device.config = config(EmulatorInfo_EnableRemoteSSH=False)
    assert not device.platform.emulator_available
    assert not device.emulator_start()
    run.assert_not_called()
    device.config = config(EmulatorInfo_RemoteSSHHost='new.example')
    assert device.emulator_start()
    assert run.call_args.args[0][-2] == 'alice@new.example'


def test_scheduler_restart_uses_one_current_config_snapshot(monkeypatch):
    from alas import AzurLaneAutoScript
    app = object.__new__(AzurLaneAutoScript)
    current = config(EmulatorInfo_RemoteSSHHost='new.example', Error_AdbOfflineThreshold=10)
    app.__dict__['config'] = current
    app.__dict__['device'] = Mock(config=config(EmulatorInfo_RemoteSSHHost='old.example'))
    app.consecutive_adb_offline = 0
    app.consecutive_game_not_running = 0
    app._deep_restart_enabled = Mock(return_value=False)
    platform = Mock()
    platform.emulator_start.return_value = True
    factory = Mock(return_value=platform)
    monkeypatch.setattr('module.device.platform.Platform', factory)
    monkeypatch.setattr('alas.time.sleep', lambda _: None)
    def operation(func, **kwargs):
        if func == platform.emulator_stop:
            app.__dict__['config'] = config(EmulatorInfo_RemoteSSHHost='later.example')
        return func()
    app._emulator_op_with_timeout = operation
    assert app._try_restart_emulator()
    factory.assert_called_once_with(current, connect=False)
    platform.emulator_stop.assert_called_once()
    platform.emulator_start.assert_called_once()


def test_ssh_readiness_waits_for_network_adb(monkeypatch):
    import module.device.platform.platform_base as base
    platform = object.__new__(PlatformBase)
    platform.config = config(Emulator_Serial='192.0.2.1:5555')
    client = Mock()
    device = client.device.return_value
    device.shell.side_effect = [OSError('offline'), 'azurpilot-ready']
    platform.__dict__['adb_client'] = client
    clock = [0.0]
    monkeypatch.setattr(base.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(base.time, 'sleep', lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    assert platform._ssh_start_watch(timeout=4)
    assert client.connect.call_count == 2
    client.device.assert_called_with(serial='192.0.2.1:5555')


def test_ssh_readiness_timeout_and_no_network_serial(monkeypatch):
    import module.device.platform.platform_base as base
    platform = object.__new__(PlatformBase)
    platform.config = config(Emulator_Serial='host.example:5555')
    client = Mock()
    client.connect.side_effect = OSError('offline')
    platform.__dict__['adb_client'] = client
    clock = [0.0]
    monkeypatch.setattr(base.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(base.time, 'sleep', lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    assert not platform._ssh_start_watch(timeout=2)
    assert clock[0] == 2
    platform.config.Emulator_Serial = 'auto'
    assert platform._ssh_start_watch(timeout=2)
    assert client.connect.call_count == 2


def test_scheduler_ssh_stop_failure_does_not_skip_start(monkeypatch):
    from alas import AzurLaneAutoScript
    app = object.__new__(AzurLaneAutoScript)
    app.__dict__['config'] = config(Error_AdbOfflineThreshold=10)
    app.consecutive_adb_offline = 0
    app.consecutive_game_not_running = 0
    app._deep_restart_enabled = Mock(return_value=False)
    platform = Mock()
    platform.emulator_stop.return_value = False
    platform.emulator_start.return_value = True
    monkeypatch.setattr('module.device.platform.Platform', Mock(return_value=platform))
    monkeypatch.setattr('alas.time.sleep', lambda _: None)
    app._emulator_op_with_timeout = lambda func, **kwargs: func()
    assert not app._try_restart_emulator()
    platform.emulator_start.assert_called_once()
    app.config.EmulatorInfo_RemoteStopCommand = ''
    assert app._try_restart_emulator()
