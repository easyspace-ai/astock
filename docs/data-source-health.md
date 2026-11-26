# 数据源健康与主备切换

对应《投研辅助系统开发计划——第一个月》第 1 条「数据接口验证」。目标是把行情、公告、财务、新闻四类上游的连通性与数据新鲜度变成可定时观测、可查询、可告警、可审计的能力，而不是等到页面取数失败才暴露。

## 设计边界

- **旁路探测**：只取最小样本（1 个标的、1 页公告/新闻），只写健康状态，不写业务表，不进入行情轮询与盘后管道热路径。
- **路由级切换**：主备切换只发生在一个轮询周期结束后整体换源，**不在单次请求内混合多个源**，避免同一份数据出现来源拼接。
- **只告警不切换**：日 K、分钟 K、公告、新闻第一阶段只记录状态；自动切换仅覆盖实时行情与财务数据。
- **状态不含密钥**：`DATA_DIR/user_data/source_health.json` 只保存源名、状态、延迟、数据时间与错误摘要。

## 探测目标

目标由「能力矩阵 × 已声明且可用的源」自动生成，配置增减数据源后无需改代码。

| 类别 | 目标 | 探测内容 |
| :--- | :--- | :--- |
| 实时行情 | `realtime.<provider>` | 单标的实时快照 |
| 分钟 K | `minute.<provider>` | 单标的近 1 分钟线 |
| 日 K | `daily.<provider>` | 单标的近 5 日日线 |
| 财务 | `financial.<provider>` | 单标的 `metrics` 最新一期 |
| 公告 | `announcements.cninfo` / `announcements.em` | 当日公告首页 |
| 新闻 | `news.ths` / `news.em` | 最新资讯首页 |

每次探测有 12 秒超时；失败后同一轮自动重试一次，仍失败才累加连续失败。单个源失败或抛异常只影响自己的状态，不阻断其他目标。

## 状态与阈值

| 状态 | 含义 |
| :--- | :--- |
| `ok` | 探测成功且数据时间在阈值内 |
| `unknown` | 探测成功但上游未返回可解析的数据时间 |
| `degraded` | 数据时间超出阈值，或连续失败 1–2 次 |
| `down` | 连续失败达到 3 次，进入 300 秒冷却 |

数据时间阈值：

| 能力 | 最大落后 |
| :--- | :--- |
| 实时行情 | 120 秒 |
| 分钟 K | 10 分钟 |
| 日 K | 36 小时 |
| 财务 | 120 天（覆盖季度报告周期） |
| 公告 | 36 小时 |
| 新闻 | 7 天（单只标的新闻天然稀疏） |

每个目标保留最近 20 条探测历史。成功一次即清零连续失败并解除冷却。

## 定时任务

调度注册在 `backend/app/jobs/daily_pipeline.py`：

| 任务 | 时间 | 范围 |
| :--- | :--- | :--- |
| `source_health_market` | 每 60 秒（连续竞价且避开开盘前 5 分钟：09:35–11:30 / 13:05–15:00） | 实时行情 + 分钟 K |
| `source_health_info` | 每天 07–23 点每 30 分钟 | 公告 + 新闻 |
| `source_health_daily_preopen` | 工作日 08:45 | 日 K |
| `source_health_daily_close` | 工作日 17:00 | 日 K |
| `source_health_financial` | 工作日 07:30 | 财务 |

调度入口失败只记日志，不向调度线程抛异常。

## 主备链

### 配置

在 **设置 → 数据源 → 数据源健康** 每类卡片底部选择备源：

- `不切换（只用主源）`：只记录告警，`resolve_provider` 始终返回主源；
- 选择备源：保存为 `[主源, 备源]` 有序链。

也可以直接调用接口：

```bash
GET /api/settings/data-sources/fallbacks
PUT /api/settings/data-sources/fallbacks
{"realtime": ["tickflow", "fuyao"], "financial": ["tickflow", "fuyao"]}
```

备源必须是已注册源，且声明了对应数据集（`realtime` / `financial`），否则不会被选中。

### 切换语义

`source_health.resolve_provider(capability, primary)` 在每次取数开始时解析生效源：

1. 能力不属于 `realtime` / `financial`，或主备链没有第二个源：直接返回主源，不读状态文件；
2. 主源状态为 `ok` / `unknown` 且连续失败为 0：保持主源；
3. 主源为 `degraded` / `down`：按链顺序选择第一个健康备源；
4. 非连续竞价时段（午休、盘后）及开盘/午后开盘前 5 分钟实时行情不切换，避免把正常停滞误判为故障；
5. 选中结果写入 `active`，变化时追加 `switches` 审计（`primary_unhealthy` / `primary_recovered`）。

已接入点：

- `app/services/quote_service.py`：实时行情全市场拉取前解析；
- `app/services/financial_sync.py`：财务同步判定与 `get_financials` 取数前解析。

### 恢复

主源重新探测为健康后，下一次解析会自动切回主源，并记录 `primary_recovered`。不会因为一次探测抖动立刻回切：连续失败计数与冷却窗口用于抑制抖动。

## 查询接口

| 方法 | 路径 | 说明 | 权限 |
| :--- | :--- | :--- | :--- |
| GET | `/api/settings/data-sources/health` | 分类快照、目标明细、切换审计、当前生效源 | 登录用户可读 |
| POST | `/api/settings/data-sources/health/probe` | 立即探测，body `{"scope":"all"}` | 设置写权限 |
| GET | `/api/settings/data-sources/fallbacks` | 读取主备链 | 登录用户可读 |
| PUT | `/api/settings/data-sources/fallbacks` | 保存主备链 | 设置写权限 |

`scope` 可选 `all` / `market` / `slow` / `info`，或单能力 `realtime` / `minute` / `daily` / `financial` / `announcements` / `news`。

## 验收步骤

1. 启动后端，打开 **设置 → 数据源**，确认「数据源健康」区出现实时行情、分钟 K、日 K、财务、公告、新闻分类；
2. 点「立即探测」，观察每类源的状态、延迟、探测时间、数据时间与连续失败次数；
3. 将实时行情主源临时配成不可用源（例如错误的 Key），连续探测 3 次后确认状态变为「不可用」；
4. 给实时行情配置备源并触发取数，确认行情仍能更新，健康区「最近切换」出现 `primary_unhealthy`；
5. 恢复主源并探测成功，再次取数后确认切回主源且出现 `primary_recovered`；
6. 调用 `GET /api/settings/data-sources/health`，确认返回结构包含 `categories` / `targets` / `switches` / `active`。

## 已知限制

- 外网不可达时探测会失败并标记源异常，这属于预期告警；不影响已落盘的本地数据读取。
- 公告与新闻继续沿用资讯中心双源抓取策略；健康状态只用于展示与告警，不强制单源切换。
- 日 K / 分钟 K 的自动切换涉及落盘口径与复权一致性，第一阶段不启用。
- `probe all` 会串行探测全部目标，目标较多时耗时可能接近分钟级；设置页默认只做一次全量探测，日常由调度增量覆盖。
