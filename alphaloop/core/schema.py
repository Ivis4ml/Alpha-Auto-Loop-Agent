"""规范数据口径：全平台数据面板的唯一权威定义。

读取层（data）负责把各市场的原始存储归一到本模块定义的口径；本模块只
声明契约，不做任何 IO。两市场原始口径的差异（时间戳语义、成交量单位、
成交额语义、公司行动表类型）在读取层消解，上层代码不再感知。

字段语义要点：

- ``ts_local``：bar 收盘时刻，tz-naive 的市场本地墙钟；具体时区由市场
  描述声明，本层不做跨市场时间对齐。
- ``volume``：统一为股数；原始单位不同的市场在读取层换算。
- ``amount``：成交额（当地货币）；``amount_is_estimated`` 为 True 表示
  该值由估算得出（例如 close 乘 volume 聚合），必须随值一路透传。
- ``trade_count``：源数据不提供时为空值，不得以 0 伪装。
- ``is_rth``：常规交易时段标记；读取层只打标不过滤行。
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "SCHEMA_VERSION",
    "ColumnSpec",
    "MINUTE_COLUMNS",
    "DAILY_COLUMNS",
    "CORP_ACTION_COLUMNS",
    "LISTING_COLUMNS",
    "column_names",
]

SCHEMA_VERSION = "1"


@dataclass(frozen=True, slots=True)
class ColumnSpec:
    """单列的规范定义。dtype 采用 pandas 的字符串表示。"""

    name: str
    dtype: str
    nullable: bool
    description: str


MINUTE_COLUMNS: tuple[ColumnSpec, ...] = (
    ColumnSpec("symbol", "string", False, "经市场描述归一后的标的代码"),
    ColumnSpec("trade_date", "object", False, "交易日（datetime.date）"),
    ColumnSpec("ts_local", "datetime64[us]", False, "bar 收盘时刻，市场本地墙钟，tz-naive"),
    ColumnSpec("minute_label", "string", False, "收盘时刻的 HHMM 标签，供时段判定"),
    ColumnSpec("open", "float64", False, "开盘价，未复权"),
    ColumnSpec("high", "float64", False, "最高价，未复权"),
    ColumnSpec("low", "float64", False, "最低价，未复权"),
    ColumnSpec("close", "float64", False, "收盘价，未复权"),
    ColumnSpec("volume", "int64", False, "成交量，统一为股"),
    ColumnSpec("amount", "float64", True, "成交额（当地货币），源不提供时为空"),
    ColumnSpec("amount_is_estimated", "bool", False, "成交额是否为估算值"),
    ColumnSpec("vwap", "float64", True, "分钟成交量加权均价，源不提供时为空，不伪造"),
    ColumnSpec("trade_count", "Int64", True, "成交笔数，源不提供时为空，不以 0 伪装"),
    ColumnSpec("is_rth", "bool", False, "是否常规交易时段（含该市场计入规则内的竞价时段）"),
    ColumnSpec("is_index", "bool", False, "是否指数类标的"),
)

DAILY_COLUMNS: tuple[ColumnSpec, ...] = (
    ColumnSpec("symbol", "string", False, "经市场描述归一后的标的代码"),
    ColumnSpec("trade_date", "object", False, "交易日（datetime.date）"),
    ColumnSpec("open", "float64", False, "开盘价，未复权"),
    ColumnSpec("high", "float64", False, "最高价，未复权"),
    ColumnSpec("low", "float64", False, "最低价，未复权"),
    ColumnSpec("close", "float64", False, "收盘价，未复权"),
    ColumnSpec("volume", "int64", False, "成交量，统一为股"),
    ColumnSpec("amount", "float64", True, "成交额（当地货币），估算与否见 amount_is_estimated"),
    ColumnSpec("amount_is_estimated", "bool", False, "成交额是否为估算值"),
    ColumnSpec("is_index", "bool", False, "是否指数类标的"),
)

CORP_ACTION_COLUMNS: tuple[ColumnSpec, ...] = (
    ColumnSpec("symbol", "string", False, "标的代码"),
    ColumnSpec("ex_date", "object", False, "除权除息日（datetime.date）"),
    ColumnSpec("split_ratio", "float64", True, "拆股比例，无拆股时为空"),
    ColumnSpec("cash_div", "float64", True, "每股现金分红，无分红时为空"),
)

LISTING_COLUMNS: tuple[ColumnSpec, ...] = (
    ColumnSpec("symbol", "string", False, "标的代码"),
    ColumnSpec("name", "string", True, "标的名称，读取层做全角与空白归一"),
    ColumnSpec("status", "string", True, "上市状态，源口径原样保留"),
    ColumnSpec("is_index", "bool", False, "是否指数类标的"),
)


def column_names(columns: tuple[ColumnSpec, ...]) -> list[str]:
    """返回规范列名列表，顺序即规范顺序。"""
    return [column.name for column in columns]
