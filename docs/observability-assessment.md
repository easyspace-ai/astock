# TSP 线上可观测性与安全可控性评估

> 评估时间：2026-11-20 17:20–17:25（Asia/Shanghai）
> 评估对象：`/Users/leven/space/test/tsp`
> 当前运行实例：`uvicorn app.main:app --reload --host 0.0.0.0 --port 3018`
> 当前实际 `DATA_DIR`：`/Users/leven/space/product/data`
> 本报告只做评估，未修改代码、配置或业务数据。
> **实施状态（2026-11-20 18:57）：P0 六项已落地，运行手册见 [`observability-runbook.md`](observability-runbook.md)。**

## 一、结论

**当前版本适合内网/灰度运行，但不满足“无人值守线上运行、出问题能实时发现并处理”的标准。**

已经具备的基础比多数项目好：业务日志落盘、日志轮转、数据源健康探测、主备路由、业务告警中心、认证/Token、能力门控、重任务限流、死锁看门狗、数据完整性自检。但这些能力目前是“分散的业务能力”，还没有连成一条**运维闭环**：

> 探测 → 判定 → 记录 → 告警 → 定位（请求/日志）→ 处置 → 恢复验证

当前缺的正是这条闭环里的**请求级追踪、系统级告警、就绪探活、磁盘水位、前端异常兜底**。

## 二、直接回答三个问题

### 2.1 有没有“自动探真、自动检测数据源连通性”？

**有，而且是真实网络探测，不是只读配置。**

- 代码：`backend/app/services/source_health.py`
- 覆盖：`realtime`、`minute`、`daily`、`financial`、`announcements`、`news`
- 方式：每个源取最小样本真实请求；实时/分钟每 60 秒，公告/新闻每 30 分钟，日 K 每日 08:45/17:00，财务每日 07:30
- 状态：`ok / unknown / degraded / down`；连续失败 3 次 `down`，冷却 300 秒
- 落盘：`<DATA_DIR>/user_data/source_health.json`
- 当前实测（16:48）：12 个目标中 10 个 `ok`，2 个 `unknown`（分钟 K 盘后无数据、东方财富公告返回 0 行），`switches=[]`

**但“探真”的深度有限：**

- 只探 1 个标的（贵州茅台）/ 1 页公告 / 1 页新闻，不能证明全市场 5,000+ 标的完整。
- 只看“有没有返回、数据时间是否超阈值”，不看字段口径、重复行、OHLC 合法性、指数/ETF 覆盖。
- `unknown`（返回成功但没有可解析数据时间）在 `resolve_provider` 里被当作**健康**，所以“空数据”不会触发切换。
- 数据完整性由另一套 `daily_integrity` 负责，两者没有联动。今天就是典型例子：数据源健康探测显示日 K 源 `ok`，但 11-19 全市场日 K 是盘中快照，指标/监控全部被阻断。

### 2.2 有没有“自动切换”？

**代码已经实现，但当前生产配置下不会发生切换。**

- 仅 `realtime`、`financial` 两类支持自动切换；日 K、分钟 K、公告、新闻只告警不切换。
- 切换是**路由级**的：一个轮询周期结束后整体换源，不在单次请求内混源。
- 必须先在「设置 → 数据源 → 数据源健康」配置备源链。
- 当前真实配置：`realtime=["tdxrs"]`、`financial=["market_data"]`，**没有备源**，所以 `resolve_provider` 永远返回主源。
- 即使配置了备源，也不是“单次请求失败立刻换源”：`quote_service` 里明确注释 `no cross-source fallback`，一次拉取失败只记日志，等下一轮再按健康状态决定。
- 实时行情在非连续竞价、开盘/午后开盘前 5 分钟不切换，避免午休/盘后误判。
- 切换成功会写 `active` 和 `switches` 审计，但**不会写告警中心，也不会发飞书/企微**。

**结论：能力已具备，配置未启用，且只覆盖“硬失败”，不覆盖“空数据/坏数据/部分缺失”。**

### 2.3 有没有日志？报错能不能分析？

**有应用日志，但不足以做线上排障。**

当前真实日志：

- 应用日志：`/Users/leven/space/product/data/logs/backend.log`
- 轮转：10MB × 3 个备份，总计约 40MB
- 格式：`%(asctime)s [%(levelname)s] %(name)s: %(message)s`
- 当前文件：3.7MB、16,320 行，时间跨度 2026-11-18 15:31 至 2026-11-20 17:22
- 当前日志中：`httpx` 4,666 行、`tdxrs.client` 5,962 行、`WARNING` 8,158 行、`ERROR` 34 行

