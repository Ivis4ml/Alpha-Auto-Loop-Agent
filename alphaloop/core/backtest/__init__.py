"""回测引擎：向量化日线组合模拟、成本模型、显著性与稳健性分析。

执行约束（可交易性、结算延迟、复权、除权嫌疑剔除）全部经市场描述
注入，本包不含任何按市场分支的判断。收益结论强制区分未扣成本与
已扣成本两个口径。
"""

from alphaloop.core.backtest.costs import CostModel, cost_model_from_execution
from alphaloop.core.backtest.engine import BacktestReport, run_backtest
from alphaloop.core.backtest.metrics import fama_macbeth, ic_series
from alphaloop.core.backtest.sensitivity import cost_sensitivity

__all__ = [
    "CostModel",
    "cost_model_from_execution",
    "BacktestReport",
    "run_backtest",
    "ic_series",
    "fama_macbeth",
    "cost_sensitivity",
]
