# 投研团队 2.0（Investment Team v2）

## 设计哲学：为什么是"对抗"，不是"综合"

旧版 `investment-team` 是"综合路线"——四大师各看各的，team-lead 汇总。这条路有隐蔽陷阱：**汇总 Agent 倾向于和稀泥**。当它同时收到"看多"和"看空"信号，最安全的输出是"建议观望"——既不得罪多方也不得罪空方，而"不给明确建议"恰恰是 LLM 最不容易犯错的选择。结果是"正确的废话"。

v2 的解法是**结构化对抗**（复刻金融行业红队传统，源自 TradingAgents 的多空辩论机制）：
- 不是让一个 AI 综合信息，而是让两个 AI 互相对着干，逼出最强论据
- **角色锁定**：Bull 被分配"为买入找最强证据"的立场，Bear 被分配"为卖出找最强证据"的立场——不是"客观分析多空"，而是"为立场辩护"（法庭辩论逻辑：原告律师不负责客观评价被告，只负责找有罪的最强证据）
- **裁判强制站队**：能扛住空头猎杀的结论，才是能执行的结论

## 三层架构

```
┌─────────────────────────────────────────────────┐
│ 第一层：分析师层（四大师，价值投资框架）          │
│  business-analyst(段永平) / financial-analyst(巴菲特) │
│  industry-researcher(芒格) / risk-assessor(李录)     │
│  → 产出四份研究，但不给最终买卖结论                │
├─────────────────────────────────────────────────┤
│ 第二层：辩论层（红队，v2 新增，核心）             │
│  bull-researcher(多头)  ⟷  bear-researcher(空头)    │
│  → 锁定立场、多轮对抗、挖最强论据                  │
├─────────────────────────────────────────────────┤
│ 第三层：裁判层（team-lead，强制站队）             │
│  → 基于辩论最强论据给出明确方向 + 概率加权回报 + 仓位│
└─────────────────────────────────────────────────┘
```

关键变化：**四大师只做研究，不再"各给各的结论"；方向由 Bull/Bear 辩论后，裁判强制站队给出。** 这解决两个顽疾：①风险挖掘浅（risk-assessor 退化成 8 维度 checklist）②结论和稀泥（team-lead 输出"建议观望"）。

## 运行模式（standalone / evidence-only）

| 模式 | 何时用 | 包含步骤 | 产出 |
|------|--------|---------|------|
| **standalone**（默认） | 独立使用本 skill（用户直接 @/调用，要方向和回报） | 第 0→1→2→3→4 全流程（含定价审计、裁判站队、概率加权回报、仓位建议） | 完整终审报告 |
| **evidence-only** | 作为 `super-research` 的 S1 子模块（上游会做 S3 定价与回报、S5 终审） | 只跑第 0→1→2 步；**跳过第 3 步定价审计和第 4 步裁判**——不产生价格判断、目标价、概率加权回报、仓位 | **Evidence Pack**（见下） |

**为什么要双模式**：若 super-research 的 S1 调用 standalone 版，会出现"v2 内部先裁判/定价一次 → S3 再审计定价一次 → S5 再裁判一次"的重复计价。evidence-only 让 S1 只产出证据，S3 只建模型，S5 只裁决——**S1=Evidence, S3=Model, S5=Decision，彻底干净。**

**Evidence Pack 结构**（evidence-only 模式的唯一产出）：

```json
{
  "business_evidence": {},
  "financial_evidence": {},
  "industry_evidence": {},
  "risk_evidence": {},
  "bull_arguments": [],
  "bear_arguments": [],
  "fatal_risks": [],
  "catalyst_candidates": [],
  "disputed_assumptions": []
}
```

**约束**：Evidence Pack 中**不含** target price、intrinsic value、probability-weighted return、仓位建议。disputed_assumptions 记录 Bull/Bear 双方仍有分歧、未收敛的假设（供 S3B 建模时标注敏感性）。

## 执行流程

### 第 0 步：信息评级 + 口径校准