**缺什么：**

- `uvicorn.access` / `uvicorn.error` 不进入这个 rotating file，HTTP 访问日志和未捕获异常 traceback 可能只在终端/容器 stdout。
- 没有 `request_id` / `trace_id`，一次请求的多条日志串不起来。
- 没有全局 `Exception` handler，500 只返回通用错误，前端拿不到错误编号，后端也没有带 path/method/user/request_id 的统一错误记录。
- 没有独立 `error.log`，ERROR 和 INFO 混在一起。
- 没有日志关键字告警（ERROR、Traceback、数据源 down、磁盘满）。
- 没有脱敏过滤器；`httpx` 把完整 URL（含 query）打进 INFO，如果某个数据源把 key 放在 query 里就会泄露。
- 没有按天归档，只有按大小滚动；盘中全量拉取时 10MB 可能很快滚完，旧证据被覆盖。

**实测证据：**

- `backend.log` 中没有 `uvicorn` 记录；`backend-restart.log` 里有访问日志，但那是终端重定向产物，没有轮转，不属于应用日志体系。
- 2026-11-17 19:30 出现 `[Errno 28] No space left on device`，文件日志 handler 连续失败后**自动停用文件输出**，之后只剩终端日志。`app/logfile.py` 的错误提示还写成“多为日志文件被其他进程占用”，把磁盘满误判成文件占用。
- 2026-11-20 12:03–12:45，pytest 的注入错误（`tests/test_*.py`、`pytest-of-leven` 临时目录）直接写进了这份生产日志。如果按 ERROR 关键字做告警，会被测试噪声直接打爆。

## 三、当前真实故障（不是假设）

### 3.1 11-19 全市场日 K 是盘中快照，指标与监控被阻断

- `daily_integrity_state.json` 有 **5,226 条** 2026-11-19 的 `snapshot` 记录：5,217 只股票 + 9 个指数，`needs_raw=true`、`needs_enriched=true`。
- 启动自检明确：`股票日K：2026-11-19 的数据为停机前的盘中快照；指数日K：2026-11-19 的数据为停机前的盘中快照 (共 2 个坏分区)`。
- 14:18 修复失败：`2026-11-19 缺少修补股票: 000002.SH, 000680.SH, 899050.BJ, 原数据保持不变`。
- 14:45 盘后管道失败：同一原因；矩阵缓存预热也因同一原因失败。
- 17:20 启动后，行情轮询仍每 10 秒左右报一次 `enriched 计算失败 / 监控评估失败`；`depth` 启动补跑也失败。
- 自动修复进入退避：`自动修复退避中 ... 剩余 177 分钟`。
- **数据源健康探测仍显示日 K 源 `ok`，没有任何系统告警通知到人。**

这就是“探真”和“数据完整性”脱节的直接后果：探活说源是通的，但数据不可用；系统自己在日志里反复报错，人却只能事后翻日志。

### 3.2 日志噪声已经把信号淹没

- `tdxrs.client` 在 14:50–14:52 对每一只被剔除的标的打一条 WARNING，单次就产生约 5,900 行。
- `httpx` 对每个外部请求打一条 INFO，单日 4,666 行。
- 当前 16,320 行日志里，这两类占 65%。
- 结果：真正的 `integrity repair failed`、`pipeline failed`、`matrix cache prewarm failed` 被埋在几千行“回包校验剔除”里。

### 3.3 磁盘曾经写满，且没有预警

- 当前磁盘：460GB，已用 393GB，剩余 33GB，使用率 **93%**。
- 2026-11-17 19:30 已经发生过一次 `ENOSPC`，直接导致文件日志停用。
- 代码中没有 `disk_usage` / `statvfs` / 磁盘水位检查。
- Docker Compose 没有 `logging` driver 限制，长期运行 `json-file` 也可能把宿主机撑满。

### 3.4 历史生产错误也说明“没有告警”

- 2026-11-18 15:55 定时盘后管道失败：`日K 连续 20 只标的失败, 疑似服务端异常: 深市代码前缀不明: '988107.SZ'`。
- 2026-11-20 14:18 自检修补失败。
- 2026-11-20 14:45 管道失败 + 矩阵缓存预热失败。
- 这些错误都只在日志里，没有进入告警中心，没有 webhook，没有页面提示。

## 四、能力矩阵

