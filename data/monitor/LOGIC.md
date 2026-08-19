# 买卖闭环逻辑定义 v6（2026-08-13）

> 本文件是系统的**唯一逻辑定义**，工具实现与日常操作均以本文件为准。
> 数据：249 只四视角深度研究报告（段永平/巴菲特/芒格/李录）→ `data/monitor/portfolio_groups.json`
> v6 变更：定时任务驱动 Agent 全自动（自动复核/自动买入/轮动混合深度研究），详见第八节。

---

## 一、核心原则

1. **买入靠"基本面没改观 + 价格跌"**：深度复核确认基本面未变、价格在击球区，才买入
2. **卖出靠"内在涨幅兑现 + 基本面转换"**：不设固定止盈，只设止损与内在涨幅兑现
3. **置信源失效即移出**：内在涨幅（四视角中位数）数据源出问题 → 移出监控，不依赖失效数据决策
4. **轮动靠批量深度分析**：观察组/放弃组每半月-一月批量重跑四视角，据结果轮动
5. **自动化边界**（v6）：研究→回填→轮动→买入全链路由定时任务驱动 Agent 自动执行；人工仅处理 quarantine（三次失败）与月报审阅

---

## 二、监控池（每日 batch1）

### A. 价格扫描（现有 price_monitor 状态机）
```
WATCH(观察) → 现价进入击球区[建仓价×0.85, 建仓价]
    → 第1天 TRIGGERED（登记 triggered_since，首次扫到登记一下）
    → 连续2天仍在击球区 + 基本面没变 + 内在涨幅变化不大 → REVIEW_DUE
    → --review 开4视角深度复核 → 通过 → 买入组 → 建仓
```
- **第1次扫到**：TRIGGERED 登记（只登记，不动作）
- **第2次扫到**：确认仍在建仓价内 → REVIEW_DUE → 深度复核 → 买入

### B. 每日四视角涨幅刷新（建仓标的小，成本可控）
- 每日对**建仓价内/已持有**的少量标的批量重跑四视角（或调用 financial_rigor 重核），重复得出内在涨幅 G
- 用途：①兑现止盈判断 ②基本面变化检测 ③置信源健康检查

### C. 置信源失效 → 移出监控
- 触发条件：涨幅 G 无法获取 / 数据源中断 / 报告文件缺失 / 交叉验证不一致
- 动作：将该股从监控池标记 `REMOVED`，移出监控层次，不再自动决策
- 恢复：数据源恢复 + 重新四视角研究后才可重新入池

---

## 三、持仓侧（每日 batch1）

| 信号 | 触发 | 动作 |
|---|---|---|
| 止损 | 盈亏 ≤ -20% | 自动卖出信号 |
| **内在涨幅兑现止盈** | 已实现涨幅 ≥ 内在涨幅 G × 80% | **止盈卖出信号** |
| 估值提醒 | 盈亏 ≥ +40% | 建议复核（不自动卖） |
| 基本面转换 | 论文过期/财报恶化/治理爆雷/涨幅 G 转负 | 卖出 |
| 高置信新闻 | 巨潮正式公告冲击基本面 | 报告人决策 |

---

## 四、买入执行

```
--add 登记 → 仓位 = 中位数偏差凯利（pool_kelly.py）
  G_med = 候选池(买+观)内在涨幅中位数（月度冻结基准，当前 7.5%）
  δᵢ = Gᵢ − G_med
  f = δ/σ²池（连续凯利）→ 半凯利 → clamp [0, 12% / 小盘8%]
```

---

## 五、轮动（每半月/一月 batch2）

1. **批量四视角深度分析**：观察组 + 放弃组全部重跑四视角，更新各自内在涨幅 G、建仓价、击球区
2. **选择轮动**（基于新研究结果）：
   - 观察组轮出 → 后备组：涨超建仓+40% / 论文过期>90天 / 涨幅转负恶化
   - 放弃组轮入 → 观察组：深度回调至建仓价下方（需重新研究确认）
   - 后备组轮入 → 观察组：价格回落至建仓+15%内
3. **基准刷新**：pool_kelly --refresh-basis（月度冻结更新）
4. **空位补位**：position_manager --rotate

---

## 六、定时任务

| 任务 | 周期 | 命令 |
|---|---|---|
| batch1 每日 | 15:05 每日 | `scripts/batch1_daily.bat`（价格扫描+持仓巡检+健康检查） |
| batch2 轮动 | 每月 1/15 日 09:05 | `scripts/batch2_rotate.bat`（批量深度分析+轮动+基准刷新） |
| 深度复核 | REVIEW_DUE 时手动 | `--review {code}` → /investment-team 四视角 → 回填 |

---

## 七、数据文件