复用旧版"AI 研究偏见评估"（A/B/C 三级）决定研究策略，并对用户自带资料做口径校准（时点/单双季/分母/官方口径 vs 反推口径）。财务数据至少两个独立来源交叉验证。

### 第 1 步：分析师层（四大师并行）

沿用旧版四视角任务书（商业模式/财务估值/行业竞争/风险评估），但**删除各报告末尾的"总体结论/买卖建议"**——四大师只负责把各自维度的证据挖出来、摆上桌，方向判断交给辩论层。

**数据两包纪律（禁止混装）**——每个 Agent 任务书收到的是两个独立包：

| 包 | 内容 | 来源 | 禁止 |
|----|------|------|------|
| **Canonical Anchor Pack** | price（行情源）/ shares / market_cap（=price×shares 确定性计算）/ revenue / earnings / balance sheet / financial ratios | T0 原始披露、T1 结构化数据商（financial-data 四层体系） | Web 结果覆盖其中任何数字 |
| **Research Context Pack** | analyst consensus / news / interviews / competitor events / market narrative | T3 Web Research | 填充 Canonical Anchor Pack 中的任何数字 |

研究要求：财务数据双源交叉（T2 验证 canonical）；联网失败禁止伪装（报告顶部标注置信度降级）。

### 第 2 步：辩论层（红队，核心）

创建两个研究员角色，各自收到四份分析师报告，进行多轮对抗辩论。**完整任务书模板见 `references/red-team.md`**，要点：

| 角色 | 锁定立场 | 核心使命 |
|------|---------|---------|
| bull-researcher | advocating FOR（为买入辩护） | 为投资找最强证据：成长、护城河、正面指标；反驳空头的每一个担忧 |
| bear-researcher | advocating AGAINST（为卖出辩护） | **猎杀利空**：如果这只股票要跌 50%，最可能因为哪一条？挖财报造假信号、管理层诚信污点、业务颠覆路径 |

**辩论机制**（详见 `references/red-team.md` §3）：
- 轮次：Bull 先立论 → Bear 反驳 → Bull 回应 → Bear 收口，**论点收敛即停**（默认 2 轮封顶，控制成本）
- 每轮必须回应对方上一轮的具体论点，禁止自说自话
- Bear 空手而归是**合法成功**——但必须列出"我做了哪些猎杀动作、为何未发现致命项"

### 第 3 步：定价审计（衔接 expectation-arb）【仅 standalone 模式；evidence-only 跳过本步】

裁判在站队前，用 `expectation-arb` 的第 1 步补一道硬校验：反推当前价格隐含的假设（价值型拆 PEV 反推隐含收益率，成长型拆 PE 反推隐含增速），判断市场对 Bull/Bear 各自论据的定价程度。这一步把"多空辩论"和"市场预期"接起来——辩论决定方向，定价审计决定赔率。

### 第 4 步：裁判强制站队（team-lead）【仅 standalone 模式；evidence-only 跳过本步】

裁判 prompt 必须包含这句（对抗"安全偏好"）：

> "不要因为双方都有道理就给 Hold，必须站队。基于辩论中最强的论据，明确给出方向——买入/观望/回避（或五档 BUY/OVERWEIGHT/HOLD/UNDERWEIGHT/SELL），并说明'我倾向哪方、原因是什么'。"

最终报告结构：
1. 一句话结论（明确方向，禁止"建议关注"式废话）
2. 辩论纪要（Bull 最强论点 vs Bear 最强论点，逐条对照）
3. 定价审计（隐含假设反推 + 预期差）
4. 概率加权回报（乐观/基准/悲观/极端，悲观必填；概率加权年化 + 累计分布区间）
5. 仓位建议（配合双轨/闸门体系）+ 证伪清单（每条例空必须有数据支撑）
6. 数据校正小节 + "不构成投资建议"声明

## 与旧版及期望差 skill 的关系

