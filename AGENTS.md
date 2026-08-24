# AGENTS.md — Agent 操作手册

本文件是 Agent 进入本工作区的自动加载入口。**先读完本文件再动手。**

## 本仓库是什么

A 股价值投资买卖闭环 v7：四视角 Agent 深度研究 → 六道闸门（确定性代码）→ 双轨交易（本地记账+富途模拟盘）。已投入每日无人值守运行（7 持仓、定时任务、自愈机制）。

## 绝对规则

1. **不要动生产状态**：`data/monitor/`、`data/positions/`、`reports/*.md` 是运行状态与研究成果，除非用户明确要求处理特定条目，否则只读
2. **不要改六道闸门与反锚定的判据**：阈值是冻结的先验值（见 `docs/anti-overfitting.md`），任何修改需用户明示
3. **bat 文件编码**：`scripts/*.bat` 必须保持 ASCII + CRLF（曾因 UTF-8+LF 导致定时任务连续失败）
4. **不要大规模批量调用 LLM**：重研 30-90 分钟/只，每日仅 1 只 full + 少量 lite；研究类任务先问成本
5. **研究结论驱动，价格只做触发**：任何"因为现价便宜所以值得买"的逻辑都是违规（反过拟合核心原则）

## 常用操作速查

```bash
# 查状态
python tools/agent_driver.py status              # 队列状态
python tools/futu_bridge.py --positions          # 模拟盘持仓
python tools/futu_bridge.py --reconcile          # 双轨核对
python tools/price_monitor.py --ticker 600036    # 单股检查

# 手动触发
python tools/agent_driver.py review --limit 1    # 处理 1 只 REVIEW_DUE（含重研+闸门+买入）
python tools/agent_driver.py run-one 600036 招商银行  # 指定股票完整重研

# 日志
reports/monitor/cron_agent.log                   # Agent 运行日志（买入/拦截/补写）
reports/monitor/daily/cron.log                   # 定时任务心跳日志（[STEP] 标记）
reports/monitor/daily/{date}-monitor.md          # 每日监控日报
```

## 常见故障处置（按顺序）

1. **OpenD 掉线**（查询返回 ok:false）：启动 `%APPDATA%\Futu_OpenD\Futu_OpenD.exe` 等用户登录
2. **队列卡 in_progress + 锁陈旧**：`rm data/monitor/agent.lock` 后看 cron_agent.log 判断是续跑还是重试（报告新鲜<48h 且可解析 → 续跑 skip 重研）
3. **补写**（四视角质量不合格）：`python -c "import sys; sys.path.insert(0,'tools'); import agent_driver as ad; ad.backfill_stale_views('CODE','名称','fix_xxx', ['视角1','视角2'])"`
4. **富途下单失败但本地记账成功**：修好 OpenD 后 `python tools/futu_bridge.py --buy CODE 股数 价格` 补单
5. **挂单未成交**：现价>挂单价（高开）→ 撤单回滚等回落；现价<挂单价（低开）→ 降级不接飞刀。都走 `futu_bridge --auto-fix` 或手动

## 研究任务入口

- 用户要求"研究某股" → 用 `/investment-team {code} {name}` 技能（四视角并行）
- 产出必须落盘为 `reports/{code}{名称}-{视角}.md` 四个文件，含 `## 量化结论` 五字段（内在涨幅/击球区/目标建仓价/二次补仓价/数据核验）
- 研究完成后跑 `python tools/report_sync.py --code CODE` 验证解析

## 关键文档

- `data/monitor/LOGIC.md` —— 闭环全部判据细节（最高权威）
- `docs/anti-overfitting.md` —— 反过拟合四层防线
- `docs/agent_backtest_brief.md` —— 回测开发任务书
- `README.md` —— 项目全景