| 能力 | 现状 | 缺口 | 线上风险 |
| :--- | :--- | :--- | :--- |
| 应用文件日志 | 有，10MB×3 | 不含 uvicorn access/error；无 error.log；无脱敏；无按天归档 | 500/请求级问题无法落盘追踪 |
| 请求追踪 | 无 | 无 request_id、无 method/path/status/user 结构化记录 | 无法把一次故障串起来 |
| 全局异常兜底 | 只有业务异常 handler | 无 `Exception` handler | 500 无上下文、无错误编号 |
| 数据源探活 | 有，真实请求 | 单标的浅探；`unknown` 视为健康 | 空数据/坏数据不切换 |
| 自动切换 | 代码有，仅实时/财务 | 当前无备源；非请求级；无告警 | 实际不切换 |
| 数据完整性 | 有自检/修补/退避 | 与探活、/health、告警未联动 | 数据坏了但“健康” |
| 业务告警 | 有 alert_store + SSE | 只覆盖监控/复盘/模拟盘成交 | 系统故障不通知 |
| 系统告警 | 无 | 无 source down、pipeline failed、5xx、磁盘、日志停用告警 | 只能人工发现 |
| liveness | `/health` | 只证明进程活着 | 依赖坏了仍显示 ok |
| readiness | 无 | 不检查数据目录、磁盘、调度器、DB | 容器编排误判健康 |
| 进程守护 | watchdog + Docker restart | watchdog 只处理死锁；当前 dev 是 `--reload` 前台进程 | 进程退出不一定自动拉起 |
| Docker 健康检查 | 无 | 无 healthcheck | 容器“running”但服务不可用 |
| 前端异常 | 无 ErrorBoundary | 无 `window.onerror`、`unhandledrejection`、上报 | 白屏只能等用户反馈 |
| 磁盘水位 | 无 | 无阈值、无预警 | 已发生过 ENOSPC |
| 日志轮转 | 有，40MB 上限 | 无独立错误文件；无长期归档 | 证据保留短，故障后可能已滚掉 |

## 五、建议的落地顺序

### P0：上线前必须补（最小闭环，不引入 ELK）

1. **请求级可观测性**
   - 新增 `request_id` 中间件：读取 `X-Request-ID` 或生成 UUID；写入 `request.state`；响应头回写 `X-Request-ID`。
   - 访问日志只记 API：`method path status duration_ms user_id request_id`；静态资源不记。
   - 新增全局 `Exception` handler：记录完整 traceback + 上述上下文；返回 `{"code":"INTERNAL","message":"服务器内部错误","request_id":"..."}`。
   - 新增独立 `error.log`（ERROR 及以上，20MB×5），与 `backend.log` 分开。

2. **日志治理**
   - `logging.getLogger("httpx").setLevel(WARNING)`；`apscheduler.executors.default` 降为 WARNING。
   - `tdxrs.client` 的逐标的“回包校验剔除”改为每批一条汇总：`batch=..., requested=..., hit=..., skipped=..., sample=...`。
   - pytest 运行时禁用文件 handler，或强制 `DATA_DIR` 指向临时目录；生产日志里不能再出现 `pytest-of-*`。
   - 加统一 redaction：`api_key/token/secret/password/Authorization/Cookie/webhook` 一律脱敏。

3. **`/ready` 就绪探活**
   - `/health` 保持 liveness：进程活着即可。
   - 新增 `/ready`：检查 `DATA_DIR` 可写、磁盘剩余 > 2GB、用户 DB 可读、调度器心跳 < 90s、最近一次探活记录存在。
   - **不要把外部数据源可用性作为 `/ready` 的失败条件**，否则上游抖动会导致容器反复重启；数据源状态放到 `/status` 或健康页。
   - Docker Compose 加 `healthcheck` 指向 `/ready`。

4. **系统级告警**
   - 新增 `system_alert` 桥接，复用现有 `alert_store` + SSE + 飞书/企微/自定义 webhook。
   - 事件：`source_down/source_recovered`、`pipeline_failed`、`integrity_blocked`、`continuous_5xx`、`disk_low`、`log_file_disabled`、`watchdog_exit`。
   - 只在状态跃迁时发，按 `fingerprint` 去重，默认 30 分钟抑制；恢复时发一条 `recovered`。
   - 数据源 `down`、管道失败、磁盘 < 2GB 必须立即通知；其余可聚合到日报。

5. **磁盘与日志保护**
   - 每 5 分钟检查 `shutil.disk_usage(DATA_DIR)`：<10GB WARNING，<2GB CRITICAL。
   - `app/logfile.py` 的 `handleError` 修正错误提示：`Errno 28` 明确写“磁盘空间不足”，不要统一说成“文件被占用”。
   - 文件日志停用后写一条系统告警，并尝试降级到 stderr 结构化输出，不要静默。

