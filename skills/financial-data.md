# 财务数据获取与交叉验证规范（四层数据体系）

本规范适用于所有涉及企业财务数据的研究。**核心原则：事实数据层与 Web Research 层彻底分离——财务事实必须来自结构化数据层（T0/T1/T2），Web 研究（T3）只产生研究证据，不产生 canonical 财务事实。**

---

## 一、四层数据体系（核心框架）

| 层级 | 角色 | 举例 | Agent 权限 |
|------|------|------|-----------|
| **T0 原始披露** | Canonical facts 最高权威 | SEC EDGAR（10-K/10-Q）、HKEX 披露易、巨潮资讯 cninfo、上交所/深交所、MOPS（台股公开资讯观测站） | 最高权威，一切冲突以此为准 |
| **T1 结构化数据商** | 机器可读的 canonical 数据 | Tushare Pro、FinMind | 可写 canonical，**必须记录 provenance**（来源/拉取时间） |
| **T2 金融聚合网站** | 验算、补洞、快速交叉验证 | macrotrends、stockanalysis、aastocks、eastmoney、goodinfo | 可交叉验证，**默认不能写 canonical**——确需补洞时标 provisional（见下） |
| **T3 Web Research** | 研报、新闻、访谈、社区叙事、竞争情报 | 券商观点、雪球、媒体、专家文章、管理层访谈 | **只产生研究证据/共识预期，不产生 canonical 财务事实** |

**为什么分四层**：两个金融网站（如 macrotrends + stockanalysis）并不等于两个真正独立的原始事实源——它们可能都来自同一个 SEC filing，只是口径不同（GAAP vs Non-GAAP、单季 vs YTD、归母 vs consolidated、最新股本 vs 历史股本、汇率日期、ADR 折算比例）。`245 vs 278 → 差异 13.5%` 往往不是"数据冲突"，而是**两个完全不同的 metric**。

**一句话原则**：`LLM + Web（T3）用来产生 hypothesis；确定性数据层（T0/T1/T2）用来验证 hypothesis。`

---

## 二、Canonical Fact Schema（每个数字不再是 `revenue = 100`）

任何进入研究报告的财务数字，必须是结构化对象而非裸数值：

```json
{
  "metric": "revenue",
  "value": 100000000000,
  "currency": "CNY",
  "unit": "yuan",
  "period_start": "2026-01-01",
  "period_end": "2026-06-30",
  "period_type": "H1",
  "basis": "GAAP",
  "scope": "consolidated",
  "attribution": null,
  "source_tier": "T0",
  "source": "exchange_filing",
  "filing_date": "2026-08-29",
  "fetched_at": "2026-09-08T02:00:00Z",
  "confidence": 1.0
}
```

**字段说明**：
- `period_type`：Q1/H1/Q3/FY/TTM——单季与累计禁止混用
- `basis`：GAAP / Non-GAAP / IFRS——利润类必须标注
- `scope`：consolidated / parent（归母 vs 合并）
- `attribution`：归母净利润 / 含少数股东权益（合并口径）
- `source_tier`：T0/T1/T2——禁止 T3 进入此字段
- `confidence`：1.0（T0原文）/ 0.9（T1结构化）/ 0.7（T2聚合）/ 0.5（反推估算，须标 `[估计]`）

**Agent 的工作方式因此改变**：不再问"网页 A 说多少？网页 B 说多少？"，而是问"给我 {公司} 截至 {as_of} 可用的 canonical financial snapshot"——snapshot 由 T0/T1 工具层生成，Agent 只消费不篡改。

---

## 三、各层详解与数据源

### T0 原始披露（最高权威）

| 市场 | 原始来源 | 获取方式 |
|------|---------|---------|
| 美股 | SEC EDGAR | sec.gov/cgi-bin/browse-edgar（10-K / 10-Q 原文） |
| 港股 | HKEX 披露易 | hkexnews.hk（年报/中报 PDF） |
| A股 | 巨潮资讯 cninfo / 上交所 / 深交所 | cninfo.com.cn（原始年报/季报 PDF） |
| 台股 | 公开资讯观测站 MOPS | mops.twse.com.tw（财报原文/月营收公告） |

**规则**：两个 T2 来源均与 T0 不符时，以 T0 为准，并标记 T2 来源错误。

### A股数据执行层：a-stock-data skill（本地已安装，A股首选工具包）

本地已安装 `a-stock-data` skill（V3.2.3，27 端点实测可用，自包含零依赖、东财接口已内置限流防封）。**A 股研究一律优先经此工具包取数**，它把"四层体系"落到可执行代码。与四层的映射：

