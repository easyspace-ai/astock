# TSP 线上可观测性运行手册

本手册对应 2026-11-20 落地的 P0：请求追踪、全局异常、日志治理、`/ready`、系统级告警、磁盘水位、数据完整性与系统健康页。目标是出现故障时 **1 分钟内可定位，5 分钟内可处置**。

## 1. 三个探针的分工

| 端点 | 用途 | 失败含义 | 是否要求登录 |
|---|---|---|---|
| `GET /health` | 进程存活 | 进程挂了/端口不通 | 否 |
| `GET /ready` | 容器就绪 | 数据目录不可写、磁盘 <2GB、用户库不可读、调度器心跳 >90s | 否 |
| `GET /api/system/health` | 管理页排障快照 | 不会失败；返回 readiness/磁盘/日志/调度器/数据源/完整性 | 是（管理员） |

`/ready` **不把外部数据源不可用作为失败条件**。上游行情抖动不应导致 Docker 反复重启；数据源异常走系统告警，不阻塞容器。

Docker Compose 已配置 healthcheck：

```yaml
healthcheck:
  test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:3018/ready', timeout=5)"]
  interval: 30s
  timeout: 10s
  retries: 3
  start_period: 60s
```

`start_period` 是启动宽限；本机首次启动因 DuckDB 注册视图可能接近 60s，不要随意缩短。

## 2. 日志

真实运行日志在 `DATA_DIR/logs/`：

- `backend.log`：INFO+，含业务日志、HTTP 访问、uvicorn access/error。默认 20MB × 5 轮转。
- `error.log`：ERROR+，只保留错误与堆栈，默认 20MB × 5 轮转。
- `TSP_DISABLE_FILE_LOG=1`：仅测试/诊断使用，禁止用于生产。

每个 `/api/` 请求记录：

```
http request | request_id=... method=GET path=/api/xxx status=500 duration_ms=12.3 user_id=...
```

- 不记录 query，避免 `token`/`webhook_url` 落盘。
- 5xx 用 ERROR，耗时 ≥2s 用 WARNING。
- 响应头 `X-Request-ID` 可让用户直接报给运维；CORS 已暴露该头。
- 日志最终输出会脱敏 `api_key/token/secret/password/authorization/cookie/webhook_url`、飞书/企业微信机器人地址。

`httpx/httpcore/urllib3/apscheduler` 已降到 WARNING，避免盘中逐请求 INFO 淹没业务日志。

## 3. 系统级告警

统一入口：`app.services.system_alert.emit()`，写入现有 `user_data/alerts.jsonl` 并复用 SSE `alert` 事件与飞书/企业微信/自定义 Webhook。

| 事件 | 严重度 | 触发 | 恢复事件 |
|---|---|---|---|
| `daily_not_finalized` | critical | 17:40 后今日日K仍无定版标记（16:40 起每 10 分钟补跑，17:40 后只告警） | `daily_finalize_recovered` |
| `daily_finalize_catchup` | info/critical | 启动/巡检触发的官方日K补跑完成或失败 | 无（补跑结果本身） |
| `index_universe_incomplete` | warn | 指数维表少于 9 只必需指数（如 899050.BJ 缺失） | 维表补齐后 `index_universe` 告警自然停止 |
| `source_down` | critical | 数据源连续失败进入 down | `source_recovered` |
| `source_degraded` | warn | 探测失败/数据时间偏旧 | `source_recovered` |
| `pipeline_failed` | critical | 盘后/盘前调度任务异常 | `pipeline_recovered` |
| `integrity_repair_failed` | warn/critical | 自动修补异常或返回 error | `integrity_repair_recovered` |
| `integrity_blocked` | critical | 待修复分区 >0 | `integrity_recovered` |
| `integrity_unavailable` | warn | 完整性状态文件不可读 | `integrity_recovered` |
| `disk_low` | warn/critical | 磁盘低于 10GB/2GB | `disk_recovered` |
| `log_file_disabled` | critical | 文件日志 handler 停用 | `log_file_recovered` |
| `scheduler_unhealthy` | critical | 调度器未运行或心跳 >90s | `scheduler_recovered` |
| `http_5xx` | critical | 5 分钟内 ≥5 次 5xx | 冷却后再次触发 |
| `watchdog_exit` | critical | 看门狗主动退出进程 | 无（进程重启） |

