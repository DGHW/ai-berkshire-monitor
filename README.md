# ai-berkshire-monitor

**Agent 驱动的 A 股价值投资买卖闭环（v7）** —— 从四大师深度研究到富途模拟盘自动交易的全自动决策系统。

- **研究**：`/investment-team` 技能驱动，四视角 Agent（段永平/巴菲特/芒格/李录）并行深度研究
- **决策**：六道闸门（确定性代码，非 LLM）+ 反锚定机制 + 中位数偏差凯利仓位
- **执行**：本地记账 + 富途 OpenD 模拟盘双轨下单，每日自动核对
- **风控**：8+4 分批建仓、报告落盘原子性、防过拟合现价、心跳日志+中断自愈

> 上游研究框架见 [xbtlin/ai-berkshire](https://github.com/xbtlin/ai-berkshire)（四大师深度投研技能）。本项目是**全自动执行层**：研究结论 → 机械闸门 → 双轨交易。

## 核心闭环

```
价格扫描(price_monitor) ── 现价入击球区 ──→ REVIEW_DUE（每日 15:05 定时任务）
                                                │
                              agent_driver review --limit 1（自动，1只/天）
                                                │
                          /investment-team 完整重研（4视角 Agent 并行，30-90min）
                                                │
                              落盘质量检查（齐全+原子+五字段可解析，不合格自动补写）
                                                │
                              反锚定漂移检测（建仓价漂移>15% 机械锁定回旧值）
                                                │
                    六道闸门（研究完整性→四视角一致性→击球区→现价→凯利→资金）
                                                │
                   8+4 分批：凯利 8% 初仓 → 双轨下单（本地记账 + 富途模拟盘）
                                                │
                    每日 15:05 成交核对 reconcile + 17:00 自愈 SelfHeal 兜底
```

**持仓后**：现价跌至二次补仓价 → ADD_DUE → lite 速评复核 → 错杀则补仓 4%（8+4=12 封顶）；证伪则不加仓并冷却 7 天。

## 关键设计（防 Agent 失败的核心）

| 机制 | 说明 | 文件 |
|------|------|------|
| **六道闸门** | 研究完整性/四视角一致性/击球区/现价上沿/凯利仓位/资金充足，全部确定性代码 | `tools/agent_driver.py` |
| **行业估值分层** | 申万二级估值难度知识库（宏观四特征→星级→额外维度→替代方法→PE陷阱→隐藏资产负债表）；研究前强制 lookup，★★★★+ 禁止 PE 直算，报告关键词合规闸门不通过自动补写 | `tools/industry_valuation.py` + `data/industry_valuation_map.json` |
| **反锚定** | 基本面无恶化但建仓价漂移>15% → 机械锁定回旧值，防止 LLM 用现价倒推结论 | `docs/anti-overfitting.md` |
| **8+4 分批** | 初仓 8% + 下跌复核补仓 4%（封顶 1 次，FAIL 冷却 7 天） | `tools/pool_kelly.py` |
| **双轨交易** | 本地记账为真源，富途模拟盘为镜像，每日 reconcile 核对，失败可回滚 | `tools/futu_bridge.py` |
| **落盘原子性** | 四视角文件须齐全+原子+五字段可解析，不合格自动补写 | `tools/report_sync.py` |
| **自愈** | 心跳日志定位中断环节 + 17:00 自愈任务补跑（含隔夜僵死清理） | `tools/batch1_selfheal.py` |
| **JSON 修复链** | LLM 输出修复（围栏/引号/截断/废话），替代弱正则 | `tools/llm_json_validator.py` |

## 快速开始

```bash
git clone https://github.com/DGHW/ai-berkshire-monitor.git
cd ai-berkshire-monitor
pip install requests pandas numpy futu-api

# 手动跑一次完整 batch1（等价于定时任务）
cmd /c scripts\batch1_daily.bat

# 注册定时任务（Windows）
schtasks /create /tn "StockMonitorDaily" /tr "cmd /c C:\...\scripts\batch1_daily.bat" /sc daily /st 15:05 /f
schtasks /create /tn "StockMonitorSelfHeal" /tr "cmd /c C:\...\scripts\batch1_selfheal.bat" /sc daily /st 17:00 /f
```

> 需要 CodeBuddy CLI（`codebuddy -p`）可用 + 富途 OpenD 已登录（仅模拟盘下单需要；研究/闸门/记账不依赖）。

## 目录结构

```
tools/                     # 核心工具（全部无 GUI，可 headless 运行）
├── agent_driver.py        # Agent 编排：入队/锁/重研/归一/六闸门/自动买入
├── price_monitor.py       # 价格状态机 WATCHING→REVIEW_DUE→BOUGHT→ADD_DUE
├── pool_kelly.py          # 中位数偏差凯利（8% 初仓 + 4% 补仓）
├── position_manager.py    # 本地持仓记账（真源）
├── futu_bridge.py         # 富途模拟盘双轨 + 成交核对 + 撤单回滚
├── report_sync.py         # 报告解析/五字段提取/聚合
├── pool_rotator.py        # 股票池健康检查与轮动
├── quote_fetcher.py       # 腾讯/新浪免费行情
├── financial_rigor.py     # 财务数据交叉验证
├── industry_valuation.py  # 行业估值难度分层（lookup/map/check-report/list）
├── llm_json_validator.py  # LLM 输出 JSON 修复链
└── batch1_selfheal.py     # 17:00 自愈检查
scripts/                   # 定时任务入口（ASCII+CRLF，勿改编码）
data/
├── monitor/               # pool/队列/锁/分组（运行状态）
└── positions/             # 本地持仓/资金（记账真源）
reports/
├── *.md                   # 四视角研究报告（{code}{名称}-{视角}.md）
└── monitor/               # 日报/Agent运行日志
docs/                      # 设计文档（防过拟合/回测任务书/路线图）
```

## 定时任务（Windows schtasks）

| 任务 | 时间 | 职责 |
|------|------|------|
| StockMonitorDaily | 每日 15:05 | batch1：扫描→巡检→核对→复核买入（心跳日志） |
| StockMonitorSelfHeal | 每日 17:00 | 自愈：REVIEW_DUE/ADD_DUE 遗留补跑 + 僵死清理 |
| StockRotationBiweekly | 每月 1/15 日 09:05 | batch2：混合深度研究 + 轮动 |

**账号切换注意**：定时任务与 CodeBuddy 账号无关（schtasks 是系统级）；`codebuddy -p` 的认证在 `~/.codebuddy`，重新 login 后自动跟随。切换账号后自检：`codebuddy -p "回复OK"`。

## 文档索引

- `docs/anti-overfitting.md` —— 防 Agent 过拟合现价的四层防线
- `docs/agent_backtest_brief.md` —— Agent 回测开发任务书（前视偏差清单）
- `docs/ROADMAP.md` —— 路线图
- `data/monitor/LOGIC.md` —— 买卖闭环逻辑细节（含全部判据）

## 声明

本项目仅供学习与研究，不构成投资建议。模拟盘交易不涉及真实资金。投资有风险，决策需谨慎。

## License

MIT License — 见 [LICENSE](LICENSE)。
