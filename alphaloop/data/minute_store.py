"""双市场 MinuteDB 的规范化读取入口。

物理布局（两市场同构）::

    {root}/minute/{symbol}/{year}.parquet
    {root}/daily/{symbol}.parquet
    {root}/corp_actions.parquet
    {root}/listing.parquet

本模块把原始存储归一到 core.schema 定义的规范口径。归一化戒律：

- 成交额只在源确实提供时保留原值；缺失以空值表达，绝不用 close 乘
  volume 之类的估算静默填充（参考项目曾因此把某市场的真实成交额静默
  压低约 100 倍）。源本身即估算值的，以 amount_is_estimated 标注透传。
- 成交量统一换算为股；源以其他单位计量的市场按 volume_multiplier 换算。
- 源存储的 vwap 若与成交量单位耦合（每交易单位价格），换算为每股价格；
  成交量为零的行（指数）vwap 置空，不保留无意义的 0。
- 源不提供的字段一律为空值，不以 0 伪装。
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path

import duckdb
import pandas as pd

from alphaloop.core.schema import (
    CORP_ACTION_COLUMNS,
    DAILY_COLUMNS,
    LISTING_COLUMNS,
    MINUTE_COLUMNS,
    SCHEMA_VERSION,
    column_names,
)
from alphaloop.data.fingerprint import dataset_fingerprint
from alphaloop.market.protocols import MarketSpec

__all__ = ["DatasetManifest", "MinuteStore"]

_SYMBOL_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.\-]*$")


@dataclass(frozen=True, slots=True)
class DatasetManifest:
    """数据集的接入声明，计入运行配置快照。"""

    dataset_id: str
    market_id: str
    root: str
    schema_version: str
    ts_semantics: str
    timezone: str
    fingerprint: str
    adjustment_available: bool
    coverage_start: str
    coverage_end: str


class MinuteStore:
    """规范化读取双市场 MinuteDB。重扫描经 DuckDB，面板加工经 pandas。"""

    def __init__(self, root: Path, market: MarketSpec, *, dataset_id: str) -> None:
        if not (root / "minute").is_dir() or not (root / "daily").is_dir():
            raise FileNotFoundError(f"{root} 不是有效的 minute_db 目录（缺 minute/ 或 daily/）")
        self.root = root
        self.market = market
        self.dataset_id = dataset_id
        self._trading_days_cache: list[date] | None = None

    # ------------------------------------------------------------------
    # 读取接口
    # ------------------------------------------------------------------

    def read_minute(self, symbols: list[str], start: date, end: date) -> pd.DataFrame:
        """读取闭区间 [start, end] 内若干标的的分钟数据，返回规范面板。"""
        files = self._minute_files(symbols, start, end)
        if not files:
            return _empty_frame(column_names(MINUTE_COLUMNS))
        conventions = self.market.conventions
        select_vwap = "vwap" if conventions.has_minute_vwap else "NULL AS vwap"
        select_amount = "amount" if conventions.amount_native_minute else "NULL AS amount"
        start_ts = datetime.combine(start, time.min)
        end_ts = datetime.combine(end + timedelta(days=1), time.min)
        raw = duckdb.sql(
            f"SELECT symbol, ts, open, high, low, close, volume, trade_count, "
            f"{select_amount}, {select_vwap} "
            f"FROM read_parquet(?, union_by_name=true) "
            f"WHERE ts >= ? AND ts < ? ORDER BY symbol, ts",
            params=[[f.as_posix() for f in files], start_ts, end_ts],
        ).df()
        return self._normalize_minute(raw)

    def read_daily(self, symbols: list[str], start: date, end: date) -> pd.DataFrame:
        """读取闭区间 [start, end] 内若干标的的日线数据，返回规范面板。"""
        files = [
            self.root / "daily" / f"{self._check_symbol(symbol)}.parquet" for symbol in symbols
        ]
        existing = [f for f in files if f.exists()]
        if not existing:
            return _empty_frame(column_names(DAILY_COLUMNS))
        raw = duckdb.sql(
            "SELECT symbol, trade_date, open, high, low, close, volume, amount "
            "FROM read_parquet(?, union_by_name=true) "
            "WHERE trade_date >= ? AND trade_date <= ? ORDER BY symbol, trade_date",
            params=[[f.as_posix() for f in existing], start.isoformat(), end.isoformat()],
        ).df()
        return self._normalize_daily(raw)

    def read_corp_actions(self) -> pd.DataFrame:
        """读取公司行动表并统一到规范类型；空表返回规范空 DataFrame。"""
        path = self.root / "corp_actions.parquet"
        columns = column_names(CORP_ACTION_COLUMNS)
        if not path.exists():
            return _empty_frame(columns)
        raw = duckdb.sql(
            "SELECT * FROM read_parquet(?)", params=[path.as_posix()]
        ).df()
        if raw.empty:
            return _empty_frame(columns)
        out = pd.DataFrame(
            {
                "symbol": raw["symbol"].astype("string"),
                "ex_date": [date.fromisoformat(str(value)) for value in raw["ex_date"]],
                "split_ratio": pd.to_numeric(raw["split_ratio"], errors="coerce"),
                "cash_div": pd.to_numeric(raw["cash_div"], errors="coerce"),
            }
        )
        return out[columns]

    def read_listing(self) -> pd.DataFrame:
        """读取标的清单，名称做全角与内嵌空白归一。"""
        path = self.root / "listing.parquet"
        columns = column_names(LISTING_COLUMNS)
        if not path.exists():
            return _empty_frame(columns)
        raw = duckdb.sql("SELECT * FROM read_parquet(?)", params=[path.as_posix()]).df()
        if raw.empty:
            return _empty_frame(columns)
        symbols = raw["symbol"].astype("string")
        names = raw.get("name")
        normalized_names = (
            names.map(_normalize_name).astype("string")
            if names is not None
            else pd.Series(pd.NA, index=raw.index, dtype="string")
        )
        status = raw.get("status")
        out = pd.DataFrame(
            {
                "symbol": symbols,
                "name": normalized_names,
                "status": (
                    status.astype("string")
                    if status is not None
                    else pd.Series(pd.NA, index=raw.index, dtype="string")
                ),
                "is_index": symbols.map(self.market.is_index_symbol).astype(bool),
            }
        )
        return out[columns]

    def daily_liquidity_frame(self, start: date, end: date) -> pd.DataFrame:
        """全市场日线流动性切片（symbol、trade_date、amount、is_index），供选池。"""
        raw = duckdb.sql(
            "SELECT symbol, trade_date, amount FROM read_parquet(?, union_by_name=true) "
            "WHERE trade_date >= ? AND trade_date <= ?",
            params=[
                (self.root / "daily" / "*.parquet").as_posix(),
                start.isoformat(),
                end.isoformat(),
            ],
        ).df()
        if raw.empty:
            return _empty_frame(["symbol", "trade_date", "amount", "is_index"])
        return pd.DataFrame(
            {
                "symbol": raw["symbol"].astype("string"),
                "trade_date": [date.fromisoformat(str(value)) for value in raw["trade_date"]],
                "amount": pd.to_numeric(raw["amount"], errors="coerce").astype("float64"),
                "is_index": raw["symbol"]
                .astype("string")
                .map(self.market.is_index_symbol)
                .astype(bool),
            }
        )

    def trading_days(self) -> list[date]:
        """数据推导的交易日序列（缓存）。"""
        if self._trading_days_cache is None:
            frame = duckdb.sql(
                "SELECT DISTINCT trade_date FROM read_parquet(?) ORDER BY trade_date",
                params=[(self.root / "daily" / "*.parquet").as_posix()],
            ).df()
            self._trading_days_cache = [
                date.fromisoformat(str(value)) for value in frame["trade_date"]
            ]
        return self._trading_days_cache

    def manifest(self) -> DatasetManifest:
        """返回数据集接入声明（快速级指纹）。"""
        days = self.trading_days()
        return DatasetManifest(
            dataset_id=self.dataset_id,
            market_id=self.market.market_id,
            root=self.root.as_posix(),
            schema_version=SCHEMA_VERSION,
            ts_semantics="bar_close_local_wallclock",
            timezone=self.market.conventions.timezone,
            fingerprint=dataset_fingerprint(self.root),
            adjustment_available=self.market.corporate_actions.adjustment_available,
            coverage_start=days[0].isoformat(),
            coverage_end=days[-1].isoformat(),
        )

    # ------------------------------------------------------------------
    # 归一化
    # ------------------------------------------------------------------

    def _normalize_minute(self, raw: pd.DataFrame) -> pd.DataFrame:
        columns = column_names(MINUTE_COLUMNS)
        if raw.empty:
            return _empty_frame(columns)
        conventions = self.market.conventions
        multiplier = conventions.volume_multiplier
        symbols = raw["symbol"].astype("string").str.strip().str.upper()
        ts_local = pd.to_datetime(raw["ts"]).astype("datetime64[us]")
        volume = (
            pd.to_numeric(raw["volume"], errors="coerce").fillna(0).astype("int64") * multiplier
        )
        if conventions.amount_native_minute:
            amount = pd.to_numeric(raw["amount"], errors="coerce").astype("float64")
        else:
            amount = pd.Series(float("nan"), index=raw.index, dtype="float64")
        if conventions.has_minute_vwap:
            vwap = pd.to_numeric(raw["vwap"], errors="coerce").astype("float64") / multiplier
            vwap = vwap.where(volume > 0)
        else:
            vwap = pd.Series(float("nan"), index=raw.index, dtype="float64")
        if conventions.has_trade_count:
            trade_count = pd.to_numeric(raw["trade_count"], errors="coerce").astype("Int64")
        else:
            trade_count = pd.Series(pd.NA, index=raw.index, dtype="Int64")
        minute_label = ts_local.dt.strftime("%H%M").astype("string")
        session = self.market.calendar.session()
        unique_labels = {label: session.is_rth(label) for label in minute_label.unique()}
        is_index_map = {symbol: self.market.is_index_symbol(symbol) for symbol in symbols.unique()}
        out = pd.DataFrame(
            {
                "symbol": symbols,
                "trade_date": ts_local.dt.date,
                "ts_local": ts_local,
                "minute_label": minute_label,
                "open": pd.to_numeric(raw["open"], errors="coerce").astype("float64"),
                "high": pd.to_numeric(raw["high"], errors="coerce").astype("float64"),
                "low": pd.to_numeric(raw["low"], errors="coerce").astype("float64"),
                "close": pd.to_numeric(raw["close"], errors="coerce").astype("float64"),
                "volume": volume,
                "amount": amount,
                "amount_is_estimated": False,
                "vwap": vwap,
                "trade_count": trade_count,
                "is_rth": minute_label.map(unique_labels).astype(bool),
                "is_index": symbols.map(is_index_map).astype(bool),
            }
        )
        return out[columns]

    def _normalize_daily(self, raw: pd.DataFrame) -> pd.DataFrame:
        columns = column_names(DAILY_COLUMNS)
        if raw.empty:
            return _empty_frame(columns)
        conventions = self.market.conventions
        symbols = raw["symbol"].astype("string").str.strip().str.upper()
        volume = (
            pd.to_numeric(raw["volume"], errors="coerce").fillna(0).astype("int64")
            * conventions.volume_multiplier
        )
        amount = pd.to_numeric(raw["amount"], errors="coerce").astype("float64")
        is_index_map = {symbol: self.market.is_index_symbol(symbol) for symbol in symbols.unique()}
        out = pd.DataFrame(
            {
                "symbol": symbols,
                "trade_date": [date.fromisoformat(str(value)) for value in raw["trade_date"]],
                "open": pd.to_numeric(raw["open"], errors="coerce").astype("float64"),
                "high": pd.to_numeric(raw["high"], errors="coerce").astype("float64"),
                "low": pd.to_numeric(raw["low"], errors="coerce").astype("float64"),
                "close": pd.to_numeric(raw["close"], errors="coerce").astype("float64"),
                "volume": volume,
                "amount": amount,
                "amount_is_estimated": not conventions.amount_native_daily,
                "is_index": symbols.map(is_index_map).astype(bool),
            }
        )
        return out[columns]

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------

    def _check_symbol(self, symbol: str) -> str:
        normalized = self.market.normalize_symbol(symbol)
        if not _SYMBOL_PATTERN.match(normalized):
            raise ValueError(f"非法标的代码: {symbol!r}")
        return normalized

    def _minute_files(self, symbols: list[str], start: date, end: date) -> list[Path]:
        if start > end:
            raise ValueError(f"起止日期颠倒: {start} > {end}")
        files: list[Path] = []
        for symbol in symbols:
            normalized = self._check_symbol(symbol)
            symbol_dir = self.root / "minute" / normalized
            if not symbol_dir.is_dir():
                continue
            for year in range(start.year, end.year + 1):
                path = symbol_dir / f"{year}.parquet"
                if path.exists():
                    files.append(path)
        return files


def _normalize_name(value: object) -> object:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return pd.NA
    text = unicodedata.normalize("NFKC", str(value))
    return "".join(text.split())


def _empty_frame(columns: list[str]) -> pd.DataFrame:
    return pd.DataFrame({name: pd.Series(dtype="object") for name in columns})