去重与恢复规则：

- 同一 `dedupe_key` 的故障在 `OPS_ALERT_COOLDOWN_S`（默认 1800 秒）内不重复写/推。
- 严重度升级（warn→critical）可穿透冷却。
- `*_recovered` 必须与故障使用同一 `dedupe_key`，只在 active 时发一次并清除 active。
- 状态文件：`user_data/system_alert_state.json`，原子写。

## 4. 巡检线程

`OpsMonitor` 在 lifespan 启动，独立 daemon 线程，默认 60 秒：

- 磁盘：`shutil.disk_usage`，阈值 `OPS_DISK_WARN_GB=10` / `OPS_DISK_CRITICAL_GB=2`。
- 文件日志：`file_logging_disabled()`。
- 数据完整性：`daily_health.snapshot()` 的待修复条目。
- 调度器：`running` + `system_heartbeat_at` 心跳（60 秒 job，90 秒超时）。
- 盘后定版：16:40 起检查今天 `kline_daily` 是否有 `.finalized.json`；没有则静默启动官方日K补跑，16:40–17:40 每 10 分钟一次（与正常管道共用 run slot）。17:40 仍未定版才发 `daily_not_finalized` critical，并停止自动重跑；进程启动补偿不受此窗口限制。

即使 API 线程池被卡住，该线程仍能发现磁盘/完整性/调度器异常并告警。

## 5. 排障路径

### 5.1 `/ready` 返回 503

看 `checks` 里哪一项 `ok=false`：

- `data_dir`：目录不可写 → 查挂载/权限/磁盘。
- `disk`：剩余 <2GB → 立即清理；此时文件日志可能已停用。
- `user_db`：SQLite 不可读 → 查 `user_data/users.db` 权限/损坏；不要直接删库。
- `scheduler`：未运行/心跳超时 → 查启动日志 `scheduler not started`、`system_heartbeat` job。

外部数据源异常不会让 `/ready` 失败；如果 `/ready` 正常但业务失败，去 `/api/system/health` 看 `sources` 与 `integrity`。

### 5.2 用户报错

先要 `X-Request-ID`，然后：

```bash
rg "request_id=<id>" DATA_DIR/logs/backend.log DATA_DIR/logs/error.log
```

`error.log` 有完整堆栈；`backend.log` 有同一请求的 method/path/status/duration。

### 5.3 数据完整性阻塞

`/api/system/health` 的 `integrity`：

- `pending_count > 0`：到数据页执行自检/修补；自动修复有退避，别反复点。
- `status=unavailable`：状态文件损坏/不可读，查 `user_data/daily_integrity_state.json`。
- 系统告警 `integrity_blocked` 的 `data` 里有 `days/assets/kinds`，可判断是股票还是指数、快照还是缺口。

### 5.4 数据源异常

`/api/system/health` 的 `sources.counts` 与 `targets`：

- `down` 优先于 `degraded`；`unknown` 不单独告警。
- 切换次数看 `switches`；当前主备链只有 `realtime`/`financial` 支持备源，且无备源时不会自动切换。
- `source_down` 只代表探测失败，不等于全市场数据不可用；结合 `pipeline_failed`/`integrity_blocked` 判断影响面。

### 5.5 磁盘写满

`disk_low` critical + `log_file_disabled` 通常同时出现：

1. 清理 `DATA_DIR/logs/*.log.*` 旧轮转、Docker json 日志、临时文件。
2. 确认 `df -h` 恢复 >2GB；`/ready` 会自动恢复 200。
3. 文件日志 handler 停用后需要 **重启进程** 才恢复；重启前终端日志仍可用。

