"""测试装配：基于金样本夹具构建双市场存取入口。"""

from __future__ import annotations

from pathlib import Path

import pytest

from alphaloop.data.calendar_source import scan_trading_days
from alphaloop.data.instruments import scan_fund_like_symbols
from alphaloop.data.minute_store import MinuteStore
from alphaloop.market.cn_ashare import build_cn_ashare_spec
from alphaloop.market.us_equity import build_us_equity_spec

FIXTURES = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture(scope="session")
def us_store() -> MinuteStore:
    root = FIXTURES / "us_equity" / "minute_db"
    spec = build_us_equity_spec(scan_trading_days(root), scan_fund_like_symbols(root))
    return MinuteStore(root, spec, dataset_id="us_equity_fixture")


@pytest.fixture(scope="session")
def cn_store() -> MinuteStore:
    root = FIXTURES / "cn_equity" / "minute_db"
    spec = build_cn_ashare_spec(scan_trading_days(root))
    return MinuteStore(root, spec, dataset_id="cn_equity_fixture")
