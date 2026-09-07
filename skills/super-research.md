# 超级投研（Super Research）—— 全场景股票研究编排器

## 设计哲学：编排器，不是大杂烩

本 skill **不重复实现**任何子 skill 的功能，而是作为"母指挥官"编排已有 skill 家族按阶段上场。三个核心原则：

1. **条件路由**：专项深挖不是无差别全跑——按公司类型、红队发现的问题、时间窗口决定开哪些专项（详见 `references/routing-map.md`）
2. **双模式**：full（全流程 6-10 个 Agent，60-90 分钟）/ lite（3-4 个 Agent，20-30 分钟）——对齐"每日仅 1 只 full"的成本红线，默认 full，用户说"快速/lite"时走 lite
3. **单一收敛**：所有阶段产出最终收敛到一个裁判——**输出单一的内在涨幅数字**（附三情景交叉验算支撑），不是一堆平行报告

## 五阶段流水线

### S0 体检初筛（快速过滤，lead 直接做或 1 个 Agent）

调用 **`quality-screen`**（去劣筛选 7 条硬指标：10年ROE<8%、5年FCF为负、利息覆盖<2、长期毛利率<15%、经营现金流/净利<0.7、长期净利率<5%、5年股本膨胀>20%，另有豁免规则）。

- **不通过** → 直接出局，输出"该标的被去劣指标排除，理由 XX"——**省掉后面全部成本**，这是本阶段的价值
- **通过/灰色** → 记录豁免与灰点，进入 S1
- 同时调 `financial-data` 规范完成一手数据获取（价格/市值/核心财报）

产出：`reports/{code}{名称}-S0体检.md`

### S1 深度研究（核心，调用 `investment-team-v2 --mode evidence-only`）

**不重复造轮子**——执行 v2 的分析师层 + 辩论层，**evidence-only 模式**（跳过 v2 内部的定价审计与裁判站队——S1 只产出证据，S3 只建模型，S5 只裁决，防止提前裁判与后续重复计价）：
- 分析师层：四大师（business/financial/industry/risk），只研究不给结论
- 辩论层：Bull/Bear 红队对抗（角色锁定、逐条反驳、论点收敛即停）
- 产出 **Evidence Pack**：business/financial/industry/risk_evidence + bull_arguments + bear_arguments + fatal_risks + catalyst_candidates + disputed_assumptions——**不含价格判断/目标价/概率加权回报/仓位**

产出落盘沿用 v2 规范：`reports/{code}{名称}-商业模式分析.md`、`-财务估值.md`、`-行业竞争.md`、`-风险评估.md`、`-多头立论.md`、`-空头猎杀.md`、`-evidence-pack.json`

### S2 专项深挖（条件路由，按需触发 0-N 个）

**这是本 skill 的灵魂**——不是全跑，是"对症下药"。由 lead 根据 S1 的结果（红队发现的问题、公司类型、时点）按下表触发：

| 专项 skill | 触发条件（命中才跑） | 干什么 |
|-----------|---------------------|--------|
| **`bottleneck-hunter`**（供应链瓶颈） | 公司属超级趋势产业链（AI算力/能源转型/国防/半导体/太空）；或属制造业/上游材料 | 用"瓶颈思维"分析公司在供应链中的咽喉地位——是不是"行业扩张时先不够用的那一环" |
| **`management-deep-dive`**（管理层纵深） | 红队 Bear 发现治理疑点（减持/质押/关联交易/问询函/频繁换帅） | 挖实控人背景、历史资本运作、诚信污点 |
| **`small-cap-diligence`**（小盘尽调） | C 级信息稀缺标的（小盘/冷门/新上市） | 冷信息挖掘（应收/存货突变、股东户数、大宗/龙虎榜、公告分类） |
| **`earnings-team`**（财报精读） | 财报发布 ≤30 天 | 四大师并行精读最新财报 |
| **`news-pulse`**（异动归因） | 近期股价异动 ≥±5% | 4 Agent 侦察异动真因，判断是否触发论文重审 |

**规则**：默认只触发 0-2 个最相关的专项；每个专项产出落到 `reports/{code}{名称}-S2{专项名}.md`，并作为**增量证据喂给 S3 定价审计**（不是另起炉灶）。

