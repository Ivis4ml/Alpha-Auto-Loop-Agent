# 科研诚信治理：任务书 3.1 各条款的承载机制与验证方式

本文档逐条说明任务书第 3.1 节八条效果性约束（外加三条附加条款）在本
系统中的实现位置与验证方式。测试名指 `tests/` 下的用例。

## 第 1 条：配对诚实性（信号存在时能找到，不存在时不虚报）

- 承载：`evalharness/` 信号注入评估框架。在真实日线数据上按已知强度
  beta 改写收益路径（`inject.py`），beta = 0 对照组走完全相同的物化
  管线；研究循环以子进程运行（`apps/cli run-loop`），其配置经真值线索
  片段断言过滤，且 import-linter 契约静态禁止 `alphaloop` 依赖
  `evalharness`。真值（beta、信号规格）存于框架私有目录并附 HMAC 密封
 （`truth.py`）。
- 隐藏机制的威胁模型声明：同机同用户环境下这是治理边界，不是密码学
  安全边界；保证手段是静态依赖禁止、进程隔离与配置过滤，密封用于
  事后篡改审计。
- 验证：`test_evalharness.py::TestHonestyCurve`——强度网格
  {0, 0.02, 0.04, 0.08} 各两次运行，发现率 0 → 0 → 1 → 1 单调不减，
  对照误报率为零；beta = 0 对照价格与源数据逐值一致。真实全量数据上
  的验收演练（`docs/acceptance.md` 第 5 条）显示无注入信号时双市场
  均正确报告"未发现"。

## 第 2 条：规则冻结

- 承载：`alphaloop/govern/freeze.py`。运行配置的全部规则项（语法版本、
  字段范围、预算、门控、切分、生成器、提示词版本、特征面板引用等，
  见 `RunConfig.freeze_rules`）在进入保留段验证之前哈希汇总写入冻结
  清单；验证入口重算比对，不一致抛 `FreezeViolation` 并指名改动项；
  清单禁止覆盖。
- 验证：`test_governance.py::TestFreeze`——改动一项配置即被指名检出。

## 第 3 条：样本外资格以触碰记录界定

- 承载：`alphaloop/govern/sealed.py` 一次性消费账本。封存键由冻结哈希、
  数据集指纹、评估窗口与排序后的候选集合构成（与调度顺序无关），消费
  动作是 O_CREAT | O_EXCL 原子文件创建；同键重复评估被稳定拒绝，
  与日历时间无关。
- 验证：`test_governance.py::TestSealedLedger`——键顺序无关性、二次消费
  拒绝、8 进程并发争抢恰一赢家；`test_loop.py` 中同配置第二次运行被
  封存账本拒绝。

## 第 4 条：全量候选计数

- 承载：`alphaloop/govern/trials.py` 试验账本。候选在生成后、评估前
  登记 planned（含语言模型解析产出的全部规格、每一轮、被静态筛查
  拒绝的在内）；FDR 的 p 值向量长度等于全量 planned（未评估者记 1.0），
  DSR 的 n_trials 取全量 planned。
- 验证：`test_governance.py::TestTrialLedger`；`test_loop.py` 断言
  `gate_report.n_planned_total` 等于提出总数。

## 第 5 条：双模式反馈治理（用户拍板：双模式并存）

- 承载：`alphaloop/loop/feedback.py` 与引擎多轮结构。模式 A：单轮强制，
  生成端零验证统计。模式 B：多轮生成，反馈只来自训练段的离散桶
 （结果类别、IC 符号桶、换手桶），经受控词表自检杜绝连续统计量；
  保留段信息不进入生成端；门控紧缩（FDR q 减半，DSR 下限强制启用）。
  运行配置记录所用模式。
- 验证：`test_llm.py::TestDualModeLoop`——第二轮提示词含离散桶且无
  数值；`test_loop.py` 模式与策略不一致被拒绝。

## 第 6 条：六维对照

- 承载：`RunConfig.protocol_id` 固化语法版本、字段范围、候选预算、
  算力预算、门控配置、切分配置六个维度；模板与随机两个无语言模型
  生成器作为对照基线臂与语言模型生成器共用同一循环与门控。
- 验证：`test_loop.py::TestConfig::test_protocol_id_covers_six_dimensions`。

## 第 7 条：point-in-time

- 承载：新闻库区分 publish_ts 与 visible_ts（`news/store.py`），检索的
  PIT 过滤单点实施于 SQL 候选层且只依据 visible_ts，ingest_ts 仅审计；
  情绪面板按可见时刻与收盘截止归入下一可交易时点（`news/sentiment/
  panel.py`），manifest 声明快照起点与覆盖缺口；阅读数等快照字段
  在 schema 层声明禁入因子。
- 验证：`test_news.py::TestRetrieval::test_pit_boundary_excludes_future`；
  `test_sentiment_qa.py::TestPanel::test_pit_assignment_and_aggregation`。

## 第 8 条：成本标注

- 承载：回测报告字段显式区分未扣成本（gross）与已扣成本（net）口径；
  净收益门控与换手成本常驻管线（引擎门控无移除开关）；交易计划的
  预期净收益字段名即声明成本后口径（`expected_net_return_bps_after_cost`）；
  成本敏感性 0 × / 0.5 × / 1 × / 2 × 网格必含基准点。
- 验证：`test_backtest.py::TestCostSensitivity`、
  `test_execution.py`（计划字段断言）。

## 附加条款

- 生成与评审角色分离：`alphaloop/llm/roles.py` 双系统提示词；评审只输出
  结构化结论、无权改写候选、看不到任何验证统计
 （`test_llm.py::TestLlmReviewer::test_reviewer_sees_no_statistics`）。
- 确定性规则引擎：`alphaloop/loop/rules.py`——语法与白名单校验、字段
  合法性、参数边界、去重、回看上限、动态截断重算前视检测，全部不经
  语言模型；语言模型输出一律先过本引擎。
- 版本不覆盖：factor_id 为规格规范化哈希，参数变化即新标识；冻结清单
  与封存记录禁止覆盖；试验账本 append-only。
