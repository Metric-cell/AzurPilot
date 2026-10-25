"""桌面启动器的预构建组件协议，不在用户设备上执行包管理器。"""
import json
import os
import platform
from pathlib import Path


def is_prebuilt() -> bool:
    """当前进程是否由组件模式的桌面启动器启动。"""
    return os.environ.get('AZURPILOT_PREBUILT') == '1'


def build_manifest(root=None) -> dict:
    """读取 CI 写入的源码构建信息，不需要 Git。"""
    root = Path(root) if root else Path(__file__).resolve().parents[2]
    try:
        return json.loads((root / 'BUILD_MANIFEST').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}


def validate_runtime(root=None) -> None:
    """确认代码与 Python 属于同一批已完成提交的组件。"""
    root = Path(root) if root else Path(__file__).resolve().parents[2]
    try:
        receipt = json.loads((root / '.azurpilot/installed.json').read_text(encoding='utf-8'))
        manifest = receipt['manifest']
        build = build_manifest(root)
        if (root / '.azurpilot/transaction/journal.json').exists():
            raise ValueError('组件部署尚未完成')
        if manifest['schema'] != 1 or manifest['revision'] != build.get('azurpilot_commit'):
            raise ValueError('源码组件版本不匹配')
        if manifest['python'] != platform.python_version() or manifest['python'] != build.get('python'):
            raise ValueError('Python 组件版本不匹配')
        required = {'source', 'resources', 'frontend', 'python', 'dependencies', 'adb'}
        if not required.issubset(receipt['components']):
            raise ValueError('缺少运行组件')
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise RuntimeError('运行组件不完整，请停止服务并通过桌面启动器重新部署或修复') from exc
