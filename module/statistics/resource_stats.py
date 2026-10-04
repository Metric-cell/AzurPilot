"""资源快照统计模块，实现游戏资源的本地记录与历史查询。
通过 SQLite 数据库存储资源变动快照，
支持按实例和时间范围查询，用于绘制资源趋势图。"""

# 此文件实现了通用资源快照的记录与查询功能。
# 当各项资源数值（如石油、物资、钻石等）发生变化时，记录快照以便后续绘制历史趋势图。
import sqlite3
import threading
import os
from datetime import datetime
from typing import Any, Dict, List

from module.logger import logger


_local_lock = threading.Lock()
_LOCAL_DB = './config/azurstats_local.db'
_table_ensured = False


class _ClosingConnection(sqlite3.Connection):
    """事务结束后立即释放连接，避免资源快照库在 Windows 上残留文件锁。"""

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


def _connect() -> sqlite3.Connection:
    return sqlite3.connect(_LOCAL_DB, factory=_ClosingConnection)


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
    """确保 resource_snapshots 表存在（仅首次调用时执行）。"""
    global _table_ensured
    if _table_ensured:
        return
    os.makedirs(os.path.dirname(_LOCAL_DB), exist_ok=True)
    with _connect() as conn:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS resource_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                instance TEXT NOT NULL,
                ts TEXT NOT NULL,
                oil INTEGER,
                coin INTEGER,
                gem INTEGER,
                pt INTEGER,
                cube INTEGER,
                core INTEGER,
                medal INTEGER,
                merit INTEGER,
                guild_coin INTEGER,
                action_point INTEGER,
                yellow_coin INTEGER,
                purple_coin INTEGER
            )
        ''')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_res_snap_instance ON resource_snapshots(instance)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_res_snap_ts ON resource_snapshots(instance, ts)')
        conn.commit()
    _table_ensured = True


def record_resource_snapshot(instance: str, resources: Dict[str, Any]) -> bool:
    """记录一次资源快照。

    当游戏内任何资源数值发生变化时调用，记录所有资源的当前值。

    Args:
        instance: 实例名称
        resources: 资源字典，包含所有 Dashboard 资源的当前值
            key 为资源名（如 Oil, Coin, Gem, Pt, Cube 等），
            value 为资源数值（int）

    Returns:
        bool: 是否成功记录
    """
    try:
        _ensure_table()
        now = datetime.now().isoformat()

        row = {
            'instance': instance,
            'ts': now,
            'oil': resources.get('Oil'),
            'coin': resources.get('Coin'),
            'gem': resources.get('Gem'),
            'pt': resources.get('Pt'),
            'cube': resources.get('Cube'),
            'core': resources.get('Core'),
            'medal': resources.get('Medal'),
            'merit': resources.get('Merit'),
            'guild_coin': resources.get('GuildCoin'),
            'action_point': resources.get('ActionPoint'),
            'yellow_coin': resources.get('YellowCoin'),
            'purple_coin': resources.get('PurpleCoin'),
        }

        with _local_lock:
            with _connect() as conn:
                conn.execute('''
                    INSERT INTO resource_snapshots (
                        instance, ts,
                        oil, coin, gem, pt, cube,
                        core, medal, merit, guild_coin,
                        action_point, yellow_coin, purple_coin
                    ) VALUES (
                        :instance, :ts,
                        :oil, :coin, :gem, :pt, :cube,
                        :core, :medal, :merit, :guild_coin,
                        :action_point, :yellow_coin, :purple_coin
                    )
                ''', row)
                conn.commit()
        return True
    except Exception as e:
        logger.warning(f'[统计-资源] 记录资源快照失败: {e}')
        return False


def get_resource_timeline(
    instance: str = 'default',
    limit: int = 500,
    since: str = None,
) -> List[Dict[str, Any]]:
    """获取资源快照时间序列数据，用于绘制资源变化曲线。

    Args:
        instance: 实例名称
        limit: 最大返回条数
        since: 起始时间（ISO 文本，含）。为空表示不限

    Returns:
        list[dict]: 按时间排序的快照列表，每个包含:
            - ts: ISO 格式时间戳
            - oil, coin, gem, pt, cube, core, medal, merit, guild_coin,
              action_point, yellow_coin, purple_coin: 资源数值（可能为 None）
    """
    try:
        _ensure_table()
        with _connect() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                '''
                SELECT * FROM resource_snapshots
                WHERE instance = ? AND (? IS NULL OR ts >= ?)
                ORDER BY id DESC
                LIMIT ?
                ''',
                (instance, since, since, limit),
            ).fetchall()
            result = [dict(row) for row in rows]
            result.reverse()
            return result
    except Exception as e:
        logger.warning(f'[统计-资源] 获取资源时间线失败: {e}')
        return []


__all__ = ['RESOURCE_COLUMNS', 'record_resource_snapshot', 'get_resource_timeline']
