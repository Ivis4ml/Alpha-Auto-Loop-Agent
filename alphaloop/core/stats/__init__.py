"""统计检验集合：自相关稳健 t 检验、多重检验修正、过拟合诊断。"""

from alphaloop.core.stats.fdr import bh_fdr
from alphaloop.core.stats.nw import newey_west_mean_t
from alphaloop.core.stats.pbo import probability_of_backtest_overfitting
from alphaloop.core.stats.sharpe import deflated_sharpe_probability

__all__ = [
    "newey_west_mean_t",
    "bh_fdr",
    "deflated_sharpe_probability",
    "probability_of_backtest_overfitting",
]