| 文件 | 用途 |
|---|---|
| `data/monitor/pool.json` | 监控池状态机（WATCHING/TRIGGERED/REVIEW_DUE/BOUGHT/REMOVED） |
| `data/monitor/portfolio_groups.json` | 四组分类 + 建仓价 + 内在涨幅中位数（report_sync 回填） |
| `data/positions/kelly_basis.json` | 凯利冻结基准（G_med/σ²） |
| `data/positions/positions.json` | 持仓（成本/股数/gain_med/买卖历史） |
| `data/positions/portfolio_cash.json` | 资金池总额（自动买入金额基准） |
| `data/positions/futu_config.json` | 富途模拟盘执行层开关（enabled/dry_run，默认 dry_run=true） |
| `data/monitor/rotation_state.json` | 轮动状态 + 深度复核登记 |
| `data/monitor/agent_queue.json` | Agent 任务队列（pending/in_progress/done/failed/quarantined） |
| `data/monitor/agent.lock` | Agent 队列互斥锁（O_EXCL + 24h 陈旧抢占） |
| `reports/monitor/agent_runs/{run_id}.log` | 每次 codebuddy 调用全量 stdout/stderr |

---

## 八、Agent 自动驱动（v6 新增）

### A. 触发链路

```
schtasks（Windows 计划任务）
  ├─ StockMonitorDaily  每日 15:05 → batch1_daily.bat
  │     price_monitor → position_manager --daily → pool_rotator --health-only
  │     → agent_driver review --limit 1（REVIEW_DUE → 自动复核 → 自动买入）
  └─ StockRotationBiweekly(1日)/Biweekly2(15日) 09:05 → batch2_rotate.bat
        agent_driver batch2-research --lite-cap 30（混合深度研究）
        → pool_rotator --monthly-rotate → pool_kelly --refresh-basis
        → position_manager --rotate → pool_rotator --report
```

### B. codebuddy 无头调用（agent_driver.py）

- 命令：`codebuddy -p "<提示词>" -y --output-format json --max-turns N`
  - `-y` 必须（否则文件/命令/网络操作全被阻止）
  - 提示词以 `/investment-team {code} {name}`（完整版）或 `/investment-team-lite {code} {name}`（速评）开头触发技能
  - 环境变量：`CODEBUDDY_RETRY_WATCHDOG=1`（无人值守重试）、`CODEBUDDY_CODE_DISABLE_BACKGROUND_TASKS=1`（-p 模式强制禁后台任务，技能内 run_in_background 自动降级前台）
- **成功判定唯一权威**：reports/ 下该股四视角文件存在且 ≥3 份 `## 量化结论` 五字段可解析（stdout json 不含文件内容，不可信）
- 完整版超时 90min / 速评 20min；失败 tries≤2，达上限 quarantine（人工处理）

### C. 自动买入六道闸门（全过才执行 --add）

1. 四视角报告齐全且可解析（research_succeeded ≥3/4）
2. 新内在涨幅 gain_med > 0
3. 击球区非全红（zone_all_red == False）
4. 涨幅未恶化（新 gain_med ≥ 旧 gain_med − 10pct）
5. 决策时点实时现价 ≤ buy_zone.high（研究期间价格漂移保护）
6. 凯利建议仓位 > 0

仓位 = 中位数偏差凯利（pool_kelly 冻结基准）× 资金池 total_cash；股数整手向下取整（100 股）；`pool.status → BOUGHT`。

### D. 轮动混合深度（batch2-research）

- 边界标的（pool_rotator --plan：watch 轮出/ drop 轮入 / reserve 轮入 阈值触发）→ `/investment-team` 完整重研（并发 1）
- 其余 → `/investment-team-lite` 速评（并发 2，--lite-cap 每轮上限，默认 30）：
  - verdict=FAIL → 移入放弃组（由 agent_driver 执行）
  - PASS/HOLD → 保留原指标（不重估五字段，仅验证"未证伪"）
- 全部完成后 `report_sync --all` 回填 → 规则轮动用新指标决策

### E. 自动买入金额基准

`data/positions/portfolio_cash.json`：`total_cash` = 资金池总额（当前 100 万）。单票金额 = 凯利仓位 % × total_cash，硬顶不超 total_cash。

### F. 富途模拟盘执行层（双轨）

自动买入时**双轨执行**：① 本地记账（position_manager --add，唯一决策源）② 富途 OpenD 模拟盘真实撮合下单（`tools/futu_bridge.py`，验证价格与执行）。

- 交易环境**硬编码 SIMULATE**（模拟盘），代码无 REAL 路径；实盘需富途客户端手动操作
- **模拟盘失败绝不影响记账与状态机**（futu 仅验证层，失败只记 pool.note 告警）
- `data/positions/futu_config.json`：`enabled=true`、`dry_run=false`（已正式启用）、`acc_id=20552058`（沪深模拟账户）
- 代码映射：`6xx/688→SH.`、`0/2/3→SZ.`、`4/8/920→BJ.`（北交所模拟盘不支持则跳过）
- 前置：OpenD 运行并登录富途账号（端口 11111），futu-api SDK ≥10.4.6408
- 官方技能：`~/.codebuddy/skills/futuapi`（/futuapi 自然语言行情交易，默认模拟环境）

