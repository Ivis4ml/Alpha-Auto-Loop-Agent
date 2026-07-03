# 架构说明

## 分层

自底向上七层，层间依赖由 pyproject.toml 的 import-linter 契约机械校验，
另有两道 AST/文本级检查（`tools/`）封堵动态 import 绕过与市场耦合：

```
alphaloop/
  infra/       L0 基础设施：内容哈希与指纹、原子 JSON/JSONL、事件总线、种子派生
  contracts/   L1 对外契约：特征面板 manifest 与指纹算法、市场日历只读视图
  market/      L2 市场描述：协议 + 注册表 + 通用日历（顶层市场中立），
               us_equity/ 与 cn_ashare/ 两个子包承载全部市场特有规则
  core/        L3 纯函数：规范 schema、因子 DSL（22 算子白名单 + 三层泄漏
               防护）、统计检验（NW/FDR/DSR/PBO）、回测引擎（约束注入）
  data/        L4 读取层：MinuteStore 规范化读取、数据集指纹、交易日推导、
               选池、特征面板装载
  govern/      L5a 治理：试验账本、冻结、一次性封存、能力凭证（禁依赖 llm）
  llm/         L5b 模型客户端：三态缓存、claude CLI 后端、双角色提示词
  execution/   L5c 执行：交易计划、订单意图编译、记录型适配器
  loop/        L6 研究循环：配置与六维协议、生成器（模板/随机/LLM）、
               评审、确定性规则引擎、反馈策略、引擎编排
news/          新闻子系统（只允许 import alphaloop 的 contracts/infra/llm）：
               SQLite+FTS5 库、实体链接、检索、情绪双轨、问答
apps/          装配层：市场装配、命令行（run-loop / serve）、HTTP 服务
gui/           前端（Vite + React + TS + ECharts，六视图，design tokens 双主题）
evalharness/   信号注入评估（依赖 alphaloop；反向依赖被契约禁止）
```

## 关键数据流

1. **研究循环**：数据审计 → 选池（截至训练段终点）→（多轮）候选提出 →
   静态筛查（规则引擎）→ 评审（可选，零统计可见）→ 样本内估计 →
   规则冻结 → 保留段验证（能力凭证 + 一次性封存消费）→ FDR + 成本 +
   DSR 门控 → 交易计划与订单意图。产物全部确定性落盘于运行目录，
   事件流供界面增量轮询。
2. **新闻到决策**：存量语料入库（PIT 字段）→ 实体链接 → 情绪双轨打分 →
   (symbol, trade_date) 面板 + manifest → 研究循环按指纹校验消费并把
   build_config_hash 登记进门控报告。
3. **评估闭合**：evalharness 物化注入数据集（beta = 0 对照同管线）→
   子进程运行研究循环 → 汇总发现率-强度曲线与对照误报率。

## 复现机制

- 运行配置快照（数据集指纹、schema/语法/提示词版本、模型标识、模式、
  种子、特征面板引用）随运行落盘。
- LLM 三态缓存：explore 写、auto 命中即取、replay 只读且未命中抛错。
- 随机性从根种子按组件名派生，无全局随机状态。
- 数据集两级指纹（路径粒度快速级 / parquet footer 严格级）。

## 双市场差异的收敛点

| 差异 | 收敛位置 |
| --- | --- |
| 时区与墙钟语义 | DataConventions.timezone（读取层不转换，声明制）|
| 成交量单位 | DataConventions.volume_multiplier（读取层统一为股）|
| 成交额语义 | amount_native_*（估算值以 amount_is_estimated 传染）|
| 复权可得性 | CorporateActionModel（美股事件累乘 / A 股恒 1 + 除权嫌疑标注）|
| 交易时段 | SessionSpec（A 股含开收盘集合竞价 241 根，美股 RTH 390 根）|
| 结算锚点 | ListedCalendar.settlement_lag_days（A 股 1，美股 0）|
| 涨跌幅制度 | TradabilityModel（A 股一字板不可买卖 / 美股全可交易）|
| 下单合规 | ExecutionProfile.supports_live_adapter（A 股仅计划输出）|