- `investment-team`（旧版）：保留，用于快速四视角研究；本 skill 用于"要深挖、要方向明确"的场景，优先于旧版
- `expectation-arb`（预期差博弈）：本 skill 的第 3 步直接调用它的"反推隐含假设 + 定价审计"；如需专门做"市场已定价什么"的审计，单独用 expectation-arb
- 仓库既有深挖 skill 复用：bear-researcher 的利空挖掘武器库来自 `small-cap-diligence`（冷信息挖掘：应收/现金流背离、股东户数、问询函、质押、裁判文书）、`management-deep-dive`（诚信污点）、`news-pulse`（异动归因）——见 `references/red-team.md` §2

## 执行纪律

1. **数据两包分离**：Canonical Anchor Pack（价格/股本/市值/财务——T0/T1/确定性计算）与 Research Context Pack（共识/新闻/访谈/竞品/叙事——Web/T3）严格分开派发；Web 结果禁止覆盖前者任何数字；卖方观点属 Research Pack，不属锚点
2. **429 处理**：逐个 spawn（一条消息一个）；限流期间 lead 直接合成并标注，禁止编造 Agent 报告
3. **跨 Agent 互查**：lead 用自己的验证锚点裁决数据分歧，终稿列"数据校正"
4. **证伪清单红线**：每条利空必须落到可验证证据（财报科目异常/问询函/减持质押/裁判文书），模糊的"行业竞争加剧"不算数——防红队为否定而否定
5. **evidence-only 模式**：super-research S1 调用时必须显式传 `--mode evidence-only`；产出 Evidence Pack 后即止，禁止"顺手"补价格判断
6. 仅供学习研究，不构成投资建议

---

# 红队设计（Red Team）—— Bull/Bear 辩论层完整规格

> 本文件是 investment-team-v2 第二层（辩论层）的完整规格，源自 TradingAgents 多空辩论机制 + 新华实战提炼。

---

## §1 角色锁定的 Prompt 模板（必须逐字采用立场词）

**Bull Researcher（多头研究员）**：

```
You are a Bull Researcher advocating FOR investing in {公司}. Your task is to
build the strongest possible case for buying, emphasizing growth potential,
competitive advantages, positive catalysts, and any counter-argument against
the Bear's concerns.

Do NOT be "objective" — you are assigned a stance and your job is to find the
strongest evidence for it. When the Bear raises a risk, refute it with data,
not dismissal.

Resources available:
- 四份分析师报告（商业模式/财务估值/行业竞争/风险评估）
- 已核实的财务锚点数据
- 辩论历史与上一轮 Bear 的论点

Deliver: 你的最强多头论据，逐条反驳 Bear 的担忧。
```

**Bear Researcher（空头研究员 / 利空猎手）**：

```
You are a Bear Researcher advocating AGAINST investing in {公司}. Your task is
to build the strongest possible case for selling/avoiding, emphasizing growth
risks, competitive disadvantages, negative signals, and the scenario where
this stock falls 50%.

Do NOT be "objective" — you are assigned a stance. Your ONE core question:
"如果这只股票要暴跌 50%，最可能因为哪一条致命原因？"

猎杀清单（逐项排查，每项必须落到可验证证据）：
1. 财报造假/质量信号：应收账款与营收背离、经营现金流长期低于净利润、
   存货/在建工程异常、商誉减值风险、毛利率异常波动
2. 管理层诚信污点：历史减持/质押/关联交易/资本运作、监管处罚、问询函及回复
3. 业务颠覆路径：技术替代、渠道崩塌、政策扼杀、大客户流失
4. 财务脆弱性：高杠杆、债务到期、担保/或有负债、资金链
5. 对 Bull 论点的反驳：逐条指出其假设漏洞、过于乐观之处

Deliver: 你的最强空头论据 + 致命利空候选清单（按杀伤力排序，每条附证据）。
若未发现致命项，明确说明"我做了哪些猎杀动作、为何空手而归"——空手而归是合法成功。
```

---

## §2 利空猎手的武器库（复用仓库既有 skill）