### G. 研究落盘归一（normalize_reports）

`/investment-team` 完整版产出的综合报告（技能默认命名）不满足四文件规范 → 自动二次 codebuddy 调用把综合报告拆写为四视角规范文件。提示词用**相对路径 + markdown 列表**（长绝对路径会被消息截断——实测根因）。

### H. 反锚定机制（2026-08-14 用户判据）

锚定效应判据 = **时间序列漂移**：两次重研之间，基本面无恶化（新 gain_med ≥ 旧 gain_med − 10pct）但建仓价下调 >15% → 锚定嫌疑。

- **提示词层**：重研必须先读旧报告对比基本面；基本面无变化则建仓价沿用旧值（±5% 微调）；只有基本面实质恶化才能下调且须列证据；禁止以"股价下跌"作为下调理由
- **机械层**（lock_entry_price，接入 run-one/review/batch2 三入口）：触发时建仓价锁定回旧值（groups.entry_med + pool.buy_zone），仅保留新 gain_med

### I. 8+4 分批建仓（2026-08-19 用户确认）

- **初仓**：凯利 clamp 12% → **8%**（打 2/3，留 1/3 子弹；小盘股维持 8% 不设补仓）
- **补仓触发**：持仓股（BOUGHT）现价 ≤ 二次补仓价（四视角报告 entry_min）→ `ADD_DUE`
- **补仓复核**（agent_driver add_review_once）：ADD_DUE → lite 速评 → **PASS/HOLD（错杀）→ 补仓 4%**（8+4=12 总敞口封顶）；**FAIL（证伪）→ 不加仓 + 7 天冷却**（防每日重触发循环）
- **封顶**：每笔最多补 1 次（positions.add_count）；补仓价锚定报告值，禁止随股价下移
- 触发链：price_monitor ADD_DUE → batch1 review 内自动复核 → 双轨补仓（记账+富途）

---

## 九、闭环全景（v7）

### 买卖闭环（日频 · StockMonitorDaily 15:05）

```
价格扫描(price_monitor) ── 现价入击球区 ──→ 直接 REVIEW_DUE（v7:取消2天确认）
        │                                        │
        │                      agent_driver review --limit 1（自动）
        │                                        │
        │                      /investment-team 完整重研（4 Agent 并行）
        │                                        │
        │                              研究落盘归一（四文件+五字段）
        │                                        │
        │                              report_sync 回填 groups
        │                                        │
        │                              反锚定漂移检测（锁建仓价）
        │                                        │
        │                              六道闸门全过？
        │                           ┌────┴────┐
        │                           │ 否      │ 是
        │                           ▼         ▼
        │                      不买(记录原因)  双轨建仓：
        │                                       ├ 本地记账 --add（决策源）
        │                                       └ 富途模拟盘下单（验证层）
        │                                        │
        │                                   pool.status=BOUGHT
        │
        └── 持仓巡检(position_manager --daily，已持有标的每日)：
              止损 -20%（唯一自动卖）/ 兑现止盈 gain×80%
              / 估值提醒 +40%（不自动）/ 基本面转换 → 卖出
```

### 挑选轮动闭环（半月频 · StockRotationBiweekly 每月1/15日 09:05）

```
pool_rotator --plan（只读边界清单）
        │
        ├─ 边界标的（轮出/轮入阈值触发）→ /investment-team 完整重研（并发1）
        │                                     → 归一 → 回填 → 反锚定检测
        ├─ 其余标的 → /investment-team-lite 速评（并发2，每轮≤30只）
        │             verdict=FAIL → 移放弃组；PASS/HOLD → 保留
        │
        ▼
report_sync --all（全量回填 groups）
        │
        ▼
pool_rotator --monthly-rotate（基于新指标做组移动）
        │
        ▼
pool_kelly --refresh-basis（凯利基准月度冻结更新）
        │
        ▼
position_manager --rotate（BOUGHT 空位补位：drop/reserve → watch）
        │
        ▼
pool_rotator --report（轮动报告落盘）
```

### 已知不一致（待修）

- `position_manager --rotate` 补位只更新 pool.json（status/group=WATCH），**不同步 portfolio_groups.json 组归属**——上海银行 601229 已入监控池但 groups 仍标 drop。影响：kelly 候选池（buy+watch）漏计补位标的。修复方向：--rotate 补位时同步 groups 组移动，或 monthly_rotate 前做 groups/pool 一致性校验。

---

> 逻辑版本：v7 | 更新：2026-08-14
