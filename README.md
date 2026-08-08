# ai-berkshire-monitor

**A 股全市场初筛与监控工作流** — 从全 A 股（约 5500 只）程序化初筛出优质标的池，每日监控价格状态机，用半凯利公式计算买入仓位，股票池按周自动轮动。

> 上游研究框架见 [xbtlin/ai-berkshire](https://github.com/xbtlin/ai-berkshire)（四大师深度投研）。本项目是其**执行层扩展**：把深度报告的结论变成可执行的筛选、监控、仓位与轮动系统。

## 项目解决什么问题

深度研究框架解决了"怎么研究一家公司"，但留下三个执行层问题：

1. **全市场选股靠手工**：5539 只 A 股里哪只值得深研？—— 答：两层漏斗程序化初筛（~50 分钟跑完全市场）
2. **研究完就忘**：深度报告给出目标价/安全边际，但没人盯着价格是否到达 —— 答：监控池每日状态机检查
3. **买多少没依据**：报告说"值得买"，但仓位拍脑袋 —— 答：三情景概率 → 连续版半凯利公式 → 约束层

## 主要功能

| 功能 | 工具 | 说明 |
|------|------|------|
| **全 A 股初筛** | `tools/ashare_screener.py` | 两层漏斗：快筛（ST/市值/PE）+ 财务层（ROE/毛利率/净利率），5539→~890 |
| **价格监控** | `tools/price_monitor.py` | 状态机 WATCHING→TRIGGERED→REVIEW_DUE，连续 N 日确认防毛刺 |
| **半凯利仓位** | `tools/kelly_sizer.py` | 三情景概率加权 → μ/σ² → 半凯利 → 约束层（上限/评分/流动性折扣） |
| **报告解析入池** | `tools/extract_targets.py` | 从 investment-research 报告自动提取击球区/三情景/仓位写入监控池 |
| **股票池轮动** | `tools/pool_rotator.py` | 五层池联动，三层淘汰（硬性/财务恶化/论文过期） |
| **行情接口** | `tools/quote_fetcher.py` | 腾讯(主)+新浪(备) 免费接口，覆盖 A股/港股/美股/ETF |
| **财务数据** | `tools/financial_data.py` | Tushare 财报日历/三表/指标，代理端点可配置 |

## 安装方式

```bash
# 1. 克隆仓库
git clone https://github.com/xbtlin/ai-berkshire-monitor.git
cd ai-berkshire-monitor

# 2. Python 环境（3.10+）
pip install requests tushare

# 3. 配置 Tushare token（财务层用；行情层无需）
#    编辑 data/api_keys.json：
#    {"tushare_token": "你的token", "tushare_api_url": "https://ts.gyzcloud.top/api"}
```

> Tushare 免费注册即可，财务三表接口需积分（约 200 元一次性买断，或第三方代理周卡）。
> 行情接口（腾讯/新浪）完全免费，不配置 token 也能跑 Layer0 与监控。

## 使用方法

```bash
# ① 全市场初筛
python3 tools/ashare_screener.py --layer 0     # 快筛（秒级）：5539 → ~3944
python3 tools/ashare_screener.py --layer 1     # 财务层（~50分钟）：→ ~890
python3 tools/ashare_screener.py --stats       # 查看结果

# ② 深度报告入池（先用 investment-research 出报告）
python3 tools/extract_targets.py --report reports/腾讯/腾讯-research-20260620.md --ticker 0700.HK

# ③ 每日监控（可配定时任务）
python3 tools/price_monitor.py                 # 生成 reports/monitor/daily/{date}-monitor.md

# ④ 计算仓位
python3 tools/kelly_sizer.py --scenarios '[{"r":0.60,"p":0.25},{"r":0.25,"p":0.50},{"r":-0.30,"p":0.25}]' --score 4.3 --info-grade A

# ⑤ 股票池轮动
python3 tools/pool_rotator.py --health-only    # 每日健康检查
python3 tools/pool_rotator.py --weekly         # 每周轮动
python3 tools/pool_rotator.py --report         # 轮动周报
```

## 输入输出示例

**输入：** 全 A 股（自动）→ **输出：** 初筛池统计

```
$ python3 tools/ashare_screener.py --stats
总股票数:        5539
Layer0 通过:     3944（快筛：非ST/非亏损/非高PE/市值够）
Layer1 通过:     890（财务：ROE/毛利/净利/负债/营收）

TOP 20（按市值）：
  贵州茅台  PE 19.8  ROE 42.3%  毛利率 89.8%  市值 16366亿
  宁德时代  PE 21.1  ROE 24.2%  毛利率 23.9%  市值 17955亿
  中际旭创  PE 72.0  ROE 70.2%  毛利率 46.1%  市值 10760亿
  ...
```

**输入：** 三情景估值（乐观 +60%/25%、中性 +25%/50%、悲观 -30%/25%）→ **输出：** 半凯利仓位

```
$ python3 tools/kelly_sizer.py --scenarios '[{"r":0.60,"p":0.25},{"r":0.25,"p":0.50},{"r":-0.30,"p":0.25}]' --score 4.3 --info-grade A
期望收益 μ = +20.00%   方差 σ² = 0.1037
全凯利 f* = 1.93   半凯利 f = 0.96
约束层: 评分4.3 OK × 信息A ×1.0 × 流动性×1.0 → 合成上限 12.0%
>>> 建议仓位: 12.0%（顶格仓位，受约束层限制）
```

**输入：** `python3 tools/price_monitor.py` → **输出：** 每日监控日报

```markdown
# 监控池日报 — 2026-08-08
## 触发警报（1）
| 股票 | 名称 | 现价 | 击球区 | 状态 |
|------|------|------|--------|------|
| 000858 | 五粮液 | 75.11 | 74.0-76.0 | REVIEW_DUE |
## 接近触发（距击球区 <10%，1）
| 601318 | 中国平安 | 53.38 | 距区间上方 2.7% |
```

## 目录结构

```
tools/
├── ashare_screener.py   # 全A初筛（两层漏斗）
├── price_monitor.py     # 监控池价格状态机
├── kelly_sizer.py       # 半凯利仓位计算
├── extract_targets.py   # 报告→击球区解析
├── pool_rotator.py      # 股票池轮动
├── quote_fetcher.py     # 行情接口（腾讯/新浪）
├── financial_data.py    # Tushare 财务数据
└── news_fetcher.py      # 新闻聚合（GDELT/巨潮/雪球）
data/
├── screening/           # 初筛池 stage1_pool.json
└── monitor/             # 监控池 pool.json / ticker_map.json / api_keys.json
reports/
└── monitor/             # 日报 daily/ 与轮动周报
a-share-monitor/         # 可分发 Skill（SKILL.md + agents.yaml）
```

## 声明

本项目仅供学习与研究，不构成投资建议。所有工具输出"分析与建议"，不执行真实交易。投资有风险，决策需谨慎。

## License

MIT License — 见 [LICENSE](LICENSE)。
