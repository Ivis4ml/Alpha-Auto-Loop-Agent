"""因子表达式系统。

因子以 JSON 规格表述，在白名单算子上经解释器求值：不存在任意代码
执行路径，未知算子立即报错。规格经规范化哈希得到 factor_id，任何参数
变化产生新 id，永不覆盖。泄漏防护三层：静态校验（lint，确定性规则
引擎的一部分）、求值器只使用截至当前行的窗口、动态截断重算测试
（leakage.no_lookahead_test）。
"""

from alphaloop.core.dsl.evaluate import evaluate
from alphaloop.core.dsl.grammar import GRAMMAR_VERSION, FactorSpec
from alphaloop.core.dsl.leakage import no_lookahead_test
from alphaloop.core.dsl.lint import LintError, lint_spec
from alphaloop.core.dsl.operators import OPERATORS, OperatorDef

__all__ = [
    "GRAMMAR_VERSION",
    "FactorSpec",
    "OperatorDef",
    "OPERATORS",
    "evaluate",
    "lint_spec",
    "LintError",
    "no_lookahead_test",
]
