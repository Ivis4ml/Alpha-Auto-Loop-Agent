# Alpha-Auto-Loop-Agent

市场无关、大语言模型驱动的自动化因子研究与交易计划 Agent，同时覆盖美股与中国 A 股。

## 项目定位

以双市场分钟与日线行情（Alpha-Data MinuteDB）加新闻情绪面为输入，运行尽量少人工干预的研究与决策循环：

- 自动化因子研究循环：数据审计、因子提出、样本外验证、治理门控、策略草拟、回测评估，产出经得起统计检验的候选。
- 自动回测：双市场可复现回测，含统计显著性检验与成本敏感性分析。
- 交易计划：对头部流动性标的产出结构化选股与买卖计划，预留信号到订单的执行接口协议，当前阶段不连接任何真实券商。
- 新闻检索与情绪融合：自建新闻采集、point-in-time 检索与双轨情绪打分，情绪面板经统一契约进入选股决策。
- 对话交互：围绕标的与新闻的证据式问答，回答附新闻时间戳与来源引用。

完整需求见 `.prompt.md` 任务书；科研诚信治理（信号注入配对验证、规则冻结、全量试验计数、双模式反馈治理等）是本项目的价值内核，见任务书第 3.1 节。

## 仓库结构

```
alphaloop/      核心平台，自底向上分层：infra、contracts、core、market、data、
                (govern | llm | execution)、loop；层间依赖由 import-linter 契约机械校验
news/           新闻子系统：采集、入库、实体链接、检索、情绪双轨、对话问答
apps/           应用装配层：HTTP 服务与命令行入口
gui/            前端（Vite + React + TypeScript + ECharts）
evalharness/    信号注入评估框架；alphaloop 禁止反向依赖本包
tools/          CI 检查脚本（分层绕过检测、市场中立断言）
docs/           文档与合规记录
```

## 快速开始

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"

# 执行一轮研究循环（配置文件示例见 tests/test_loop.py 的 RunConfig 构造）
.venv/bin/python -m apps.cli run-loop \
  --config run_config.json --data-root <minute_db 目录> \
  --out runs/run-001 --sealed-root runs_cache/sealed

# 构建前端并启动界面
(cd gui && npm install && npm run build)
.venv/bin/python -m apps.cli serve --runs-dir runs
```

文档：`docs/architecture.md`（架构与双市场差异收敛点）、
`docs/governance.md`（科研诚信条款逐条映射）、`docs/acceptance.md`
（验收对照）、`docs/compliance/`（财联社条款核实记录）。

## 开发环境

本地执行与 CI 相同的检查：

```bash
.venv/bin/ruff check .
.venv/bin/mypy
.venv/bin/lint-imports
.venv/bin/python tools/check_import_bypass.py
.venv/bin/python tools/check_market_neutral.py
.venv/bin/pytest -q
```

## 工程纪律

- 特性分支加 Pull Request，不直接提交 main。
- 代码带类型注解与异常处理，不留 TODO 占位。
- 文档与界面文案使用中文，采用正式技术文档文体。
- 市场无关层禁止出现具体市场的身份、规则与默认值，由 `tools/check_market_neutral.py` 强制。
