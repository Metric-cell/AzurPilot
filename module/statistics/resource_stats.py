"""资源快照统计模块，实现游戏资源的本地记录与历史查询。
通过 SQLite 数据库存储资源变动快照，
支持按实例和时间范围查询，用于绘制资源趋势图。"""

# 此文件实现了通用资源快照的记录与查询功能。
# 当各项资源数值（如石油、物资、钻石等）发生变化时，记录快照以便后续绘制历史趋势图。
import sqlite3
import threading
from datetime import datetime
from typing import Any, Dict, List

from module.logger import logger


_local_lock = threading.Lock()
_LOCAL_DB = './config/azurstats_local.db'
_table_ensured = False


def _database():
    from module.persistence.database import for_legacy_path
    return for_legacy_path(_LOCAL_DB, 'statistics')


def _connect() -> sqlite3.Connection:
    return _database().connect()


# Dashboard 使用的资源名称与数据库列名保持在同一处，供区间聚合复用。
RESOURCE_COLUMNS = {
    'Oil': 'oil',
    'Coin': 'coin',
    'Gem': 'gem',
    'Pt': 'pt',
    'Cube': 'cube',
    'Core': 'core',
    'Medal': 'medal',
    'Merit': 'merit',
    'GuildCoin': 'guild_coin',
    'ActionPoint': 'action_point',
    'YellowCoin': 'yellow_coin',
    'PurpleCoin': 'purple_coin',
}


def _ensure_table():
    """建表和旧数据转换统一由安装级迁移入口完成。"""
    _database().ensure_ready()


def _overlay_opsi_snapshot(row: Dict[str, Any]) -> Dict[str, Any]:
    """保留业务适配入口；资源值直接读取原生列。"""
    return row


def record_resource_snapshot(instance: str, resources: Dict[str, Any]) -> bool:
    """以同一事务保存各项资源的当前读数。"""
    from module.persistence.database import register_instance
    try:
        row = {'instance': instance, 'ts': datetime.now().isoformat()}
        row.update({column: resources.get(name) for name, column in RESOURCE_COLUMNS.items()})
        with _local_lock, _connect() as conn:
            register_instance(conn, instance)
            columns = ','.join(row)
            values = ','.join(':' + name for name in row)
            conn.execute(f'INSERT INTO resource_snapshots ({columns}) VALUES ({values})', row)
        return True
    except Exception as error:
        logger.warning(f'[统计-资源] 记录资源快照失败: {type(error).__name__}')
        return False


def get_resource_timeline(
    instance: str = 'default',
    limit: int = 500,
    since: str = None,
    until: str = None,
    include_opsi: bool = True,
) -> List[Dict[str, Any]]:
    """获取资源快照时间序列数据，用于绘制资源变化曲线。

    Args:
        instance: 实例名称
        limit: 最大返回条数
        since: 起始时间（ISO 文本，含）。为空表示不限
        until: 结束时间（ISO 文本，含）。为空表示不限
        include_opsi: 是否返回大世界三列（行动力/黄币/紫币）。

    Returns:
        list[dict]: 按时间排序的快照列表，每个包含:
            - ts: ISO 格式时间戳
            - oil, coin, gem, pt, cube, core, medal, merit, guild_coin,
              action_point, yellow_coin, purple_coin: 资源数值（可能为 None）
    """
    try:
        _ensure_table()
        with _database().transaction(write=False) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                '''
                SELECT * FROM resource_snapshots
                WHERE instance = ? AND (? IS NULL OR ts >= ?) AND (? IS NULL OR ts <= ?)
                ORDER BY id DESC
                LIMIT ?
                ''',
                (instance, since, since, until, until, limit),
            ).fetchall()
            if include_opsi:
                result = [_overlay_opsi_snapshot(dict(row)) for row in rows]
            else:
                result = [dict(row) for row in rows]
                for item in result:
                    item.update(action_point=None, yellow_coin=None, purple_coin=None)
            result.reverse()
            return result
    except Exception as e:
        logger.warning(f'[统计-资源] 获取资源时间线失败: {type(e).__name__}')
        return []


__all__ = ['RESOURCE_COLUMNS', 'record_resource_snapshot', 'get_resource_timeline']
