"""标的类型识别辅助：从标的清单按名称识别基金类载体。

部分市场的标的清单不含资产类型字段，只能按名称模式识别 ETF、ETN、
信托与基金类载体，供选池排除。这是显式声明的启发式：以 Trust 结尾的
不动产投资信托会被一并排除，对头部流动性选池而言是可接受的取舍。
"""

from __future__ import annotations

import re
from pathlib import Path

import duckdb

__all__ = ["scan_fund_like_symbols"]

_FUND_NAME_PATTERN = re.compile(r"\b(ETF|ETN|Trust|Fund|Shares)\b", re.IGNORECASE)


def scan_fund_like_symbols(root: Path) -> frozenset[str]:
    """扫描 minute_db 根目录下的标的清单，返回名称疑似基金类载体的代码集合。"""
    path = root / "listing.parquet"
    if not path.exists():
        return frozenset()
    frame = duckdb.sql(
        "SELECT symbol, name FROM read_parquet(?)", params=[path.as_posix()]
    ).df()
    matched = frame["name"].astype("string").fillna("").map(
        lambda name: bool(_FUND_NAME_PATTERN.search(name))
    )
    return frozenset(frame.loc[matched, "symbol"].astype(str))
