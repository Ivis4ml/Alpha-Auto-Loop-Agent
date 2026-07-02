"""美股市场描述。

口径依据 Alpha-Data 的核实结论：时间戳为美东本地墙钟（tz-naive）、
成交量单位为股、分钟表无成交额、日线成交额为 close 乘 volume 的估算值、
复权因子可得（corp_actions 含拆股与分红）、分钟表含盘前盘后。
"""

from alphaloop.market.us_equity.spec import build_us_equity_spec

__all__ = ["build_us_equity_spec"]
