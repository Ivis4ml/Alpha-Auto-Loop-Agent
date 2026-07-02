"""从真实 Alpha-Data MinuteDB 抽取小切片作为测试金样本。

夹具保持与真实库相同的目录布局，使 MinuteStore 可以直接读取；分钟数据
只取一个月以控制体积，日线取整年（体积可忽略），公司行动与标的清单按
标的过滤。美股样本刻意包含 NVDA（2024-06-10 除权的一拆十拆股），用于
校验复权因子语义。

用法::

    python tools/make_fixtures.py
"""

from __future__ import annotations

from pathlib import Path

import duckdb

ALPHA_DATA = Path("/Users/xinyu/Code/Workspace/ncvx-project/Alpha-Data/data")
FIXTURES = Path(__file__).resolve().parent.parent / "tests" / "fixtures"

US_SYMBOLS = ["AAPL", "MSFT", "NVDA", "TSLA", "AMZN", "SPY"]
CN_SYMBOLS = ["600519.SH", "000001.SZ", "300750.SZ", "601318.SH", "688111.SH", "000300.SH"]

MINUTE_START = "2024-01-01"
MINUTE_END = "2024-02-01"
DAILY_START = "2024-01-01"
DAILY_END = "2024-12-31"


def _copy_minute(source_root: Path, target_root: Path, symbols: list[str]) -> None:
    for symbol in symbols:
        source = source_root / "minute" / symbol / "2024.parquet"
        if not source.exists():
            raise FileNotFoundError(f"缺少分钟源文件: {source}")
        target = target_root / "minute" / symbol / "2024.parquet"
        target.parent.mkdir(parents=True, exist_ok=True)
        duckdb.sql(
            f"COPY (SELECT * FROM read_parquet('{source.as_posix()}') "
            f"WHERE ts >= '{MINUTE_START}' AND ts < '{MINUTE_END}' ORDER BY ts) "
            f"TO '{target.as_posix()}' (FORMAT PARQUET)"
        )


def _copy_daily(source_root: Path, target_root: Path, symbols: list[str]) -> None:
    for symbol in symbols:
        source = source_root / "daily" / f"{symbol}.parquet"
        if not source.exists():
            raise FileNotFoundError(f"缺少日线源文件: {source}")
        target = target_root / "daily" / f"{symbol}.parquet"
        target.parent.mkdir(parents=True, exist_ok=True)
        duckdb.sql(
            f"COPY (SELECT * FROM read_parquet('{source.as_posix()}') "
            f"WHERE trade_date >= '{DAILY_START}' AND trade_date <= '{DAILY_END}' "
            f"ORDER BY trade_date) TO '{target.as_posix()}' (FORMAT PARQUET)"
        )


def _copy_filtered_table(
    source: Path, target: Path, symbols: list[str], *, allow_empty: bool
) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if not source.exists():
        raise FileNotFoundError(f"缺少源表: {source}")
    count = duckdb.sql(
        "SELECT count(*) AS n FROM read_parquet(?)", params=[source.as_posix()]
    ).fetchone()
    if count is not None and count[0] == 0:
        if not allow_empty:
            raise ValueError(f"源表意外为空: {source}")
        duckdb.sql(
            f"COPY (SELECT * FROM read_parquet('{source.as_posix()}')) "
            f"TO '{target.as_posix()}' (FORMAT PARQUET)"
        )
        return
    quoted = ",".join("'" + s.replace("'", "''") + "'" for s in symbols)
    duckdb.sql(
        f"COPY (SELECT * FROM read_parquet('{source.as_posix()}') WHERE symbol IN ({quoted})) "
        f"TO '{target.as_posix()}' (FORMAT PARQUET)"
    )


def main() -> None:
    plans = [
        ("us_equity", ALPHA_DATA / "equity" / "minute_db", US_SYMBOLS),
        ("cn_equity", ALPHA_DATA / "cn_equity" / "minute_db", CN_SYMBOLS),
    ]
    for name, source_root, symbols in plans:
        target_root = FIXTURES / name / "minute_db"
        _copy_minute(source_root, target_root, symbols)
        _copy_daily(source_root, target_root, symbols)
        _copy_filtered_table(
            source_root / "corp_actions.parquet",
            target_root / "corp_actions.parquet",
            symbols,
            allow_empty=(name == "cn_equity"),
        )
        _copy_filtered_table(
            source_root / "listing.parquet",
            target_root / "listing.parquet",
            symbols,
            allow_empty=False,
        )
        print(f"{name} 夹具完成: {target_root}")


if __name__ == "__main__":
    main()
