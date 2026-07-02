"""数据读取层的规范化行为测试（基于真实数据切片的金样本）。"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import duckdb
import pandas as pd

from alphaloop.core.schema import DAILY_COLUMNS, MINUTE_COLUMNS, column_names
from alphaloop.data.minute_store import MinuteStore
from alphaloop.data.universe import build_universe

FIXTURES = Path(__file__).resolve().parent / "fixtures"

JAN_START = date(2024, 1, 2)
JAN_END = date(2024, 1, 31)


class TestCanonicalSchema:
    """双市场经同一 API 读出同一规范 schema（验收 7.1 的数据层部分）。"""

    def test_minute_columns_identical(self, us_store: MinuteStore, cn_store: MinuteStore) -> None:
        us = us_store.read_minute(["AAPL"], JAN_START, JAN_START)
        cn = cn_store.read_minute(["600519.SH"], JAN_START, JAN_START)
        expected = column_names(MINUTE_COLUMNS)
        assert list(us.columns) == expected
        assert list(cn.columns) == expected

    def test_daily_columns_identical(self, us_store: MinuteStore, cn_store: MinuteStore) -> None:
        us = us_store.read_daily(["AAPL"], JAN_START, JAN_END)
        cn = cn_store.read_daily(["600519.SH"], JAN_START, JAN_END)
        expected = column_names(DAILY_COLUMNS)
        assert list(us.columns) == expected
        assert list(cn.columns) == expected

    def test_corp_actions_schema_unified(
        self, us_store: MinuteStore, cn_store: MinuteStore
    ) -> None:
        us = us_store.read_corp_actions()
        cn = cn_store.read_corp_actions()
        assert list(us.columns) == list(cn.columns)
        assert cn.empty
        assert not us.empty
        merged = pd.concat([us, cn], ignore_index=True)
        assert len(merged) == len(us)


class TestAmountIntegrity:
    """成交额戒律：真实值逐行保真，估算值显式标注（规避参考项目的静默错算）。"""

    def test_cn_daily_amount_equals_raw_parquet(self, cn_store: MinuteStore) -> None:
        raw = duckdb.sql(
            "SELECT trade_date, amount FROM read_parquet(?) "
            "WHERE trade_date >= '2024-01-02' AND trade_date <= '2024-01-31' ORDER BY trade_date",
            params=[
                (FIXTURES / "cn_equity" / "minute_db" / "daily" / "600519.SH.parquet").as_posix()
            ],
        ).df()
        normalized = cn_store.read_daily(["600519.SH"], JAN_START, JAN_END)
        assert len(normalized) == len(raw)
        assert (normalized["amount"].to_numpy() == raw["amount"].to_numpy()).all()
        assert not normalized["amount_is_estimated"].any()

    def test_us_daily_amount_flagged_estimated(self, us_store: MinuteStore) -> None:
        daily = us_store.read_daily(["AAPL"], JAN_START, JAN_END)
        assert daily["amount_is_estimated"].all()

    def test_us_minute_amount_is_null_not_fabricated(self, us_store: MinuteStore) -> None:
        minute = us_store.read_minute(["AAPL"], JAN_START, JAN_START)
        assert minute["amount"].isna().all()
        assert not minute["amount_is_estimated"].any()


class TestUnitNormalization:
    """成交量统一为股、vwap 统一为每股价格、缺失字段以空值表达。"""

    def test_cn_volume_scaled_to_shares(self, cn_store: MinuteStore) -> None:
        raw = duckdb.sql(
            "SELECT volume FROM read_parquet(?) WHERE trade_date = '2024-01-02'",
            params=[
                (FIXTURES / "cn_equity" / "minute_db" / "daily" / "600519.SH.parquet").as_posix()
            ],
        ).df()
        normalized = cn_store.read_daily(["600519.SH"], JAN_START, JAN_START)
        assert int(normalized["volume"].iloc[0]) == int(raw["volume"].iloc[0]) * 100

    def test_us_volume_unchanged(self, us_store: MinuteStore) -> None:
        raw = duckdb.sql(
            "SELECT volume FROM read_parquet(?) WHERE trade_date = '2024-01-03'",
            params=[(FIXTURES / "us_equity" / "minute_db" / "daily" / "AAPL.parquet").as_posix()],
        ).df()
        normalized = us_store.read_daily(["AAPL"], date(2024, 1, 3), date(2024, 1, 3))
        assert int(normalized["volume"].iloc[0]) == int(raw["volume"].iloc[0])

    def test_cn_vwap_is_per_share_price(self, cn_store: MinuteStore) -> None:
        minute = cn_store.read_minute(["600519.SH"], JAN_START, JAN_START)
        traded = minute[minute["volume"] > 0]
        ratio = (traded["vwap"] / traded["close"]).dropna()
        assert ((ratio > 0.9) & (ratio < 1.1)).all()
        implied = traded["amount"] / traded["volume"]
        assert (implied - traded["vwap"]).abs().max() < 0.01

    def test_cn_trade_count_is_null_not_zero(self, cn_store: MinuteStore) -> None:
        minute = cn_store.read_minute(["600519.SH"], JAN_START, JAN_START)
        assert minute["trade_count"].isna().all()

    def test_us_trade_count_present(self, us_store: MinuteStore) -> None:
        minute = us_store.read_minute(["AAPL"], JAN_START, JAN_START)
        assert minute["trade_count"].notna().all()

    def test_us_vwap_is_null(self, us_store: MinuteStore) -> None:
        minute = us_store.read_minute(["AAPL"], JAN_START, JAN_START)
        assert minute["vwap"].isna().all()

    def test_cn_index_flags_and_null_vwap(self, cn_store: MinuteStore) -> None:
        minute = cn_store.read_minute(["000300.SH"], JAN_START, JAN_START)
        assert minute["is_index"].all()
        assert (minute["volume"] == 0).all()
        assert minute["vwap"].isna().all()
        assert minute["amount"].notna().all()


class TestSessionMarking:
    """时段打标不过滤：A 股整日 241 根全为常规时段，美股盘前盘后保留且标记为 False。"""

    def test_cn_full_day_bar_count(self, cn_store: MinuteStore) -> None:
        minute = cn_store.read_minute(["600519.SH"], JAN_START, JAN_START)
        assert len(minute) == 241
        assert minute["is_rth"].all()
        labels = minute["minute_label"].tolist()
        assert labels[0] == "0930"
        assert labels[-1] == "1500"
        assert "1300" not in labels

    def test_us_premarket_kept_but_marked(self, us_store: MinuteStore) -> None:
        minute = us_store.read_minute(["AAPL"], date(2024, 1, 3), date(2024, 1, 3))
        assert len(minute) > 390
        rth = minute[minute["is_rth"]]
        assert len(rth) == 390
        assert rth["minute_label"].iloc[0] == "0931"
        assert rth["minute_label"].iloc[-1] == "1600"
        premarket = minute[minute["minute_label"] < "0931"]
        assert not premarket.empty
        assert not premarket["is_rth"].any()


class TestUniverse:
    """头部流动性选池：剔除指数，规模正确。"""

    def test_cn_universe_excludes_index(self, cn_store: MinuteStore) -> None:
        selected = build_universe(cn_store, date(2024, 1, 31), 3)
        assert len(selected) == 3
        assert "000300.SH" not in selected
        assert "600519.SH" in selected

    def test_us_universe_excludes_fund_vehicles(self, us_store: MinuteStore) -> None:
        selected = build_universe(us_store, date(2024, 1, 31), 3)
        assert len(selected) == 3
        assert "SPY" not in selected
        assert set(selected).issubset({"AAPL", "MSFT", "NVDA", "TSLA", "AMZN"})


class TestManifest:
    def test_manifest_fields(self, cn_store: MinuteStore) -> None:
        manifest = cn_store.manifest()
        assert manifest.market_id == "cn_ashare"
        assert manifest.adjustment_available is False
        assert manifest.coverage_start == "2024-01-02"
        assert manifest.coverage_end == "2024-12-31"
        assert manifest.fingerprint == cn_store.manifest().fingerprint

    def test_us_manifest_adjustment_available(self, us_store: MinuteStore) -> None:
        assert us_store.manifest().adjustment_available is True