### S3 定价与回报（三层，调用 `expectation-arb` + `forward-return`）

汇总 S1 + S2 全部证据，分三步（职责分离，消除重复计价）：

- **S3A 市场现在隐含了什么**（调用 `expectation-arb`）：反推市场隐含假设（Reverse DCF / Reverse Earnings Model；价值型拆 PEV、成长型拆 PE、周期型三口径 PE），输出 Consensus vs Price-Implied 预期差审计表、定价状态矩阵（已计价/过度/未计价/计价不足）
- **S3B 我们认为未来会发生什么**（调用 `forward-return` 的 Business Driver Tree）：拆解业务变量（Driver→Revenue→Margin→EBIT→NetIncome→FCF），催化剂对象化（概率+时点+影响+市场已定价+认识时滞），防逻辑陷阱逐环验证
- **S3C 各期限回报是多少**（调用 `forward-return` 的收益率期限结构引擎）：输出 6m/1y/2y/3y/5y 收益率分布表 + attribution 拆解（R_earnings/rerating/dividend/buyback/FX）+ `data/forecasts/{code}.json` 机器可读落盘

关键问题：**市场已经定价了多少（S3A）vs 我们认为未来会怎样（S3B），在各期限上的赔率差是多少（S3C）？**

### S4 买入前 Checklist（调用 `investment-checklist`）

执行巴菲特买入前六关检查，作为终审前的最后一道筛子。产出每关通过/部分/不通过 + 说明。

### S5 终审输出（team-lead 裁判，只裁决不重新建模）

综合 S0-S4，输出：

1. **Primary Horizon Expected CAGR（核心交付，v1.3 升级）**：
   - "单一内在涨幅 +40%"已信息不足——6个月+40% 和 3年+40% 是两笔质量完全不同的投资
   - 核心数字改为：**Primary Horizon Expected CAGR + Expected HPR + P(loss) + Bear CAGR + Extreme downside**
   - 示例：主决策期限 2 年 → Expected CAGR 21.4% / Expected HPR 47.4% / P(loss) 18% / Bear CAGR -9% / Extreme downside -43%；兼容保留 Fundamental upside（如 +47%）
   - **防过拟合红线：primary_horizon 必须在计算收益前先选定**（`selected_before_return_calculation: true`，依据=催化剂 base 兑现期+认识时滞+业务能见度）；6m/1y/2y/5y 只是敏感性检查——**禁止看完曲线挑最高 CAGR 的期限宣布"最佳持有期"**
2. **单一内在涨幅**（兼容保留）：Fundamental upside = S3C 选定的 return distribution 内在价值 ÷ 现价 − 1
3. **概率加权回报**：直接采用 S3C 的收益率期限结构（6m/1y/2y/3y/5y + attribution），乐观/基准/悲观/极端四档（悲观必填），概率加权年化 + 累计分布区间
4. **证伪清单**（每条利空必须有数据支撑）
5. **加仓/减仓信号清单**（可证伪的事件）
6. **thesis-tracker 衔接**：将证伪清单与监控指标写入 `reports/{code}{名称}-thesis.md`（建立/更新投资论文）；catalyst 状态变化时触发 `forward_return --refresh` 重算收益率曲线，决定持有/加仓/轮出
7. 数据校正小节 + "不构成投资建议"声明

产出：`reports/{code}{名称}-super终审.md` + `data/forecasts/{code}.json`

## 与其他 skill 的关系（调用图）

```
super-research（本编排器）
  ├─ quality-screen        → S0 体检
  ├─ financial-data        → S0 数据
  ├─ investment-team-v2    → S1 核心（内部含四大师+红队）
  │    └─ small-cap-diligence / management-deep-dive / news-pulse（Bear 武器库，S1 内部已调）
  ├─ bottleneck-hunter     → S2 专项（条件：产业链型）
  ├─ management-deep-dive  → S2 专项（条件：治理疑点）
  ├─ small-cap-diligence   → S2 专项（条件：C级稀缺标的）
  ├─ earnings-team         → S2 专项（条件：财报≤30天）
  ├─ news-pulse            → S2 专项（条件：股价异动）
  ├─ expectation-arb       → S3A 市场隐含预期（Reverse DCF/Consensus vs Price-Implied）
  ├─ forward-return        → S3B 业务路径 + S3C 收益率期限结构（Business Driver/Catalyst/6m-5y回报曲线）
  ├─ investment-checklist  → S4 买入前检查
  └─ thesis-tracker        → S5 持有期衔接
```

