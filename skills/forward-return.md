# Forward Return：收益率期限结构引擎（核心定价中枢）

对 $ARGUMENTS 执行收益率期限结构测算。本 skill 回答的不是"这家公司最终值多少钱"，而是：**这个价值什么时候形成、什么时候被市场认识、6个月/1年/2年/3年/5年的赔率分别是多少、什么时候买、持有多久赔率最高。** 这是整个投研系统新的核心定价中枢。

## 定位与系统架构

```
S1/S2 基本面研究（investment-team-v2）
        │
        ▼
business-driver-model（未来业务/利润路径）
        │
   ┌────┴─────────────────────┐
   ▼                          ▼
expectation-arb          catalyst-ledger
市场已经预期什么           哪些事件何时发生
   │                          │
   └────────────┬─────────────┘
                ▼
         forward-return（本 skill）
         收益率期限结构引擎
                │
        6m / 1y / 2y / 3y / 5y
                │
                ▼
            S5 裁判（只裁决，不重新建模）
```

| 模块 | 职责 |
|------|------|
| expectation-arb | 市场已经预期了什么（Consensus vs Price-Implied） |
| catalyst-ledger | 哪些关键事件何时发生、概率多大（催化剂台账） |
| **forward-return（本 skill）** | 把"业务路径 + 市场预期 + 催化剂时点"合成为**各期限的收益率分布** |

**输入契约**：只接受 canonical financial snapshot（来自 `financial-data.md` 的 **T0/T1** 层；T2 仅 provisional 补洞、不得进入 Base Case 核心估值 driver）+ S1/S2 基本面证据。Web Agent（T3）不允许改这些数字。

**输出契约**：①收益率期限结构表（Markdown，人读）② `data/forecasts/{code}.json`（机器可读、可追踪、可回测）。

---

## 七步工作流

### F0：冻结当前时点数据（Canonical Snapshot）

输入只能来自 canonical snapshot，禁止 Web Agent 修改：

```
as_of / price / shares / cash / debt
TTM revenue / TTM earnings / TTM FCF / segment data
latest filing_date / snapshot_id
```

所有后续计算都锚定这个 snapshot_id，保证可回测（PIT 信息集）。

### F1：建立 Business Driver Tree（业务驱动树）

**禁止**让 Agent 直接拍"2028 EPS = 20"。必须拆解业务变量：

```
收入
├── 主业
│   ├── 业务量（GMV/销量/用户）
│   ├── 货币化率（take rate/单价）
│   └── 结构（广告/交易服务/分部）
├── 新业务 A
│   ├── 用户数/订单量
│   ├── 单位经济（ARPU × margin）
│   └── 履约/获客成本
└── 新业务 B
    └── ...

Driver → Revenue → Margin → EBIT → Net Income → FCF
```

Agent 的任务变成**预测业务变量**（有事实锚、可验证），而不是凭感觉预测股价。

### F2：Catalyst Object（催化剂对象化）

研究 Agent 提出的每个"潜在利好/拐点"（如"某新业务 2027H1 扭亏"），**不接受为结论**，必须转换成可计算的催化剂对象：

```yaml
catalyst_id: grocery_breakeven
claim: 多多买菜经营利润转正
date_distribution:
  earliest: 2026H2
  base: 2027H1
  latest: 2028H1
probability:
  base: 0.55
leading_indicators:
  - subsidy_intensity
  - order_density
  - fulfillment_cost_per_order
  - competitor_exits
  - merchant_economics
financial_impact:
  annual_profit_delta: { bear: 0, base: X, bull: Y }
market_expectation:
  priced_probability: 0.20   # 来自 expectation-arb 的 Price-Implied
recognition_lag:
  base_months: 6             # 事件兑现到市场认识的时滞
```

这样"预期差"第一次变成**可计算对象**（催化剂概率 × 财务影响 × 时点分布 × 市场已定价概率 × 认识时滞）。

### F3：防逻辑陷阱（护城河链条必须逐环验证）

"前期烧钱抢市场 → 后期垄断 → 有护城河 → 有定价权"可以成为**研究假说**，但不能直接成为模型假设。必须逐环证明：

```
规模 → 单位成本下降 → 竞争者难以复制 → 用户/商家难以迁移
     → 降低补贴或提升 monetization 后需求仍保持 → 真正的经济护城河
```

必须回答：竞争者真退出了吗？用户多归属还是单归属？商家是否易迁移？履约网络是否产生密度经济？提价 1% 会不会流失？监管是否限制价格权？补贴下降后订单留存率？

**中间任何一环断掉，"垄断→定价权"就不能进入 Base Case**（只能进 Bull Case 并标注为假说）。

### F4：输出收益率期限结构表（核心交付）

