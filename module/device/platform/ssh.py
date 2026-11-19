"""Opt-in outbound OpenSSH execution for emulator lifecycle commands."""
import ipaddress
import json
import os
import re
import shutil
import subprocess
import tempfile

from module.logger import logger


def ssh_enabled(config):
    return (getattr(config, 'EmulatorInfo_Emulator', '') == 'SSH' and
            getattr(config, 'EmulatorInfo_EnableRemoteSSH', False) is True)


def _valid_host(host):
    if not isinstance(host, str) or not host or len(host) > 253:
        return False
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return all(re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?', label)
                   for label in host.rstrip('.').split('.'))


def _is_windows():
    return os.name == 'nt'


def _protect_windows_directory(directory):
    # Fixed script, with the path supplied as JSON on stdin; no key or remote
    # command is ever passed to PowerShell. Verify the resulting ACL.
    script = r"""
$ErrorActionPreference = 'Stop'
try {
    $path = [Console]::In.ReadToEnd() | ConvertFrom-Json
    $sid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User
    $acl = [System.Security.AccessControl.DirectorySecurity]::new()
    $acl.SetOwner($sid)
    $acl.SetAccessRuleProtection($true, $false)
    $rule = [System.Security.AccessControl.FileSystemAccessRule]::new($sid, 'FullControl', 'ContainerInherit,ObjectInherit', 'None', 'Allow')
    $acl.AddAccessRule($rule)
    [System.IO.DirectoryInfo]::new($path).SetAccessControl($acl)
    $actual = [System.IO.DirectoryInfo]::new($path).GetAccessControl()
    if (-not $actual.AreAccessRulesProtected -or $actual.GetOwner([System.Security.Principal.SecurityIdentifier]) -ne $sid) { exit 1 }
    $rules = @($actual.GetAccessRules($true, $true, [System.Security.Principal.SecurityIdentifier]))
    if ($rules.Count -ne 1 -or $rules[0].IdentityReference -ne $sid -or $rules[0].IsInherited -or $rules[0].AccessControlType -ne 'Allow' -or $rules[0].FileSystemRights -ne 'FullControl' -or $rules[0].InheritanceFlags -ne ([System.Security.AccessControl.InheritanceFlags]::ContainerInherit -bor [System.Security.AccessControl.InheritanceFlags]::ObjectInherit) -or $rules[0].PropagationFlags -ne 'None') { exit 1 }
} catch { exit 1 }
"""
    result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', script],
                            input=json.dumps(directory).encode(), stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, timeout=30,
                            creationflags=subprocess.CREATE_NO_WINDOW)
    if result.returncode:
        raise ValueError('Unable to protect temporary key directory')


def run_ssh_command(*, enabled, host, port, user, key, command):
    """Execute one configured remote command, returning only success or failure.

    OpenSSH itself performs first-use host-key enrollment; changed keys are rejected.
    Neither command text nor process output is logged.
    """
    if key is None:
        key = ''
    if enabled is not True:
        logger.warning('[设备-SSH] 远程 SSH 未启用')
        return False
    if (not _valid_host(host) or not isinstance(user, str) or
            (user and not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]*', user)) or
            isinstance(port, bool) or not str(port).isascii() or not str(port).isdigit() or
            not 1 <= int(port) <= 65535 or not isinstance(command, str) or
            not command.strip() or '\x00' in command or not isinstance(key, str)):
        logger.error('[设备-SSH] 远程 SSH 配置无效')
        return False
    args = ['ssh', '-n', '-T', '-p', str(port),
            '-o', 'StrictHostKeyChecking=accept-new', '-o', 'BatchMode=yes',
            '-o', 'ConnectTimeout=10', '-o', 'ClearAllForwardings=yes',
            '-o', 'ForwardAgent=no', '-o', 'ForwardX11=no',
            '-o', 'PermitLocalCommand=no', '-o', 'ProxyCommand=none',
            '-o', 'ProxyJump=none', '-o', 'ControlMaster=no',
            '-o', 'ControlPath=none', '-o', 'RemoteCommand=none']
    key_file = None
    key_dir = None
    try:
        if key:
            if _is_windows():
                key_dir = tempfile.mkdtemp(prefix='azurpilot-ssh-')
                _protect_windows_directory(key_dir)
                key_file = os.path.join(key_dir, 'identity')
                with open(key_file, 'x', encoding='utf-8') as stream:
                    stream.write(key.strip() + '\n')
            else:
                fd, key_file = tempfile.mkstemp(prefix='azurpilot-ssh-')
                with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                    stream.write(key.strip() + '\n')
                os.chmod(key_file, 0o600)
            args.extend(['-i', key_file, '-o', 'IdentitiesOnly=yes'])
        args.extend(['--', f'{user}@{host}' if user else host, command])
        result = subprocess.run(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, timeout=30, check=False)
        if result.returncode != 0:
            logger.error('[设备-SSH] 远程命令失败')
            return False
        return True
    except (OSError, subprocess.SubprocessError, ValueError):
        logger.error('[设备-SSH] 远程命令执行失败或超时')
        return False
    finally:
        if key_dir:
            try:
                shutil.rmtree(key_dir)
            except OSError:
                logger.error('[设备-SSH] 无法移除临时密钥目录')
        elif key_file:
            try:
                os.unlink(key_file)
            except OSError:
                logger.error('[设备-SSH] 无法移除临时密钥文件')


def run_emulator_ssh(config, command):
    return run_ssh_command(
        enabled=ssh_enabled(config),
        host=getattr(config, 'EmulatorInfo_RemoteSSHHost', ''),
        port=getattr(config, 'EmulatorInfo_RemoteSSHPort', 22),
        user=getattr(config, 'EmulatorInfo_RemoteSSHUser', ''),
        key=getattr(config, 'EmulatorInfo_RemoteSSHPublicKey', ''),
        command=command,
    )