**注意**：`small-cap-diligence`/`management-deep-dive`/`news-pulse` 在 v2 里已是 Bear 的武器库——若 S1 已覆盖则 S2 跳过，避免重复研究（成本红线）。S2 只在"S1 没覆盖到、或需要单独深挖"时才触发。

## 执行纪律

1. **成本红线**：full 模式总 Agent 数 ≤10；lite 模式 ≤4；每日 full 不超过 1 只（对齐仓库 `AGENTS.md`）
2. **先 S0 再研究**：体检不过直接出局，不浪费深度研究成本
3. **条件路由不滥用**：S2 默认 0-2 个专项，除非用户明确要"全开"
4. **先备锚点再派工**：lead 先取价格/市值/核心财报，写进每个 Agent 任务书
5. **429/降级**：逐个 spawn；限流期间 lead 按框架直接合成并标注「lead 合成」
6. **单一涨幅必须给**：终审禁止只给区间——必须收敛到一个数字（附三情景支撑）
7. 仅供学习研究，不构成投资建议

## WorkBuddy 执行适配（v1.1 实战沉淀）

1. **组队机制**：WorkBuddy 中给子 Agent 命名（name 参数）前**必须先 TeamCreate 建团**，否则 spawn 报 "No active team found"。流程：TeamCreate → Agent（name + run_in_background，不带 team_name）→ 收结果 → TeamDelete 清场。四大师可用一条消息并行 spawn。
2. **lead 锚点也要标置信度**：lead 写进任务书的每个数字/事实，未核实的必须标 `[待验证]` 并授予 Agent 纠错权（"与官方信源冲突时以官方为准并回报"）。实战教训：v1.0 运行中 lead 把"越南产能"写进全部任务书，被 biz-analyst 用官网核实推翻（实际为苏州/天津/波兰）——错误锚点会污染整条流水线。
3. **红队第二轮互驳用 SendMessage 续跑**，不重新 spawn：第一轮双方并行立论落盘后，lead 把对方立论要点 + 收口指令发给两个已完成 worker（SendMessage 继续同 agent，保留其上下文），要求各自追加「第二轮：对 X 方立论的回应」一节并逐条标【坚持】/【让步】。成本省一半，收敛质量更高。
4. **S2 并入任务书**：earnings-team/ bottleneck-hunter/ management-deep-dive/news-pulse/small-cap-diligence 的核查动作直接写进四大师与 Bear 的任务书（fin-analyst 兼精读财报、industry-analyst 兼瓶颈思维、Bear 兼治理/异动/筹码猎杀），full 模式 6 个子 Agent 即可跑完全流程，避免重复研究。
5. **lead 回报纪律（v1.2 新增）**：向用户汇报或写日志时，**只综合实际收到的 teammate 回报，绝不预填/预测 worker 结果**。实战教训：v1.1 运行中 lead 在等待红队第二轮时把 bull "预期结果"（涨幅+18%/可证伪线）当成实报写进日志与回复，下一轮才发现 bull 实际未回报——编造的数字一旦进入日志/终审会被当作证据污染裁判。正确做法：未收到回报一律写"回报中"，等 agent-notification 到达再综合。
6. **超时催办有明确对象与剩余清单**：同批派工中某个 worker 明显落后于同伴（如 bear 2 分钟收口而 bull 长时间无响应）时，用 SendMessage 催办并在消息里带齐：对方最终立场摘要 + 要求其回应的裁决点 + 剩余交付清单，避免 worker 只回一句"收到"就再无下文。
7. **非 A 股标的的 S0 适配（v1.2 新增）**：美股/中概标的跑七条体检时——上市历史不足 10 年按"结构性豁免"处理（如 PDD 2018 上市）；VIE/退市/审计等结构性风险不作 S0 否决项、转记 S1 风险重点；任务书必须包含汇率敏感性（给出三档汇率对目标价影响）与 ADR 折算比例（如 PDD 1 ADS=4 股）；同一锚点纠错更易频发（口径混乱：回购累计 vs 当年、美国 vs 全球用户口径），要求每个 worker 独立核实并在报告开头回报纠错。

