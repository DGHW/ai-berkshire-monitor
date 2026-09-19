# Skills Index — Agent 索引与入门（必读）

> **新 Agent 第一次进入本仓库：先读根目录 `AGENTS.md`（自动加载：绝对规则/常用操作/故障处置），再读本文件（skill 家族全景与路由），然后按任务场景深入具体 skill。**

---

## 一、这个项目是什么（30 秒版）

A 股价值投资买卖闭环：**全市场初筛（5539→890）→ 价格状态机监控 → 四大师+红队深度研究 → 六道确定性闸门 → 仓位计算 → 双轨交易（本地记账+富途模拟盘）→ 持有期论文追踪**。

三层防错设计：Agent 产生研究假说（可犯错）→ 确定性闸门验证（不可绕过）→ 反锚定四层防线（禁止"因为跌所以便宜"）。

---

## 二、数据纪律（最重要，任何 skill 执行前先懂这个）

**四层数据体系**（详见 `skills/financial-data.md`）：

| 层 | 能写 canonical 吗 | 例 |
|----|:---:|----|
| T0 原始披露（SEC/HKEX/巨潮/MOPS） | ✅ 最高权威 | 财报原文 |
| T1 结构化数据商（Tushare/FinMind） | ✅ 带 provenance | tools/financial_data.py |
| T2 金融聚合网站（macrotrends/东财） | ❌ 仅 provisional 补洞 | 双源交叉验证用 |
| T3 Web Research（研报/新闻/雪球） | ❌ 只产生研究证据 | 挖 hypothesis/共识/情报 |

三条铁律：①**LLM+Web 产生 hypothesis，确定性数据层验证 hypothesis**；②T2 provisional 字段禁止进入 Base Case 核心估值 driver；③Agent 不问"网页A/B各说多少"，而是"给我 canonical financial snapshot"。

---

## 三、Skill 家族全景（22 个，按角色分四类）

### 编排层（入口）
| Skill | 干什么 | 触发 |
|-------|--------|------|
| **super-research** | 全场景一站式：S0体检→四大师+红队→条件路由专项→定价审计→checklist→终审 | "一次性研究透+输出单一内在涨幅" |

### 核心研究层
| Skill | 干什么 | 触发 |
|-------|--------|------|
| **investment-team-v2** | 四大师+Bull/Bear红队辩论+裁判强制站队；双模式 standalone/evidence-only | 深度研究（evidence-only 供 super-research S1 调用） |
| investment-team | 旧版快速四视角 | 快速研究 |
| **expectation-arb** | 预期差审计：市场已定价什么（Consensus vs Price-Implied），**只做审计不做估值**，输出 Forward Return Input Pack | "这个利好股价反映了吗/谁被错杀" |
| **forward-return** | 收益率期限结构引擎：Business Driver Tree + Catalyst Object + 6m/1y/2y/3y/5y 回报曲线 + attribution | "持有多久赔率最高"（配套 `tools/forward_return.py`） |
| financial-data | 四层数据规范 + canonical fact schema | 所有研究的底座，任何数字先过这层 |

### 专项深挖层（条件触发，非全跑）
| Skill | 触发条件 |
|-------|---------|
| quality-screen | 任何研究第一步：7条去劣指标，不过直接出局 |
| bottleneck-hunter | 产业链型公司（AI算力/能源/半导体）：供应链咽喉地位 |
| management-deep-dive | 红队发现治理疑点：减持/质押/问询函/资本运作 |
| small-cap-diligence | C级信息稀缺标的：冷信息挖掘 |
| earnings-team | 财报发布≤30天：四大师精读 |
| news-pulse | 股价异动≥±5%：快速归因 |
| investment-checklist | 终审前：巴菲特六关检查 |

### 持有期层（买入后）
| Skill | 干什么 | 触发 |
|-------|--------|------|
| thesis-tracker | 投资论文追踪（建立/季度检查），衔接 forward-return 重算 | 买入时建立，每季度 |
| thesis-drift | 论文漂移检测：事实变了还是措辞变了 | 重大消息后 |
| portfolio-review | 组合管理：钱该留在它这里吗 | 定期 |

---

## 四、场景决策树（用户说什么 → 用什么）

```
"帮我研究透XX，最后给一个涨幅数字"     → /super-research XX
"XX值得买吗？挖深一点"                → /investment-team-v2 XX
"这个利好股价反映了吗/谁被错杀"       → /expectation-arb XX
"持有多久赔率最高"                    → /forward-return XX
"快速看看XX"（20分钟级）              → /investment-team XX
"XX过得了去劣筛子吗"                  → quality-screen XX
"XX在产业链里算咽喉吗"                → bottleneck-hunter XX
"XX刚发财报"                          → /earnings-team XX 季度
"XX今天暴跌5%"                        → /news-pulse XX
"XX买入了，建立论文"                  → /thesis-tracker XX 建立论文
```

---

## 五、职责边界（防重复计算）

| 阶段 | 只负责 |
|------|--------|
| S0 super-research | 数据/质量体检 |
| S1 investment-team-v2 (evidence-only) | 公司究竟是什么（Evidence Pack，无价格判断） |
| S2 super-research | 哪些关键问题值得专项深挖 |
| S3A expectation-arb | 市场现在隐含了什么 |
| S3B forward-return | 我们认为未来会发生什么 |
| S3C forward-return | 各期限回报是多少 |
| S4 investment-checklist | 是否存在不能买的硬伤 |
| S5 super-research 裁判 | 裁决（不重新建模） |

**禁止**："内在价值 = f(正常化利润,合理倍数) ± 市场隐含假设修正"（double count）；Price-Implied 反推结果必须标 identifiability（low=区间，默认）。

---

## 六、三条铁律

1. **LLM+Web 产生 hypothesis，确定性数据层验证 hypothesis**——Web 不得覆盖 canonical 事实
2. **每条利空必须落到可验证证据**（财报科目异常/问询函/减持质押/裁判文书），模糊担忧不算数
3. **裁判必须站队**——"两边都有道理建议观望"是违规输出

## 七、典型一次深度研究的产出清单

```
reports/{code}{名称}-S0体检.md
reports/{code}{名称}-商业模式分析.md / -财务估值.md / -行业竞争.md / -风险评估.md
reports/{code}{名称}-多头立论.md / -空头猎杀.md / -evidence-pack.json
reports/{code}{名称}-super终审.md
reports/{code}{名称}-thesis.md
data/forecasts/{code}.json
```

---

*本索引随 skill 家族演进更新；新增 skill 必须同步登记到第三节表格与第四节决策树。仅供学习研究，不构成投资建议。*
