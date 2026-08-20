"""纯本机时间源。

clean 版本只读取操作系统时钟，不进行 DNS 查询、NTP 请求或其他网络传输。
任务调度仍统一通过本模块取时；计时与休眠继续使用单调时钟，避免系统时间调整
影响间隔计算。
"""

import time as time_
from datetime import datetime, timezone


class LocalTimeSource:
    """提供与旧时间源兼容的纯本机时间接口。"""

    def __init__(self) -> None:
        self.offset = 0.0
        self.server = None
        self.synced = False
        self.refresh_interval = 0

    @property
    def enabled(self) -> bool:
        """clean 版本始终关闭网络校时。"""
        return False

    def refresh(self, force: bool = False) -> bool:
        """保留旧调用接口；本机时间无需联网刷新。"""
        return False

    def timestamp(self) -> float:
        return time_.time() + self.offset

    def now(self, tz=None) -> datetime:
        return datetime.fromtimestamp(self.timestamp(), tz=tz)

    def status(self) -> dict:
        return {
            "enabled": self.enabled,
            "synced": self.synced,
            "server": self.server or "-",
            "offset": self.offset,
            "refresh_interval": self.refresh_interval,
            "last_sync_elapsed": None,
        }

    @staticmethod
    def monotonic() -> float:
        return time_.monotonic()

    @staticmethod
    def sleep(seconds: float) -> None:
        time_.sleep(seconds)


local_time = LocalTimeSource()

# 保留旧名称供现有扩展导入；二者都只指向纯本机实现。
NetworkTimeSource = LocalTimeSource
network_time = local_time


def refresh_time(force: bool = False) -> bool:
    return local_time.refresh(force=force)


def now(tz=None) -> datetime:
    return local_time.now(tz=tz)


def utcnow() -> datetime:
    return now(timezone.utc)


def timestamp() -> float:
    return local_time.timestamp()


def status() -> dict:
    return local_time.status()


def monotonic() -> float:
    return local_time.monotonic()


def sleep(seconds: float) -> None:
    local_time.sleep(seconds)