---

# 条件路由表 —— S2 专项深挖触发规则

> super-research 的核心：专项不是全跑，是"对症下药"。lead 在 S1（investment-team-v2）结束后，根据发现的问题按下表决定开哪些专项。默认 0-2 个。

---

## 路由决策树

```
S1 红队辩论结束后，逐项检查：

Q1: 公司是否属于超级趋势产业链？
   (AI算力/数据中心/能源转型/国防/半导体/太空/新能源链/制造业上游材料)
   ├─ 是 → 触发 bottleneck-hunter
   └─ 否 → 跳过

Q2: 红队 Bear 是否发现治理疑点？
   (减持/质押/关联交易/问询函/频繁换帅/历史资本运作存疑)
   ├─ 是 → 触发 management-deep-dive
   └─ 否 → 跳过

Q3: 标的是否 C 级信息稀缺？
   (小盘/冷门/新上市/研究覆盖极少)
   ├─ 是 → 触发 small-cap-diligence
   └─ 否 → 跳过

Q4: 财报是否 30 天内刚发布？
   ├─ 是 → 触发 earnings-team（财报精读）
   └─ 否 → 跳过

Q5: 近期是否有股价异动 ≥±5%？
   ├─ 是 → 触发 news-pulse（异动归因）
   └─ 否 → 跳过
```

---

## 各专项的输入/输出/复用方式

### bottleneck-hunter（供应链瓶颈猎手）

- **触发**：公司属超级趋势产业链，或制造业/上游材料/设备
- **输入**：公司名 + 所属趋势（如"AI基础设施"）
- **干什么**：用"瓶颈思维"分析——这家公司在供应链的哪一环？是不是"行业扩张时先不够用的那一环"？如果是，它是不是"涨价链条"的受益者？
- **关键问题**：第一层瓶颈（GPU/HBM/电力）已被充分定价，真正的 alpha 在第二、三层（光模块、衬底、载板、特殊材料）——本标的属于第几层？
- **输出**：`reports/{code}{名称}-S2瓶颈.md`，含"瓶颈地位判断 + 是否涨价受益"
- **注意**：bottleneck-hunter 原版输入是"超级趋势"不是单只股票——本路由是**借用其方法论**适配到单股分析，不是直接调原 skill

### management-deep-dive（管理层纵深）

- **触发**：Bear 发现治理疑点
- **干什么**：挖实控人背景、历史资本运作、减持/质押记录、关联交易、诚信污点（万峰案式问题）
- **输出**：`reports/{code}{名称}-S2管理层.md`
- **复用**：若 S1 的 risk-assessor（李录视角）已覆盖则跳过

### small-cap-diligence（小盘股尽调）

- **触发**：C 级信息稀缺标的
- **干什么**：冷信息挖掘（财务时间序列突变、股东户数、大宗/龙虎榜、公告分类、问询函）
- **输出**：`reports/{code}{名称}-S2尽调.md`
- **复用**：v2 的 Bear 武器库已调用过则跳过（避免重复）

### earnings-team（财报精读）

- **触发**：财报发布 ≤30 天
- **干什么**：四大师并行精读最新财报，补充"财报附注"级的增量发现
- **输出**：`reports/{code}{名称}-S2财报精读.md`

### news-pulse（异动归因）

- **触发**：近期股价异动 ≥±5%（单日）或 ±10%（一周）
- **干什么**：4 Agent 侦察异动真因（公司事件/监管/行业/情绪），判断是否触发论文重审
- **输出**：`reports/{code}{名称}-S2异动.md`
- **价值**：区分"基本面驱动的合理波动" vs "情绪/资金驱动的噪音"——噪音不触发论文重审

---

## 去重规则（成本红线）

| 情况 | 处理 |
|------|------|
| S1 的 Bear 已调用某专项作为武器库 | S2 跳过该专项 |
| S1 的某大师报告已覆盖专项内容 | S2 跳过 |
| 用户明确要"全开专项" | 全部触发（但提示成本） |
| 无任何触发条件命中 | S2 整个跳过，直接进 S3 |
