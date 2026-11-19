"""Dependency-free ADB lookup and Google platform-tools provisioning."""

import os
import shutil
import stat
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path


def download_file(url, target):
    request = urllib.request.Request(url, headers={'User-Agent': 'AzurPilot/2.1'})
    with urllib.request.urlopen(request, timeout=30) as response, open(target, 'wb') as output:
        shutil.copyfileobj(response, output)


def platform_tools_url():
    """Return Google's platform-tools archive for the current operating system."""
    if sys.platform == 'win32':
        platform = 'windows'
    elif sys.platform == 'darwin':
        platform = 'darwin'
    elif sys.platform.startswith('linux'):
        platform = 'linux'
    else:
        return None
    return f'https://dl.google.com/android/repository/platform-tools-latest-{platform}.zip'


def download_adb_binary(target):
    """Install only ADB and its required Windows DLLs from platform-tools."""
    from deploy.config_transaction import config_transaction

    url = platform_tools_url()
    if url is None:
        raise RuntimeError(f'Automatic ADB download is unsupported on {sys.platform}')
    target = Path(target)
    windows = sys.platform == 'win32'
    executable = 'adb.exe' if windows else 'adb'
    names = ['AdbWinApi.dll', 'AdbWinUsbApi.dll', executable] if windows else [executable]

    # The lock is shared by processes and threads; check again after acquiring it.
    with config_transaction(target.parent / '.adb-provision', timeout=300):
        if target.is_file():
            return str(target).replace('\\', '/')
        with tempfile.TemporaryDirectory(prefix='.adb-install-', dir=target.parent) as temp:
            staging = Path(temp)
            archive = staging / 'platform-tools.zip'
            download_file(url, archive)
            with zipfile.ZipFile(archive) as bundle:
                for name in names:
                    member = f'platform-tools/{name}'
                    matches = [info for info in bundle.infolist() if info.filename == member]
                    if len(matches) != 1 or matches[0].is_dir() or not 0 < matches[0].file_size <= 128 * 1024 * 1024:
                        raise ValueError(f'Invalid or missing platform-tools member: {member}')
                    with bundle.open(matches[0]) as source, (staging / name).open('wb') as dest:
                        shutil.copyfileobj(source, dest)
            if not windows:
                binary = staging / executable
                binary.chmod(binary.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            # Publish DLLs first and the executable last, so its presence marks completion.
            for name in names:
                destination = target if name == executable else target.parent / name
                os.replace(staging / name, destination)
    return str(target).replace('\\', '/')


def resolve_adb(root, configured):
    """Find a local ADB or install it at the configured project path."""
    configured = (configured or '').strip().strip('"')
    if not configured:
        configured = '.venv/Scripts/adb.exe' if sys.platform == 'win32' else '.venv/bin/adb'
    target = os.path.abspath(os.path.join(root, configured))
    candidates = [target, *(os.path.join(root, name) for name in (
        '.venv/Scripts/adb.exe', '.venv/bin/adb', 'bin/adb/adb.exe')),
        os.path.join(os.path.dirname(sys.executable),
                     'adb.exe' if sys.platform == 'win32' else 'adb')]
    if sys.platform != 'win32':
        candidates.insert(-1, '/usr/bin/adb')
    for candidate in candidates:
        if os.path.isfile(candidate):
            return os.path.abspath(candidate).replace('\\', '/')
    path_adb = shutil.which('adb')
    if path_adb:
        return os.path.abspath(path_adb).replace('\\', '/')
    try:
        return download_adb_binary(target)
    except (OSError, ValueError, RuntimeError, zipfile.BadZipFile) as exc:
        raise FileNotFoundError(
            'ADB was not found and automatic installation failed. '
            'Install Android platform-tools locally or configure AdbExecutable in deploy.yaml.'
        ) from exc