| Horizon | 基准目标价值 | 预期持有收益 | 年化 | P(亏损) | P(>15% CAGR) | 主要兑现因素 |
|---------|------------:|------------:|-----:|-------:|-------------:|-------------|
| 6个月 | 110 | +8% | +16.6%* | 31% | 47% | 市场开始修正预期 |
| 1年 | 125 | +22% | 22% | 24% | 61% | 新业务亏损收窄 |
| 2年 | 155 | +51% | 22.9% | 17% | 71% | 盈利兑现+重新定价 |
| 3年 | 180 | +76% | 20.7% | 14% | 68% | 主业增长+FCF |
| 5年 | 220 | +115% | 16.5% | 12% | 54% | 成熟期 |

**半年这种短期限，必须同时输出"持有期收益 + 年化等价收益"**——否则"3个月赚10% = 年化46%"会制造虚假精确度。

### F5：收益率拆解（Attribution）

长期股东回报拆成乘法关系（报告中用 attribution 近似拆分）：

```
R = R_earnings + R_rerating + R_dividend + R_buyback + R_FX

例：未来 3 年 CAGR 20.8%，其中：
  +12.4pct 盈利增长
  +4.1pct  利润率改善
  +2.8pct  估值重估
  +2.0pct  回购
  -0.5pct  汇率
```

**关键判断**：如果 80% 的收益来自 multiple expansion（估值重估），风险明显高于盈利驱动——这笔投资是"靠企业赚钱"还是"靠 PE 扩张赚钱"，必须在报告中显式回答。

### F6：JSON 落盘（机器可读、可追踪、可回测）

除 Markdown 报告外，必须同时落 `data/forecasts/{code}.json`：

```json
{
  "as_of": "2026-09-08",
  "snapshot_id": "...",
  "price": 0,
  "drivers": {},
  "catalysts": [],
  "scenarios": {},
  "horizons": { "0.5y": {}, "1y": {}, "2y": {}, "3y": {}, "5y": {} },
  "attribution": {}
}
```

这是"写研报"升级为"**动态投资模型**"的关键——后续 thesis-tracker 可直接改 `grocery_breakeven_probability: 0.55 → 0.70`，然后 `python tools/forward_return.py {code} --refresh` 重算整条收益率曲线。

---

## 与系统其他模块的衔接

| 模块 | 衔接方式 |
|------|---------|
| expectation-arb | 提供 `market_expectation.priced_probability`（价格已定价概率）给每个 catalyst |
| investment-team-v2 | 提供基本面证据与多空论据（Business Driver 的输入） |
| S5 裁判（super-research） | **只裁决，不重新建模**——裁判选择"我接受哪套假设，因此采用 S3C(forward-return) 的哪个 return distribution"，不再自己重算内在价值 |
| thesis-tracker | catalyst 状态变化 → 更新 probability/driver → 重算 forward-return → 决定持有/加仓/轮出 |
| Kelly / 组合层 | **暂缓对接**（用户明确暂缓实盘仓位优化）。后续用 forward-return 的 expected return / P(loss) / downside tail / confidence 替换 pool_kelly 的 `gain_med relative edge` 输入 |

---

## 工具接口（Python 实现为后续，本 skill 先定义契约）

| 工具 | 职责 | 状态 |
|------|------|------|
| `tools/forward_return.py` | 读 `data/forecasts/{code}.json`，重算收益率期限结构；支持 `--refresh`（catalyst/driver 更新后重算）、`--horizon`（单期限输出） | 待实现 |
| `tools/reverse_valuation.py` | Reverse DCF / Reverse Earnings Model——固定其他假设 solve 1-2 个关键变量（市场隐含 CAGR / 市场给某业务的利润贡献） | 待实现 |

**当前阶段**：本 skill 用 prompt 工作流 + `financial_rigor.py`（verify-valuation/three-scenario/cross-validate）完成计算与验算；上述两个专用工具按本契约后续实现。

---

## 执行纪律

1. **canonical 优先**：F0 数据只来自 `financial-data.md` 的 T0/T1/T2 snapshot，Web（T3）不得改数字
2. **Driver 拆解强制**：禁止直接拍 EPS/目标价，必须先拆 Business Driver Tree
3. **催化剂对象化**：每个"利好/拐点"必须是 Catalyst Object（概率+时点+影响+市场已定价+时滞），不接受口头结论
4. **逻辑陷阱红线**：护城河链条逐环验证，断环则该假设只能进 Bull Case 并标"假说"
5. **期限结构必给全**：6m/1y/2y/3y/5y 五档，悲观情景必填；半年期给持有期收益+年化等价双口径
6. **机器可读必落盘**：Markdown + `data/forecasts/{code}.json` 双产出，保证可回测（PIT）
7. **S5 不重复建模**：内在价值由 forward-return 产出，裁判只裁决采用哪个 distribution，不 ± 市场隐含假设修正（防 double count）
8. 仅供学习研究，不构成投资建议
