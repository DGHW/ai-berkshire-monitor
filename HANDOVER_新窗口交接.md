# 新窗口工作交接提示词（AI Berkshire 买卖闭环系统）

> 本文件是**新会话恢复指令**。新窗口打开后，把「一、恢复指令」整段内容粘贴给 Agent 即可无缝接手。
> 生成时间：2026-08-12 ｜ 逻辑版本：v5 ｜ 数据快照：2026-08-12

---

## 一、恢复指令（直接粘贴给新会话）

```
请先阅读以下文件恢复上下文，然后只做盘点与状态确认，不要开始任何开发：
1. C:/Users/17356/WorkBuddy/2026-08-07-20-15-31/ai-berkshire/data/monitor/LOGIC.md
   （买卖闭环 v5 唯一逻辑定义，一切操作以此为准）
2. C:/Users/17356/WorkBuddy/2026-08-07-20-15-31/ai-berkshire/data/monitor/README.md
   （操作手册）
3. C:/Users/17356/WorkBuddy/2026-08-07-20-15-31/ai-berkshire/HANDOVER_新窗口交接.md
   （本项目交接文档，含项目目标/进度/文件/宪法/验收/风险）
4. 记忆文件：C:/Users/17356/.codebuddy/projects/c-Users-17356-WorkBuddy-2026-08-07-20-15-31/memory/project_portfolio_v5.md

读完先输出一份状态确认：
- 四组股票数量（应为 买入3/观察92/后备91/放弃59）
- 监控池状态（应为 95 只 WATCHING）
- 持仓数（应为 0，无持仓）
- 凯利基准（G_med=7.5%, σ²=196.9, 冻结于 2026-08-12）
- 定时任务是否已注册（schtasks 查询 StockMonitorDaily / StockRotationBiweekly）
然后等待我的指令，不要擅自开始开发或派发 Agent。
```

---

## 二、项目目标

在 `ai-berkshire/` 工作区建立一个**可无人值守运行的 A 股价值投资买卖闭环系统**：

1. **研究层**：对全市场速评卡池股票做"四视角"深度研究（段永平=商业模式 / 巴菲特=财务估值 / 芒格=行业竞争 / 李录=风险评估），每只产出固定格式报告（含内在涨幅、击球区🟢🟡🔴、目标建仓价、二次补仓价、数据核验✓/⚠️）。已全部完成 249 只。
2. **分类层**：按现价 vs 建仓价中位数分四组：买入组 / 观察组 / 后备组 / 放弃组。
3. **监控层**：每日价格扫描，状态机 WATCHING → TRIGGERED（首日触价登记）→ REVIEW_DUE（连续2日确认）→ 深度复核 → 买入。
4. **持仓层**：止损 -20%（唯一自动）、内在涨幅兑现止盈（已实现 ≥ 内在涨幅×80%）、估值提醒 +40%（不自动卖）、基本面转换卖出、高置信新闻报告。
5. **仓位层**：中位数偏差凯利（pool_kelly）——`G_med`=候选池内在涨幅中位数，`δᵢ=Gᵢ−G_med`，`f=δ/σ²` 半凯利，clamp [0, 12% / 小盘 8%]。
6. **轮动层**：观察组/放弃组每半月-一月批量重跑四视角，据新研究结果轮动。

---

## 三、已完成阶段

| 阶段 | 状态 | 说明 |
|---|---|---|
| 批量四视角研究 | ✅ 完成 | 249 只 × 4 视角报告，落盘 `reports/{code}{名称}-{视角}-{人物}视角.md`，每份含"## 量化结论" |
| 数据解析入库 | ✅ 完成 | `portfolio_raw.json` → `latest_prices.json`（245 只最新价 2026-08-12）→ 分组 |
| 四组分类 | ✅ 完成 | 判定规则：`zone_all_red`（击球区≥3个🔴）→ 放弃组；`price/entry_med≤1.0` → 买入组；`≤1.15` → 观察组；否则后备组 |
| 买卖闭环工具 | ✅ 完成 | `pool_kelly.py`（中位数偏差凯利）、`position_manager.py`（买入/卖出/每日巡检/轮动补位/深度复核）、`pool_rotator.py`（月度轮动）、`price_monitor.py`（状态机）全部可运行 |
| v5 逻辑定义 | ✅ 完成 | `LOGIC.md` 唯一逻辑定义 + `README.md` 操作手册 |
| 定时任务脚本 | ✅ 编写完成 | `batch1_daily.bat`（每日15:05）、`batch2_rotate.bat`（每月1/15日09:05） |
| 记忆持久化 | ✅ 完成 | `memory/project_portfolio_v5.md` + MEMORY.md 索引 |