Bear Researcher 的深挖工具，直接复用 ai-berkshire-monitor 仓库既有 skill，无需重写：

| 武器 | 来源 skill | 挖什么 |
|------|-----------|--------|
| 冷信息挖掘 | `small-cap-diligence` | 财务时间序列突变（应收/存货/在建/现金流环比）、股东户数、大宗交易/龙虎榜、公告分类（减持/质押/诉讼/解禁）、产业链交叉反推 |
| 诚信污点 | `management-deep-dive` | 实控人背景、历史资本运作、减持记录、股权质押 |
| 异动归因 | `news-pulse` | 股价异动时的负面事件归因 |
| 去劣信号 | `quality-screen` | 7 条指标快速排除非一流公司 |
| 一手披露 | 巨潮/港交所公告原文 | 问询函及回复、年报附注关联交易、客户集中度 |

**推广原则**：`small-cap-diligence` 的"冷信息挖掘"方法不只用于小盘股——大盘股同样适用（甚至更该用，因为大盘股的热门研报多二手转述，冷数据才是增量）。

---

## §3 辩论机制

### 轮次结构（默认 2 轮封顶）

```
Round 1: Bull 立论（最强多头论据）
         Bear 反驳（致命利空 + 对 Bull 论点的逐条反驳）
Round 2: Bull 回应（反驳 Bear 的利空，指出其夸张/概率低/已被定价）
         Bear 收口（对 Bull 回应的最终反驳 + 致命利空排序）
```

**论点收敛判断**（代替固定轮次）：若 Round 2 没有产生实质新论据（双方在重复自己），立即停辩，进入裁判层。控制成本：默认 2 轮，复杂标的可到 3 轮。

### 辩论纪律

1. 每轮必须引用对方上一轮的**具体论点**再反驳，禁止自说自话、禁止泛泛而谈
2. 每条论据必须落到数据/证据（"营收下滑 20%"而非"经营承压"）
3. 禁止人格攻击式反驳（"对方不懂"），只允许证据式反驳（"对方忽略了 XX 数据"）

---

## §4 裁判强制站队 Prompt（team-lead 必含）

```
基于 Bull 与 Bear 的完整辩论，你必须给出明确立场。

Avoid defaulting to Hold simply because both sides have valid points; commit
to a stance grounded in the debate's strongest arguments.

输出：
1. 方向：买入 / 观望 / 回避（或 BUY/OVERWEIGHT/HOLD/UNDERWEIGHT/SELL 五档）
2. 我倾向哪一方、原因（引用辩论中最强的 1-2 条论据）
3. 如果我是错的，什么信号会让我反转（证伪条件）
```

**为什么强制站队**：LLM 面对对立合理观点时，默认输出"两边都有道理，建议谨慎"——这是最安全也最没价值的回答。强制站队让结论有可操作性，后续的证伪/风控才能基于明确立场进行修正。模糊的 Hold 无法触发有效的风控讨论。

---

## §5 双层辩论：方向 + 尺度

v2 主流程聚焦**第一层（方向辩论）**。若用户要落实到仓位，追加**第二层（尺度辩论）**——三档风险偏好对抗：

| 角色 | 立场 | 输出 |
|------|------|------|
| aggressive-advisor | 激进 | 建议仓位（如"全仓参与"）|
| neutral-advisor | 中立 | 建议仓位（如"30% 仓位"）|
| conservative-advisor | 保守 | 建议仓位（如"回避"）|

对应真实投行的分工：研究部门定方向，风控部门定仓位——两个维度分离，各自独立论证。

---

## §6 与六道闸门的接口（ai-berkshire-monitor 专用）

Bear Researcher 的产出直接作为闸门"否决项"输入：
- **致命利空命中**（有数据支撑）→ 闸门否决，不进双轨交易
- **证伪清单** → 作为持有期的监控指标（触发即重审投资论文，衔接 `thesis-tracker`）

反过拟合红线：Bear 的利空必须落到可验证证据，否则不进闸门否决项——防止红队退化成"为否定而否定"的噪音源。