### 5.6 连续 5xx

`http_5xx` 告警的 `data` 有窗口与次数。用 `error.log` 找同一时间段的 `unhandled exception` / `unhandled request exception`，按 `request_id` 定位。

### 5.7 盘后日K未定版 / 又出现“盘中快照”

先看今天分区有没有定版标记：

```bash
DAY=$(TZ=Asia/Shanghai date +%F)
ls -l DATA_DIR/kline_daily/date=$DAY/.finalized.json
```

- 没有标记且已过 16:40：日志搜 `启动补跑`、`日K未定版`，此时只重试不告警。16:40–17:40 每 10 分钟重试；17:40 后仍未定版才触发 `daily_not_finalized` critical，并停止自动重跑。
- 有标记但分区仍含非空 `quote_ts`：说明打标后又被写盘。看日志 `实时行情已停止日K落盘`；若没有该日志，说明走了绕过仓库守卫的写入路径，必须立即回滚。
- 标记内容 `source=official_daily_batch` 且 `rows/expected` 达标才算真定版；`max_quote_ts` 只作记录，不作为完整性依据。
- 标的池维表含退市股，线上正常官方覆盖率约 94.5%（如 5,570/5,894）。定版阈值是 93%，低于它才视为源侧大面积缺失；不要按 98% 要求，否则每天都会误判未定版并反复补跑。
- 修复历史坏日：数据页手动修正会先清除旧标记；修补完成后重新跑盘后管道/补跑才恢复定版。

## 6. 关键配置

```dotenv
OPS_ALERT_ENABLED=true
OPS_ALERT_COOLDOWN_S=1800
OPS_DISK_WARN_GB=10
OPS_DISK_CRITICAL_GB=2
OPS_DISK_CHECK_INTERVAL_S=300   # 保留兼容；实际巡检周期用 OPS_MONITOR_INTERVAL_S
OPS_MONITOR_INTERVAL_S=60
OPS_HTTP_5XX_WINDOW_S=300
OPS_HTTP_5XX_THRESHOLD=5
LOG_FILE_MAX_MB=20
LOG_FILE_BACKUP_COUNT=5
ERROR_LOG_MAX_MB=20
ERROR_LOG_BACKUP_COUNT=5
```

校验：`OPS_DISK_WARN_GB` 必须大于 `OPS_DISK_CRITICAL_GB`；所有周期/阈值必须为正；日志轮转参数至少为 1。

## 7. 验证清单

```bash
# 后端测试
cd backend && TSP_DISABLE_FILE_LOG=1 .venv/bin/python -m pytest -q

# 语法/静态检查（至少新增模块）
cd backend && .venv/bin/python -m ruff check app/log_safety.py app/services/system_alert.py \
  app/services/system_health.py app/services/ops_monitor.py app/api/system.py

# 前端类型与构建
cd frontend && npx tsc --noEmit && npm run build

# 运行态
curl -i http://127.0.0.1:3018/health
curl -i http://127.0.0.1:3018/ready
curl -i -H 'X-Request-ID: trace-test' http://127.0.0.1:3018/api/system/health
```

## 8. 已知边界

- `/api/system/health` 需要管理员会话；未登录返回 401 是预期。
- 系统告警默认推送到所有已配置的飞书/企业微信/自定义 Webhook；没有配置时只写 `alerts.jsonl` + SSE。
- 数据源自动切换仍只覆盖 `realtime`/`financial`，且需要配置备源；不是单次请求失败立即切换。
- 前端有根级 ErrorBoundary 与 `window.onerror`/`unhandledrejection` 控制台兜底，但尚未把前端错误上报到后端。
- 看门狗退出前会尽力发 `watchdog_exit`（最多等待 3 秒），随后 `os._exit(70)`；能否自动拉起取决于 Docker/supervisor。
