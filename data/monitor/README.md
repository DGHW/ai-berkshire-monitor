# 股票池维护体系 — 买卖闭环手册（v4 · 2026-08-12）

> 基于 249 只深度研究报告（四视角：段永平/巴菲特/芒格/李录）。
> 核心原则：**基本面没改观+价格跌 = 买入；基本面转换 = 卖出；不设止盈让利润奔跑。**

## 一、分组规则

| 组 | 判定标准 | 数量 | 维护方式 |
|---|---|---|---|
| **买入组 BUY** | 现价 ≤ 建仓价（四视角中位数） | 3 | 每日巡检，到价可分批买入 |
| **观察组 WATCH** | 建仓价 < 现价 ≤ 建仓价×1.15 | 92 | 每日巡检，等待回调 |
| **后备组 RESERVE** | 有研究价值但现价 > 建仓价×1.15 | 91 | 不主动巡检，轮动补位用 |
| **放弃组 DROP** | 四视角击球区全部🔴（不值得碰） | 59 | 不维护，仅作轮动后备池 |

## 二、买卖闭环链路（v4）

```
┌────────────── 每日定时任务 batch1 ──────────────┐
│ A. 监控池扫描（观察+买入组）                       │
│    WATCH → TRIGGERED(触建仓价首日)               │
│    → REVIEW_DUE(连续2日确认，日报置顶)            │
│    → position_manager --review 开4视角深度复核     │
│    → 基本面没改观+价在击球区 → 买入组              │
│    → 基本面恶化 → 放弃组                          │
│ B. 持仓扫描（持有中）                             │
│    ① 止损 -20%（唯一自动卖出）                    │
│    ② 估值提醒 +40%（不自动卖，仅建议复核）         │
│    ③ 每日轻量新闻扫描（巨潮公告=高置信→报告）      │
│    ④ 基本面转换（论文过期/财报恶化/治理爆雷）→卖出  │
└────────────────────┬────────────────────────────┘
                     ▼
┌────────────── 买入执行 ────────────────────────┐
│  position_manager --add                        │
│  仓位 = 中位数偏差凯利（pool_kelly.py）          │
│    G_med=池中位涨幅(冻结基准)                   │
│    δᵢ=Gᵢ−G_med，f=δ/σ²池，半凯利，clamp[0,12%] │
└────────────────────┬────────────────────────────┘
                     ▼
┌────────────── 半月/月度定时任务 batch2 ──────────┐
│  pool_rotator --monthly-rotate                 │
│    观察组轮出→后备组: 涨超建仓+40%/论文过期/涨幅转负 │
│    放弃组轮入→观察组: 深度回调至建仓价下(需重新研究) │
│    后备组轮入→观察组: 价格回落至+15%内             │
│  pool_kelly --refresh-basis (基准月度冻结刷新)    │
│  position_manager --rotate (自动补位)            │
└─────────────────────────────────────────────────┘
```

## 三、仓位逻辑（中位数偏差凯利 · 用户确认版）

```
候选池 = 买入组 + 观察组（所有留下研究的股票）
G_med = 池内内在涨幅中位数          （当前冻结基准 7.5%）
δᵢ   = Gᵢ − G_med                   （个股相对池中位数的超额内在涨幅）
fᵢ*  = δᵢ / σ²_pool                 （连续凯利，池方差做分母）
fᵢ   = 半凯利 = fᵢ*/2，clamp 到 [0, 单票上限 12%/小盘8%]

含义：跑赢池中位数的股票拿更大仓位，跑输的自动缩小甚至为零。
基准冻结：月度轮动时重算写入 kelly_basis.json，月内买入统一用冻结基准。
```

## 四、定时任务（v6：已注册 + Agent 自动驱动）

```bash
# 注册每日任务（15:05 跑 batch1：价格扫描+持仓巡检+自动复核买入）
schtasks /create /tn "StockMonitorDaily" /tr "cmd /c C:\Users\17356\WorkBuddy\2026-08-07-20-15-31\ai-berkshire\scripts\batch1_daily.bat" /sc daily /st 15:05 /ru "%USERNAME%" /rl LIMITED /f

# 注册轮动任务（每月1号/15号 09:05 跑 batch2：混合深度研究+规则轮动+基准刷新）
schtasks /create /tn "StockRotationBiweekly" /tr "cmd /c C:\Users\17356\WorkBuddy\2026-08-07-20-15-31\ai-berkshire\scripts\batch2_rotate.bat" /sc monthly /d 1 /st 09:05 /ru "%USERNAME%" /rl LIMITED /f
schtasks /create /tn "StockRotationBiweekly2" /tr "cmd /c C:\Users\17356\WorkBuddy\2026-08-07-20-15-31\ai-berkshire\scripts\batch2_rotate.bat" /sc monthly /d 15 /st 09:05 /ru "%USERNAME%" /rl LIMITED /f
```