---

## 四、当前进度（2026-08-12 快照，已实测核验）

- **分组**：买入 3 / 观察 92 / 后备 91 / 放弃 59（`data/monitor/portfolio_groups.json`）
- **监控池**：95 只，全部 `WATCHING`（`data/monitor/pool.json`）
- **买入组 3 只**（已深度回调至建仓价下方，**买入前需复核各自研究报告的治理/基本面风险**）：
  - 001316 润贝航科：现价 27.22 vs 建仓 32.5（区间 24.0-32.5），内在涨幅中位 12.5%，四视角🟡
  - 300573 兴齐眼药：现价 42.19 vs 建仓 49.0（区间 38.0-49.0），内在涨幅中位 82.5%，两视角🟡
  - 600211 西藏药业：现价 38.94 vs 建仓 44.0，内在涨幅中位 39.0%，一视角🟡（注意：名称字段为 "?"，已由报告文件名人工确认）
- **持仓**：0 只（`data/positions/positions.json` 为空）
- **凯利基准**：`G_med=7.5`、`σ²=196.9`、`n=95`、冻结于 2026-08-12（月度冻结）
- **轮动状态**：`rotation_state.json` 含 last_rotation / alerts / reviews（1 条）
- **定时任务**：两条 bat 已写好，但 **schtasks 尚未实际注册**（待用户确认后执行）
- **买入触发状态**：尚无 TRIGGERED / REVIEW_DUE，体系首日干净启动

---

## 五、正在处理的文件或模块

| 路径 | 角色 | 状态 |
|---|---|---|
| `ai-berkshire/data/monitor/LOGIC.md` | **唯一逻辑宪法（v5）** | 定稿 |
| `ai-berkshire/data/monitor/README.md` | 操作手册 | 定稿 |
| `ai-berkshire/tools/position_manager.py` | 持仓买卖/每日巡检/轮动补位/深度复核 | 定稿可运行 |
| `ai-berkshire/tools/pool_kelly.py` | 中位数偏差凯利（含 --refresh-basis） | 定稿可运行 |
| `ai-berkshire/tools/pool_rotator.py` | 月度轮动（--monthly-rotate） | 定稿可运行 |
| `ai-berkshire/tools/price_monitor.py` | 监控池状态机扫描 | 项目已有，未改动 |
| `ai-berkshire/tools/financial_rigor.py` | 数据核验真源（cross-validate/three-scenario） | 项目已有，强制使用 |
| `ai-berkshire/scripts/batch1_daily.bat` | 每日 15:05 定时任务 | 已写，未注册 |
| `ai-berkshire/scripts/batch2_rotate.bat` | 每月 1/15 日 09:05 轮动任务 | 已写，未注册 |
| `ai-berkshire/data/monitor/{pool,portfolio_groups,rotation_state,watch_rank,ticker_map}.json` | 运行时数据 | 2026-08-12 快照 |
| `ai-berkshire/data/positions/{positions,kelly_basis}.json` | 持仓与凯利基准 | 持仓空/基准冻结 |
| `ai-berkshire/reports/` | 249×4 报告（唯一事实源） | 全部存在 |

---

## 六、下一步任务必须遵守的 Agent 宪法与真源文档

### 宪法（不可违反，按优先级）

1. **唯一逻辑来源**：`data/monitor/LOGIC.md`（v5）。工具行为与文档冲突时，以 LOGIC.md 为准并修正工具；改逻辑必须先经用户确认。
2. **数据核验强制**：任何涉及 ROE / PE-TTM / 市值 / 三情景估值的结论，必须调用 `python3 tools/financial_rigor.py cross-validate --field roe/pe_ttm --values '{...}' --ts-code {code}.{SZ/SH/BJ}` 做交叉验证，并报告 ✓/⚠️。ROE 注意速评卡口径陷阱（Q1 年化虚高/淡季反向低估/保守口径，必须与年报加权真值对照）。
3. **金融豁免**：银行/券商用 ROE+PB（券商可加 PE 参考），保险用 EV/PEV，不单用 PE。
4. **报告固定格式**：每份研究必须以 `## 量化结论` 结尾，含 内在涨幅 / 击球区 / 目标建仓价 / 二次补仓价 / 数据核验 五个字段，机器解析依赖此格式。
5. **批量研究模式**：用 investment-team 技能，每批 4 股票 × 4 视角 = 16 Agent 并行；受 Agent 上限（600/会话）与内存约束，分批推进。
6. **买卖纪律**：不设固定止盈；置信源（内在涨幅）失效即移出监控；基本面转换才卖；买入需"第1次 TRIGGERED 登记 + 第2次确认 + 基本面未变 + 涨幅变化不大"。
7. **仓位纪律**：仓位一律由 pool_kelly 计算（冻结基准 G_med/σ²，月度刷新），不得手工拍脑袋。

