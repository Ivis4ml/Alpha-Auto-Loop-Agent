# 验收对照：任务书第 7 节六条标准

验收日期：2026-07-02。测试总数 237 项（pytest），另有 ruff、mypy(strict)、
import-linter 五契约、两道自研 CI 检查全绿。

## 7.1 市场无关：同一套逻辑服务双市场，新增市场只加描述

- 核心逻辑（core、data、govern、llm、execution、loop、contracts、infra
  及 market 层顶层文件）无按市场分支的判断，由
  `tools/check_market_neutral.py` 机械强制（禁止具体市场身份、结算规则、
  涨跌幅制度词、后缀与六位代码字面量、具体基准标的；提示词模板同样
  纳入扫描）。
- 市场差异收敛为 `alphaloop/market/` 的可插拔描述（日历、口径声明、
  执行声明、可交易性、公司行动、选池六个组件）；新增市场即新增一个
  子包并在装配层注册，`test_market_specs.py::TestSpecShape` 断言两份
  描述满足同一协议面。
- 证据：`test_data_store.py::TestCanonicalSchema`（双市场同 API 同
  schema）、`test_backtest.py`（同一引擎跑双市场）、
  `test_loop.py::TestLoopEndToEnd::test_both_markets_complete`。

## 7.2 3.1 八条约束逐条落实

见 `docs/governance.md` 的逐条映射（机制位置 + 验证用例）。

## 7.3 带证据时间戳的对话问答，信息不足如实说明，不用未来信息

- `news/qa.py`：回答附引用（来源 + 发布/可见时间戳）；【新闻证据】与
  【背景知识】分区，背景知识显式冠名；证据不足走固定模板如实说明；
  as_of 给定时首行申明时点边界，检索只经 visible_ts 过滤。
- 证据：`test_sentiment_qa.py::TestQa` 四项用例（不足模板、PIT 首行
  申明与引用边界、证据/背景分区、问题内标的识别）。

## 7.4 双市场交易计划与预留执行接口，不接真实券商

- `alphaloop/execution/`：TradePlan（来源追溯固化）→ 纯函数编译为
  OrderIntent（按市场执行声明取整与注入约束）→ ExecutionAdapter 协议，
  当前唯一实现为记录型 JournalingAdapter；注册表拒绝为不支持实盘
  适配的市场注册实盘适配器（双市场合规不对称性预留）。
- 证据：`test_execution.py::TestTradePlanEndToEnd`——注入信号下双市场
  端到端产出计划，A 股意图整手、最短持有 1 日、含涨跌幅规则约束，
  美股无此约束。

## 7.5 无信号设定下正确报告"未发现"；估算值显式标注

- 配对诚实性：`test_evalharness.py` 发现率-强度单调曲线 + 对照零误报。
- 真实全量数据演练（本机，2026-07-02）：模板生成器 16 个候选、top-50
  流动性股票池、2024-01 至 2025-03 训练、2025-04 起保留段，双市场
  循环分别以 3 秒与 9 秒完成，全部候选未通过门控，系统输出
  `no_findings: true` 而非虚报（简单模板因子在严格门控下不应存活，
  这是预期行为）。
- 估算标注：美股日线成交额恒标 `amount_is_estimated=True` 并传染至
  容量估计与交易计划字段；A 股无复权数据以 `adjustment_available=false`
  声明并附除权嫌疑日标注与剔除计数；情绪面板 manifest 披露七成覆盖
  上限与模型回算占比；缺失字段一律空值不伪造。

## 7.6 同一运行配置结果可复现

- 运行配置快照（含数据集指纹、语法与提示词版本、模型标识、种子）
  运行开始即写入；LLM 调用经三态缓存，replay 未命中抛错而非静默；
  随机性一律从根种子派生，CI 禁全局随机状态。
- 证据：`test_loop.py::test_rerun_same_config_is_reproducible`（四类
  产物逐字节一致）、`test_llm.py::test_replay_rerun_is_byte_identical`
 （断后端重跑含 LLM 内容逐字一致）、
  `test_sentiment_qa.py::test_replay_reproducible_with_llm_track`。

## 遗留边界（如实声明）

- 财联社新增抓取因条款核实结论不利而暂停，待用户决定
 （docs/compliance/cls_terms_review.md）；现有能力基于存量语料。
- 向量混合检索按计划推迟：真实语料检索金标集（需人工标注）就绪并
  给出相对关键词基线的提升数字之前不合入。
- 实体链接与检索的真实语料金标集（400 条 / 60-100 条）为人工标注
  任务，当前以合成金标集覆盖机制正确性。
- A 股复权数据源、外部指数成分表为后续里程碑（任务书待拍板项的
  既定取舍）。