> ⚠️ schtasks 的 `/d` 不接受逗号列表，故 1/15 日拆为两个任务。

**Agent 自动驱动**（v6）：batch1 末尾 `agent_driver review --limit 1` 对 REVIEW_DUE 标的自动调 `/investment-team` 完整重研 → 六道闸门 → 自动建仓；batch2 开头 `agent_driver batch2-research --lite-cap 30` 边界标的完整重研 + 其余 lite 速评 → `report_sync --all` 回填。详见 LOGIC.md 第八节。

## 五、手动操作命令

```bash
# 每日巡检（batch1 内容）
python3 tools/price_monitor.py            # ① 监控池日报（REVIEW_DUE/触发/接近/异动）
python3 tools/position_manager.py --daily # ② 持仓巡检（止损+估值提醒+新闻扫描）
python3 tools/pool_rotator.py --health-only # ③ 健康检查

# 深度复核（日报出现 REVIEW_DUE 时——v6 已自动化，以下为人工兜底）
python3 tools/position_manager.py --review 600036   # 开4视角重审指引（登记 PENDING）
#   → 也可直接调 agent_driver 自动复核：
python3 tools/agent_driver.py review --limit 1 --dry-run   # 预览将复核的标的与股数
python3 tools/agent_driver.py review --limit 1             # 真实执行（自动买入）
python3 tools/agent_driver.py status                       # 队列+锁状态
python3 tools/agent_driver.py retry-failed                 # 重试失败任务
python3 tools/agent_driver.py run-one --code 300573 --mode full   # 单只完整重研
python3 tools/agent_driver.py run-one --code 300573 --mode lite   # 单只速评
# 回填工具（agent_driver 内部自动调用，也可手工）
python3 tools/report_sync.py --code 600036                # 单只报告→groups/pool
python3 tools/report_sync.py --all                        # 全量回填
python3 tools/report_sync.py --list-missing               # 缺报告/解析失败清单

# 轮动（batch2 内容，约半月/一月）
python3 tools/pool_rotator.py --monthly-rotate  # 观察↔放弃 联动轮动
python3 tools/pool_kelly.py --refresh-basis     # 刷新凯利基准
python3 tools/position_manager.py --rotate      # 空位自动补位

# 查询
python3 tools/pool_kelly.py 600036              # 凯利建议仓位
python3 tools/pool_kelly.py --list              # 候选池全部仓位
python3 tools/position_manager.py --list        # 持仓列表
python3 tools/position_manager.py --report      # 持仓月报
```

## 六、数据文件

| 文件 | 内容 |
|---|---|
| `data/monitor/pool.json` | 监控池（状态机 WATCHING/TRIGGERED/REVIEW_DUE/BOUGHT） |
| `data/monitor/portfolio_groups.json` | 四组分类（buy/watch/reserve/drop，含建仓价/涨幅中位数） |
| `data/positions/kelly_basis.json` | 凯利冻结基准（G_med/σ²，月度刷新） |
| `data/positions/positions.json` | 持仓记录（成本/股数/买卖历史） |
| `data/positions/portfolio_cash.json` | 资金池总额（自动买入金额基准，当前 100 万） |
| `data/monitor/rotation_state.json` | 轮动状态+深度复核登记 |
| `data/monitor/agent_queue.json` | Agent 任务队列 |
| `data/monitor/agent.lock` | Agent 队列互斥锁 |
| `reports/monitor/daily/` | 每日监控日报 |
| `reports/monitor/agent_runs/` | 每次 codebuddy 调用日志 |
| `reports/positions/` | 持仓月报 |

## 七、已知盲点与设计说明

1. **"跌因"区分**：深度复核必须区分"错杀回调"vs"基本面恶化下跌"——前者可买，后者必须放弃（--review 指引中已强调）
2. **不设止盈的代价**：盈利可能回吐。用"估值提醒 +40% 建议复核"替代自动止盈，由人决定
3. **新闻置信度分级**：巨潮正式公告=高置信→报告；GDELT 普通新闻=低置信→仅日志
4. **基准漂移**：G_med 月度冻结，避免池构成变动导致仓位漂移
5. **凯利极端值**：高涨幅股票（如 +35%）δ 巨大，f 被 clamp 到 12% 上限——上限保护天然存在
6. **σ² 用池方差**：保守统一；个股有足够历史时可用个股 σ² 替代（pool_kelly 预留）
7. **轮动与触发独立**：轮动管"池构成"（半月/月），价格触发管"买入时机"（每日）——两条时间轴

> 更新：2026-08-13（v6：定时任务驱动 Agent 全自动） | 买入组：润贝航科/兴齐眼药/西藏药业
