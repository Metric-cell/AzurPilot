"""本地背景图库；服务端网络抓取已停用。"""
import json
import os
import subprocess
import sys
import hashlib
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from module.api.protocol import ApiError
from module.logger import logger

PROJECT_ROOT = Path(__file__).resolve().parents[2]
LIBRARY_DIR = PROJECT_ROOT / 'cache' / 'background' / 'library'
INDEX_FILE = LIBRARY_DIR / 'index.json'

MAX_BYTES = 20 * 1024 * 1024


class BackgroundError(ApiError):
    """背景相关的可预期错误。"""

    def __init__(self, message: str):
        super().__init__('background', message)


MEDIA_TYPES = {
    '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.png': 'image/png',
    '.webp': 'image/webp', '.gif': 'image/gif', '.bmp': 'image/bmp',
    '.avif': 'image/avif', '.mp4': 'video/mp4', '.webm': 'video/webm',
    '.mov': 'video/quicktime',
}


def _read_index() -> List[Dict[str, Any]]:
    """读取图库索引文件。

    Returns:
        图库条目列表，索引不存在或损坏时返回空列表。
    """
    if not INDEX_FILE.exists():
        return []
    try:
        data = json.loads(INDEX_FILE.read_text(encoding='utf-8'))
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        logger.warning(f'[背景图库] 索引损坏，按空处理：{INDEX_FILE}')
        return []


def _write_index(entries: List[Dict[str, Any]]) -> None:
    """将图库条目列表写入索引文件。

    Args:
        entries: 待写入的图库条目列表。
    """
    LIBRARY_DIR.mkdir(parents=True, exist_ok=True)
    INDEX_FILE.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding='utf-8')


def gallery_list() -> List[Dict[str, Any]]:
    """列出图库条目。

    自动剔除磁盘文件已缺失的无效条目。

    Returns:
        包含条目信息的字典列表，每个条目包含 id、name、size、added、kind 等字段。
    """
    entries = [entry for entry in _read_index() if isinstance(entry, dict) and (LIBRARY_DIR / str(entry.get('id', ''))).exists()]
    return entries


def gallery_add_bytes(data: bytes, filename: str, content_type: str = '') -> Dict[str, Any]:
    """把浏览器上传的文件存进图库（本地图片走这条；服务端不再去网上抓）。

    Args:
        data: 文件字节。
        filename: 原始文件名，用于展示与推断后缀。
        content_type: 浏览器给出的内容类型，缺省时按后缀判断。

    Returns:
        新条目字典。

    Raises:
        BackgroundError: 空文件、超限或类型不是图片/视频。
    """
    if not data:
        raise BackgroundError('所选文件为空。')
    if len(data) > MAX_BYTES:
        raise BackgroundError('文件超过 20 MB。')
    suffix = Path(filename or '').suffix.lower()
    expected = MEDIA_TYPES.get(suffix)
    provided = content_type.split(';', 1)[0].strip().lower()
    if not expected or provided not in ('', 'application/octet-stream', expected):
        raise BackgroundError('只支持常见图片或视频格式。')
    is_video = expected.startswith('video/')
    digest = hashlib.sha1(data).hexdigest()[:10]
    identifier = f'{int(time.time() * 1000):x}-{digest}{suffix}'
    LIBRARY_DIR.mkdir(parents=True, exist_ok=True)
    (LIBRARY_DIR / identifier).write_bytes(data)
    entry = {
        'id': identifier,
        'name': Path(filename or '').name or identifier,
        'size': len(data),
        'added': int(time.time()),
        'kind': 'video' if is_video else 'image',
        'source': 'upload',
    }
    entries = [item for item in _read_index() if item.get('id') != identifier]
    entries.append(entry)
    _write_index(entries)
    logger.info(f'[背景图库] 已上传 {identifier}（{len(data)} 字节）')
    return entry


def gallery_remove(identifier: str) -> bool:
    """从图库删除一个条目（包括文件与索引）。

    Args:
        identifier: 条目的唯一标识符（文件名）。

    Returns:
        若成功删除返回 True，若条目不存在则返回 False。
    """
    entries = _read_index()
    kept = [item for item in entries if item.get('id') != identifier]
    if len(kept) == len(entries):
        return False
    (LIBRARY_DIR / identifier).unlink(missing_ok=True)
    _write_index(kept)
    return True


def gallery_open() -> Dict[str, str]:
    """在系统文件管理器里打开图库目录。

    打开的路径是服务端固定的图库目录，不接受任何调用方输入，因此不存在越权打开任意目录的问题；
    服务本身只监听本机（gui.py 绑定 127.0.0.1），所以该操作等同于用户自己在资源管理器中打开该文件夹。

    Returns:
        包含已打开目录路径 'path' 的字典。

    Raises:
        BackgroundError: 调用系统命令打开目录失败。
    """
    LIBRARY_DIR.mkdir(parents=True, exist_ok=True)
    try:
        if os.name == 'nt':
            os.startfile(str(LIBRARY_DIR))  # noqa: S606 - 固定路径，非用户输入
        elif sys.platform == 'darwin':
            subprocess.Popen(['open', str(LIBRARY_DIR)])
        else:
            subprocess.Popen(['xdg-open', str(LIBRARY_DIR)])
    except OSError as error:
        raise BackgroundError(f'打开文件夹失败：{type(error).__name__}') from error
    return {'path': str(LIBRARY_DIR)}


def gallery_path(identifier: str) -> Optional[Path]:
    """获取图库中某个条目的本地文件路径。

    Args:
        identifier: 条目的唯一标识符（文件名）。

    Returns:
        条目文件的 Path 对象；若不存在、为空或包含非法路径字符则返回 None。
    """
    if not identifier or '/' in identifier or '\\' in identifier or identifier.startswith('.'):
        return None
    path = LIBRARY_DIR / identifier
    return path if path.is_file() else None