| a-stock-data 七层 | 归属四层体系 | 说明 |
|-------------------|-------------|------|
| 公告层：巨潮 cninfo 公告全文检索+下载 | **T0** | 官方披露原文 |
| 基础数据：mootdx finance 季报快照(37字段)/F10、新浪财报三表 | **T1/T2** | 结构化财务数据 |
| 行情层：mootdx K线/五档/逐笔（TCP，不封IP）、腾讯财经 PE/PB/市值/涨跌停 | **T2 行情** | 不封IP可高频，Canonical Anchor Pack 的 price 来源 |
| 信号层：龙虎榜席位/全市场龙虎榜/限售解禁日历/北向资金/概念板块 | **T0 级事件事实**（东财 datacenter 聚合接口） | 交易所披露数据经聚合 |
| 资金面/筹码：融资融券/大宗交易/股东户数/分红送转/资金流120日 | **T0 级事件事实**（同上） | small-cap-diligence 冷信息的直接数据源 |
| 研报层：东财研报/同花顺一致预期/iwencai | **T3 研究证据** | Consensus 预期 → expectation-arb 输入 |
| 新闻层：东财个股新闻/全球资讯 | **T3 研究证据** | news-pulse 输入 |

**使用规则**：①行情/K线/实时价/市值/财务三表一律走 mootdx/腾讯（不封IP，可高频），**东财仅用于它独有的数据**（龙虎榜/解禁/融资融券/大宗/股东户数/分红/资金流/研报/新闻）且已内置限流；②龙虎榜/解禁/股东户数是 small-cap-diligence 与 news-pulse 的直接数据源；③同花顺一致预期是 expectation-arb Consensus 层的输入；④经东财聚合的龙虎榜/解禁等数据本质是交易所披露（T0 级事件事实），但接口可靠性按 T2 对待、记录 provenance。

### T2 Provisional Fallback（补洞规则 + 数据防火墙）

T2 默认不能写 canonical。若 T0/T1 暂不可得而必须补洞：

```json
{ "source_tier": "T2", "status": "provisional", "confidence": 0.7, "requires_verification": true }
```

**防火墙规则**：`provisional=true` 的字段**禁止进入 Base Case 的核心 valuation driver**——只能进 sensitivity 分析、Bull/Bear 论据、或等 T0/T1 核实后转正。这是数据防火墙的最后一道闸。

| 层 | 能否直接写 canonical |
|----|:---:|
| T0 | ✅ |
| T1 | ✅（带 provenance） |
| T2 | ❌ 默认不能（仅 provisional 补洞） |
| T3 | ❌ |

### T1 结构化数据商（机器可读 canonical）

- **Tushare Pro**：A股财务数据（`tools/financial_data.py` 直接拉 disclosure date / income statement / balance sheet / financial indicators，含 `ann_date/end_date/pre_date/actual_date`）。**注意：Tushare 是 T1 结构化数据服务，非 T0 原始披露**——不要把"Tushare 一手拉取"等同于"交易所原文"。
- **FinMind**：台股（见下"台股 FinMind 取数工具"）。
- token 只存本机、严禁提交 git（`local/` 已 .gitignore 排除）。

### T2 金融聚合网站（验算/补洞）

| 市场 | 主源 | 副源 | 原始一手 |
|------|------|------|---------|
| 美股（PDD/腾讯ADR/网易ADR） | macrotrends.net/stocks/charts/{ticker} | stockanalysis.com/stocks/{ticker}/financials | SEC EDGAR |
| 港股（腾讯0700/网易9999/美团3690） | aastocks.com | macrotrends（ADR：腾讯TCEHY/网易NTES） | HKEX披露易 |
| A股 | **a-stock-data skill（本地，首选）** | eastmoney.com → 财务报表 | cninfo.com.cn | 巨潮 PDF |
| 台股 | FinMind API（`tools/twstock_data.py`） | goodinfo.tw | MOPS |

**台股 FinMind 取数工具**（分析台股时优先调用，输出自带市值验算）：

```bash
python3 tools/twstock_data.py quote 2330        # 最新行情 + PER/PBR/殖利率 + 市值验算
python3 tools/twstock_data.py valuation 2330    # 估值指标 + PER一年区间 + 52周高低
python3 tools/twstock_data.py financials 2330   # 近5年年度核心财务
python3 tools/twstock_data.py revenue 2330      # 近13个月月营收及同比（台股独有月度信号）
python3 tools/twstock_data.py dividend 2330     # 近年股利政策
python3 tools/twstock_data.py search 台積        # 搜索代码（繁体）
```

台股注意：①货币单位 TWD，跨市场先统一换算；②月营收是台股独有优势（每月10日前强制披露），earnings-review/thesis-tracker 应优先用 `revenue` 子命令；③FinMind 损益表为单季值，工具已自动加总年度，不足4季标"仅前N季累计"；④token 按优先级读取：环境变量 `FINMIND_TOKEN` → `local/finmind_token.txt`；⑤台积电等 ADR 注意折算（1 TSM ADR = 5 股 2330）。

### T3 Web Research（只产生研究证据）

| Web Search 应负责 | 不应负责 |
|------------------|---------|
| 券商怎么理解公司 | 最新营收是多少 |
| 市场共识是什么 | 当前总股本是多少 |
| 管理层访谈 | GAAP 净利润 |
| 竞争对手动态 | FCF 的机械计算 |
| 渠道调研 | 市值计算 |
| 用户/商户反馈 | EPS 口径转换 |
| 新业务潜在拐点 | 财报期数判断 |
| 哪个事件市场还没关注 | ADR 折算 |