### 真源文档（只读、先读再动手）

- `data/monitor/LOGIC.md` — 逻辑宪法
- `data/monitor/README.md` — 操作手册（含命令清单）
- `reports/` — 249 只四视角报告，解析数据的唯一事实源
- `tools/financial_rigor.py` — 数据核验真源
- 记忆：`C:/Users/17356/.codebuddy/projects/c-Users-17356-WorkBuddy-2026-08-07-20-15-31/memory/`（MEMORY.md 索引 + project_portfolio_v5.md）

---

## 七、验收标准

1. **状态一致**：运行 `python tools/price_monitor.py --dry-run`、`python tools/position_manager.py --daily`、`python tools/pool_rotator.py --health-only` 均正常输出、无异常退出；分组/监控池/持仓数与第四节快照一致。
2. **逻辑可执行**：LOGIC.md 中每条规则都能在工具中找到对应实现（状态机、止损、兑现止盈、轮动、凯利）。
3. **无人值守**：两条定时任务注册成功（`schtasks /query` 可见 StockMonitorDaily、StockRotationBiweekly），bat 内路径/解释器正确。
4. **买入闭环可走通**：任一股出现 TRIGGERED → REVIEW_DUE → --review 四视角 → position_manager --add（输出凯利建议仓位）全链路演示无误。
5. **报告可解析**：对 `reports/` 任一文件，`portfolio_raw.json` 解析逻辑能提取全部五个量化字段。
6. **无脏数据**：`portfolio_groups.json` 中无名称 "?" 的股票（600211 需人工补录后确认）。

---

## 八、已知风险与未完成事项（重要：请勿擅自继续开发）

以下为 v5 逻辑定义与工具实现之间的**已知差距**，以及待办事项。**本次交接只做状态交接，不展开实现**；是否推进由用户决定。

### 已知差距（逻辑已定义、工具未实现）
1. **每日四视角涨幅刷新**（LOGIC.md 第二节 B）：每日对建仓价内/已持有标的重复四视角重估内在涨幅 G —— 无自动脚本，目前持仓 gain_med 来自初始报告快照。
2. **置信源失效自动检测**（LOGIC.md 第二节 C）：G 获取失败/数据源中断/报告缺失/交叉验证不一致 → 自动标 REMOVED —— 仅文档定义，工具未实现。
3. **轮动期批量四视角深度分析**（LOGIC.md 第五节 1）：batch2 目前只做规则轮动（pool_rotator --monthly-rotate），未集成"批量派发四视角 Agent"。
4. **定时任务注册**：两条 bat 未注册进 schtasks（命令已写入 README/LOGIC.md）。
5. **买入组 3 只买入前复核**：润贝航科/兴齐眼药/西藏药业需先复核各自研究报告的治理/基本面风险再考虑买入。

### 已知技术风险
- **Agent 上限与内存**：单会话 Agent 上限 600（已设环境变量），Node 堆内存曾触顶崩溃 → 长会话务必分批、保存 resume 快照、必要时重启。
- **Windows 路径**：Python 一律用正斜杠；glob/正则匹配路径时用 `os.path.basename` 提取代码（反斜杠会破坏 `^reports/` 匹配）。
- **数据时效**：latest_prices/分组为 2026-08-12 快照，价格每日由 price_monitor 刷新；分组与监控池在轮动/触价后需同步更新。
- **网络抖动**：派发 Agent / 拉行情遇 502 或 ENOTFOUND 时重试即可，勿视为失败。
- **正则脆弱性**：报告解析正则对格式变化敏感（已修两处：`**🟡**` 与 `+7%`），改报告模板前必须先回归解析脚本。
- **凯利单位换算**：δ 与 σ² 必须以小数（/100, /10000）计算，否则仓位稀释 100 倍（已修复，勿回退）。

---

## 九、环境信息

- 工作区根目录：`C:/Users/17356/WorkBuddy/2026-08-07-20-15-31/ai-berkshire/`
- Python：`C:/Users/17356/.workbuddy/binaries/python/envs/default/Scripts/python.exe`（命令行可用 `python`）
- 行情源：`tools/quote_fetcher.py`（腾讯/新浪双源）；新闻：`tools/news_fetcher.py`
- 记忆目录：`C:/Users/17356/.codebuddy/projects/c-Users-17356-WorkBuddy-2026-08-07-20-15-31/memory/`
- 恢复指令见本文档第一节。