6. **数据完整性接入运维闭环**
   - `daily_integrity` 的 `blocking_assets` / `bad_partitions` 变化写系统告警。
   - `/ready` 或 `/status` 暴露 `data_integrity: blocked`，但不要让容器重启。
   - 当前 11-19 的坏分区，建议先人工到「数据 → 自检/修补」处理，不要只等 177 分钟后的自动重试；同时检查为什么 `000002.SH / 000680.SH / 899050.BJ` 这三个指数码拉不到。

### P1：上线后尽快补

- 前端 `ErrorBoundary` + `window.onerror` + `unhandledrejection`，显示“错误编号”，上报到后端；只上报错误栈，不采集表单/持仓等敏感内容。
- Docker `logging` driver：`json-file` + `max-size: 10m` + `max-file: 5`，避免宿主机被日志撑满。
- 日志按天归档/压缩，至少保留 14 天；ERROR 日志单独保留 30 天。
- 告警去重/抑制/升级：同指纹 30 分钟一次；连续 3 次升级；恢复通知。
- 生产关闭或限制 `/docs`、`/redoc`、`/openapi.json`；`/health` 只保留最小字段，详细内存/版本信息放到鉴权后的 `/status`。
- 生产不要用 `uvicorn --reload`；用 Docker CMD 或 systemd/launchd 托管，确保退出后自动拉起。

### P2：规模化再补

- 结构化 JSON 日志（字段：ts、level、logger、request_id、user_id、method、path、status、duration_ms、event、error_code）。
- Prometheus 指标：请求数/5xx/延迟分位、数据源成功率、管道时长、磁盘、内存、调度延迟。
- Loki/ELK 日志聚合；自托管 Sentry/GlitchTip 做异常聚合。
- SLO 面板与每日运维摘要：错误 Top N、数据源可用率、管道成功率、磁盘趋势。
- 多实例部署时再做分布式追踪和日志聚合。

## 六、上线验收清单

- [ ] 注入一次 500，`error.log` 中能用 `request_id` 找到完整 traceback、path、method、user。
- [ ] 响应头返回 `X-Request-ID`，前端错误页能显示同一编号。
- [ ] 停掉一个数据源（或断网），3 次探测后状态变 `down`，告警通道收到通知；配置备源后能看到 `switches` 记录。
- [ ] 恢复数据源，收到 `recovered` 通知，主源自动切回。
- [ ] 把磁盘阈值临时调到当前剩余以上，收到 `disk_low`；`/ready` 返回 503。
- [ ] 停掉调度器/把 `DATA_DIR` 改成只读，`/ready` 返回 503，但 `/health` 仍 200。
- [ ] 跑一遍 pytest，生产 `backend.log` 不再出现 `pytest-of-*` 和注入错误。
- [ ] 盘中跑一次全量拉取，`backend.log` 不再出现逐标的 WARNING 刷屏。
- [ ] 触发一次前端组件异常，页面显示兜底 UI，后端收到错误上报。
- [ ] 重启容器，`docker compose ps` 的 health 状态正确；日志 driver 不超过配置上限。

## 七、关键文件索引

- 日志初始化：`backend/app/main.py`（`logging.basicConfig`）、`backend/app/logfile.py`
- 日志级别：`backend/app/config.py`（`log_level="INFO"`）
- 探活：`backend/app/api/routes.py` 的 `GET /health`
- 认证白名单/中间件：`backend/app/main.py`
- 看门狗：`backend/app/watchdog.py`（默认 30s 间隔、15s 超时、连续 2 次失败 `os._exit(70)`）
- 业务告警：`backend/app/services/alert_store.py`
- 数据源健康：`backend/app/services/source_health.py`
- 数据完整性：`backend/app/services/data_integrity.py`、`backend/app/services/daily_health.py`
- 盘后管道：`backend/app/jobs/daily_pipeline.py`
- 前端入口：`frontend/src/main.tsx`；路由：`frontend/src/router.tsx`；API：`frontend/src/lib/api.ts`
- 部署：`docker-compose.yml`、`Dockerfile`、`docs/deployment.md`、`操作说明书.md` §26.16
- 当前日志：`/Users/leven/space/product/data/logs/backend.log`
- 当前状态：`/Users/leven/space/product/data/user_data/source_health.json`、`daily_integrity_state.json`

## 八、一句话总结

**“探真”有了但不够深，“切换”有了但没启用，“日志”有了但不够用，“告警”有业务没系统。上线前把请求 ID、全局异常、`/ready`、系统告警、磁盘水位、日志治理这六件事做完，才算形成可观测闭环。**
