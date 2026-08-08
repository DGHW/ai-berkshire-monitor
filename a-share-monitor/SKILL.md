---
name: a-share-monitor
description: "A 股全市场初筛与监控工作流：从全A股（~5500只）程序化初筛出优质标的池，每日监控价格状态机（WATCHING→TRIGGERED→REVIEW_DUE），用半凯利公式计算仓位，股票池按周轮动。适用于：A 股价值投资标的筛选、持仓监控、击球区到价提醒、买入仓位计算。使用场景：\"筛选A股好公司\"、\"监控我的股票池\"、\"帮我算这只票该买多少仓位\"、\"检查股票池健康度\"。依赖本仓库 tools/ 下脚本与 data/monitor/ 数据文件。"
agent_created: true
---

# A 股全市场初筛与监控工作流

在 ai-berkshire 仓库内运行，将全 A 股（约 5500 只）通过两层漏斗初筛为优质标的池（约 800-1000 只），随后对监控池（20-30 只重点标的）做每日价格状态机监控、到价触发复审、半凯利仓位计算与按周轮动。

## 前置条件

- Python 3.10+，依赖：`requests`、`tushare`（可选，财务层用）
- Tushare token 配置于 `data/api_keys.json`（代理端点 `ts.gyzcloud.top` 或官方）
- 行情走腾讯/新浪免费接口，无需 token

## 核心工作流

### 1. 全 A 股初筛（tools/ashare_screener.py）

两层漏斗，把全市场缩小到可研究的池子：

```bash
python3 tools/ashare_screener.py --layer 0    # 快筛（秒级）：剔除 ST/市值<30亿/科创板PE>100
python3 tools/ashare_screener.py --layer 1    # 财务层（~50分钟）：ROE≥8%/毛利率≥15%/净利率≥5%/营收正增长
python3 tools/ashare_screener.py --stats      # 查看结果统计
```

- Layer 0：剔除 ST/*ST、市值 <30亿、科创板 PE>100、全市场 PE>300
- Layer 1：逐股拉财务指标（fina_indicator + income），金融行业（银行/保险/证券）豁免毛利率与负债率检查
- 结果写入 `data/screening/stage1_pool.json`，**每只股票保留淘汰理由（可追溯不黑箱）**
- 断点续跑：每 50 只自动落盘；中断后重跑自动跳过已完成

### 2. 监控池价格监控（tools/price_monitor.py）

对已确定击球区的重点标的做每日价格检查：

```bash
python3 tools/price_monitor.py                # 跑监控池并生成日报
python3 tools/price_monitor.py --ticker 600519  # 只查一只
python3 tools/price_monitor.py --dry-run      # 只拉价格不写文件
```

状态机：
```
WATCHING（观察）→ TRIGGERED（首日进入击球区）→ REVIEW_DUE（连续N日确认，需复审）
```
- 击球区：`data/monitor/pool.json` 中每只股票的 `buy_zone: {low, high}`
- 连续确认天数：`trigger_confirm_days`（默认 2，小盘股自动翻倍），防价格毛刺误触发
- 单日涨跌 >8% 自动标记"建议 news-pulse 归因"
- 日报写入 `reports/monitor/daily/YYYY-MM-DD-monitor.md`

### 3. 半凯利仓位计算（tools/kelly_sizer.py）

从深度报告的三情景估值（乐观/中性/悲观目标价+概率）计算建议仓位：

```bash
python3 tools/kelly_sizer.py \
  --scenarios '[{"r":0.60,"p":0.25},{"r":0.25,"p":0.50},{"r":-0.30,"p":0.25}]' \
  --score 4.3 --info-grade A --adv-usd 50000000
```

- 连续版凯利：`μ = Σ p·r`，`σ² = Σ p·(r−μ)²`，`f* = μ/σ²`，半凯利 = f*/2
- 约束层：单票上限 12%（小盘 8%）、评分<4.0 仓位减半、<3.5 不建仓、信息等级 B/C 打折、流动性 <$1000万 打折
- 输出：全凯利/半凯利/约束后建议仓位，计算过程可审计

### 4. 报告解析入池（tools/extract_targets.py）

从 investment-research 深度报告自动提取击球区、三情景与仓位，写入监控池：

```bash
python3 tools/extract_targets.py --report reports/腾讯/腾讯-research-20260620.md --ticker 0700.HK
```

解析：信息等级（A/B/C）→ 综合评分 → 三情景目标价+概率 → 分层建议表推导击球区 → 联动 kelly_sizer → 写入 pool.json

### 5. 股票池轮动（tools/pool_rotator.py）

五层池联动 + 三层淘汰，保持池子"活"：

```bash
python3 tools/pool_rotator.py --health-only    # 每日健康检查（论文过期/击球区失效/变ST）
python3 tools/pool_rotator.py --weekly         # 每周轮动（重跑Layer1 + 健康检查）
python3 tools/pool_rotator.py --report         # 生成轮动周报
python3 tools/pool_rotator.py --stats          # 各池数量统计
```

淘汰逻辑：
- 硬性：变 ST、净利连续 2 期为负、市值跌破 30亿 → 立即移出
- 恶化：ROE 下滑>5pct / 毛利率下滑>3pct / 营收转负 → 标记 WEAKENING，连续 2 周触发 thesis 复审
- 论文：>90 天未复审 → STALE；thesis-drift 判定"事实改变" → INVALIDATED 移出

## 数据文件约定

| 文件 | 内容 |
|------|------|
| `data/screening/stage1_pool.json` | 初筛池（5539只全量 + Layer0/1 结果 + 淘汰理由） |
| `data/monitor/pool.json` | 监控池（击球区/三情景/凯利仓位/状态机/next_earnings） |
| `data/monitor/ticker_map.json` | 全局股票映射（ticker ↔ 中文名 ↔ 市场/数据源） |
| `data/api_keys.json` | API 密钥（gitignore 排除，勿提交） |
| `reports/monitor/daily/` | 每日监控日报 |
| `reports/monitor/weekly-rotation-*.md` | 轮动周报 |

## 与其他 Skill 的关系

| Skill | 分工 |
|-------|------|
| investment-research / investment-team | 深度报告产出（本 skill 的"上游"，提供三情景/评分） |
| **a-share-monitor（本 skill）** | 初筛 → 监控 → 仓位 → 轮动的**执行层** |
| thesis-drift | 到价复审（REVIEW_DUE 后对比论文基线） |
| news-pulse | 单日异动 >8% 时的快速归因 |

## 常见问题

1. **Tushare token 失效**：检查 `data/api_keys.json` 的 `tushare_expires` 字段，周卡/月卡到期需续费；财务层（Layer1）依赖它，行情层（腾讯/新浪）不依赖
2. **初筛 Layer1 中断**：代理网络抖动正常，脚本已内置单只重试 4 次 + 断点续跑，重跑 `--layer 1` 自动跳过已完成
3. **金融股毛利率为 None**：正常现象，银行/保险/证券豁免毛利率与负债率检查（见代码 `FINANCE_KEYWORDS`）
4. **击球区怎么来**：跑 `/investment-research` 生成深度报告 → `extract_targets.py` 自动提取写入 pool.json；也可手动在 pool.json 填 `buy_zone`
