"""从行情数据推导交易日序列。

数据即权威：交易日序列取日线全集的 trade_date 去重结果，自动涵盖
节假日、临时休市与半日市，无需外部日历库。装配顺序由此解耦：先扫描
交易日，再据此构建市场描述，最后打开数据存取入口。
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import duckdb

__all__ = ["scan_trading_days"]


def scan_trading_days(root: Path) -> list[date]:
    """扫描 minute_db 根目录下全部日线文件，返回升序交易日序列。"""
    daily_glob = root / "daily" / "*.parquet"
    if not (root / "daily").is_dir():
        raise FileNotFoundError(f"日线目录不存在: {root / 'daily'}")
    frame = duckdb.sql(
        "SELECT DISTINCT trade_date FROM read_parquet(?) ORDER BY trade_date",
        params=[daily_glob.as_posix()],
    ).df()
    days = [date.fromisoformat(str(value)) for value in frame["trade_date"]]
    if not days:
        raise ValueError(f"日线数据为空，无法推导交易日序列: {root}")
    return days
