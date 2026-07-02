"""中国 A 股市场描述。

口径依据 Alpha-Data `docs/DATA_GUIDE.md` §6 与任务书 2.1 节的核实结论：
时间戳为北京本地墙钟（tz-naive）、成交量单位为手（1 手 = 100 股）、
分钟与日线成交额为真实值、无复权因子（corp_actions 为空表）、行情
不做常规时段过滤（09:30 开盘集合竞价与 15:00 收盘集合竞价均计入）。
"""

from alphaloop.market.cn_ashare.spec import build_cn_ashare_spec

__all__ = ["build_cn_ashare_spec"]
