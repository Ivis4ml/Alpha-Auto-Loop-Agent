"""信号注入评估框架：对研究循环做配对诚实性评估（发现率与误报率）。

本包依赖 alphaloop；alphaloop 禁止反向依赖本包（import-linter 契约强制）。
注入强度、信号规格等真值只存在于本框架的私有工作目录并附 HMAC 密封，
研究进程以子进程运行且其配置不含任何真值信息。
"""

from evalharness.harness import HonestyPoint, HonestyReport, InjectionHarness
from evalharness.inject import materialize_injected_dataset
from evalharness.truth import read_sealed_truth, write_sealed_truth

__all__ = [
    "InjectionHarness",
    "HonestyPoint",
    "HonestyReport",
    "materialize_injected_dataset",
    "read_sealed_truth",
    "write_sealed_truth",
]