**T3 产物必须标注为"研究证据"**，与 canonical 财务事实分开存放，禁止互相覆盖。

---

## 四、执行规范（交叉验证归位到 T2 层）

### 第一步：获取 canonical 数据

优先从 T0/T1 获取 canonical snapshot。对关键财务指标，T1 工具拉取后记录 provenance。

### 第二步：T2 交叉验证

对每个关键指标，从 T2 的**来源1**和**来源2**取数验证（这是"双源交叉验证"的正确位置——验证 canonical，不是替代 canonical）：

```
误差率 = |来源1数值 - 来源2数值| / 来源1数值 × 100%
```

| 误差 | 处理方式 |
|------|---------|
| ≤ 1% | ✅ 一致，取 T0/T1 canonical 数值，标注两个 T2 来源 |
| 1% ~ 5% | ⚠️ 标记"数据存在差异"，注明两个数值，说明可能原因（汇率/会计口径） |
| > 5% | ❌ 标记"数据存在重大差异"，**必须查 T0 原始财报核实**，不得直接使用 |

### 第三步：数据呈现格式

```
收入：1,239亿元 ✅
  - T0/T1 canonical: 1,241亿元 (H1, GAAP, consolidated, filing 2026-08-29)
  - T2 macrotrends: 1,241亿元
  - T2 stockanalysis: 1,237亿元
  - 误差: 0.3%
```

差异示例：
```
净利润：245亿元 ⚠️ 数据存在差异
  - T2 macrotrends: 245亿元（GAAP）
  - T2 stockanalysis: 278亿元（Non-GAAP）
  - 误差: 13.5% — 原因：basis 不同（GAAP vs Non-GAAP），按 canonical schema 应分别记录为两个 metric，而非"冲突"
```

---

## 五、常见差异原因（不一定是数据错误）

| 原因 | 说明 | canonical schema 对应字段 |
|------|------|--------------------------|
| GAAP vs Non-GAAP | 最常见，尤其利润类 | `basis` |
| 单季 vs 累计（YTD） | Q2单季 vs H1累计 | `period_type` |
| 归母 vs 合并 | 是否含少数股东权益 | `scope` / `attribution` |
| 汇率换算 | 港币/人民币/美元/新台币换算时间点 | `currency` + 换算日期 |
| 财年定义 | 自然年 vs 财年（苹果财年10月结束） | `period_start/end` |
| 最新股本 vs 历史股本 | 拆股/增发后的 EPS 重算 | 复权规则（见下） |
| ADR 折算 | ADR 与原股比例不同 | 单独记录折算比例 |
| 数据更新滞后 | 某平台未更新最新一期 | `filing_date` vs `fetched_at` |

---

## 六、特别规则

1. **未上市公司**（米哈游、莉莉丝等）：只有一手数据来源时，数据前标记 `[估计]`（confidence=0.5），不执行交叉验证
2. **季度 vs 年度**：优先用年度数据交叉验证；季度数据部分来源可能滞后（核 `filing_date`）
3. **原始财报优先**：T2 来源均与 T0 不符时，以 T0 为准，标记 T2 来源错误

---

## 七、股价与复权（历史序列必读）

| 口径 | 含义 | 用途 |
|------|------|------|
| 不复权 | 实际成交价，除权除息日跳空 | 仅用于"当前时点"快照 |
| 前复权 | 以最新价为基准回调历史价 | 历史股价对比、N年涨幅、历史PE band 一律用它 |
| 后复权 | 以上市首日为基准前推 | 计算历史总回报/年化收益 |

规则：①历史价格分析统一用前复权，同一分析内不得混用复权与不复权来源；②当前市值/PE 用当前实际股价×当前总股本，与复权无关；③跨拆股/大比例送转的每股指标必须复权还原后再同比；④总回报/年化需计分红（后复权已含）；⑤增发/回购后市值验算以最新总股本为准（`financial_rigor.py verify-market-cap` 偏差>5% 提示核对）。

---

## 八、快速索引

| 场景 | T1/T2 主要来源 | 备用 | T0 原始源 |
|------|---------------|------|----------|
| PDD / 拼多多 | macrotrends.net/stocks/charts/PDD | stockanalysis.com/stocks/pdd | SEC EDGAR |
| 腾讯 | macrotrends.net/stocks/charts/TCEHY | aastocks（0700.HK） | HKEX |
| 网易 | macrotrends.net/stocks/charts/NTES | aastocks（9999.HK） | HKEX |
| 三七互娱 | eastmoney.com（002555） | — | cninfo.com.cn |
| 吉比特 | eastmoney.com（603444） | — | cninfo.com.cn |
| Nintendo | macrotrends（NTDOY） | stockanalysis（NTDOY） | 日本 TDnet |
| Capcom | macrotrends（CCOEY） | stockanalysis（CCOEY） | 日本 TDnet |
| 台积电 | tools/twstock_data.py（2330） | goodinfo.tw / macrotrends（TSM，1 ADR=5股） | MOPS |
| 联发科 | tools/twstock_data.py（2454） | goodinfo.tw | MOPS |
