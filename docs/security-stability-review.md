# TSP 源码级安全与稳定性审查报告

> 审查日期：2026-11-23
> 审查范围：`backend/app`（Python · FastAPI）、`backend-go`（Go sidecar）、`frontend/src`、部署脚本与配置。
> 结论性质：本报告区分**已核实事实**（带文件/行号证据）、**分析推断**与**优化建议**；未在本环境复现的问题不做结论性断言。
> 验证基线（本次实测）：
> - **后端全量**：`pytest tests -q` → **4877 passed, 6 failed, 7 skipped, 1 xfailed**（326s，Python 3.11.15，本机 macOS）。6 个失败见下方「测试失败项复核」，初步判断以环境性为主，上线前需在 CI 标准 Linux 环境复跑确认。
> - 后端抽样（先于全量）：`test_accounts.py`、`test_api_gateway.py`、`test_auth_session.py` 30 项通过；`test_workload.py`、`test_heavy_job_limiter.py`、`test_mining_process_lock.py` 16 项通过。
> - 前端：`pnpm build`（tsc + vite）通过，仅有 chunk 体积警告。
> - 后端 lint：`ruff check app/` 存量 522 条（类别分布见附录 A），CI 未将其设为阻断。

### 测试失败项复核（6 failed）

| 测试 | 现象 | 初步归类 | 建议 |
| --- | --- | --- | --- |
| `test_ai_provider.py::test_resolve_macos_codex_command_finds_bundled_cli` | 解析到本机真实 `/opt/homebrew/bin/codex`，而非测试临时 bundle 内的假 cli | 环境性：测试未隔离 PATH/外部安装，本机装了 homebrew codex | CI Linux 环境复跑；必要时让解析器在测试临时目录优先，或 mock 外部路径 |
| `test_ai_provider.py::test_resolve_macos_codex_command_skips_non_executable_file` | 同上，同一解析逻辑的可执行性分支 | 环境性 | 同上 |
| `test_fuyao_daily_streaming_sync.py::test_sync_promotes_staged_files_without_reading_them_back` | staging 提升路径调用了 `pl.scan_parquet` 读回临时分片（`kline_sync.py:488` 首建分区合并小文件），触发“不应读回临时文件”断言 | **待确认**：可能是小文件合并改造后测试未同步，也可能实现应改为直接移动分片 | 对照 `kline_sync.py:484-490` 的注释意图，确认是更新测试还是调整实现；不要在未确认前改代码 |
| `test_mining_lock_admin.py::test_force_release_kills_external_holder_and_takes_over` | 强制释放未能终止外部持有者进程，锁未释放 | 环境性：沙箱内跨进程信号/权限受限 | 在标准容器以同用户复跑；确认生产 Linux 下 kill 路径可用 |
| `test_mining_lock_admin.py::test_lock_endpoints_full_recovery_loop` | 同上，完整恢复链路 | 环境性 | 同上 |
| `test_mining_lock_admin.py::test_legacy_lock_holder_force_release_takes_over` | 同上，旧格式锁持有者接管 | 环境性 | 同上 |

> 说明：本次未改动任何业务代码；以上失败不直接等同于产品缺陷。上线门禁建议：CI 标准环境全量绿；若仍有失败，逐项确认是测试过期还是实现回归后再合入。

---

## 1. 架构与线上形态（事实）

| 组件 | 技术 | 部署形态 |
| --- | --- | --- |
| 主服务 | Python 3.11+ / FastAPI / uvicorn，单 worker | Docker 单容器（`Dockerfile` CMD）或 systemd `tickflow` |
| AI 研判旁路 | Go（`backend-go`，端口 8787） | dev.sh / systemd `aistock`；**Docker 镜像不打包 sidecar**，容器内该旁路不可用（降级 502） |
| 前端 | React + Vite，产物拷入后端静态目录 | 与主服务同容器，`http://host:3018` |
| 存储 | 本地 `data/`（Parquet / DuckDB / SQLite `users.db` / JSON） | 挂载持久化；`data/` 不纳入 git |
| 调度 | 进程内 AsyncIOScheduler + 启动补跑（daily catch-up） | **单副本约束**：队列、缓存、限额均为进程内，不能靠加 worker / 多容器写同一数据目录扩容 |

关键入口：`backend/app/main.py`（应用装配、认证中间件、SPA 回退、生命周期）；`backend/app/api/accounts.py`（注册/登录/会话/找回密码）；`backend/app/services/api_gateway.py`（Token 通道、scope、限流、网络门禁）。

### 已经做得比较扎实的安全/稳定性基础（保持，不要回退）

- **口令存储**：PBKDF2 20 万次迭代 + 随机 salt + `compare_digest` 恒定时间比较（`services/user_store.py`）。
- **API Token**：32 字节随机串，落库只存 SHA-256 摘要；吊销即失效（`services/api_tokens.py`）。
- **密钥文件**：`secrets.json` 0600、原子写（`secrets_store.py`）；日志经 `RedactingFormatter` 脱敏（`log_safety.py`）。
- **路径穿越防护**：策略/文件删除前 `is_relative_to` 校验；资源包解压拒绝绝对路径、`..`、盘符、反斜杠、符号链接与目录/文件冲突（`services/package_archive.py`、`api/strategy.py`）。
- **隔离与自愈**：行情单源失败隔离；看门狗探测僵死后 `os._exit(70)` 交 supervisor 重启；启动完整性自检、日 K 修补、分钟缺口修复轮、进程锁恢复、旧配置迁移；日志按大小轮转；磁盘水位/连续 5xx/管道失败进统一告警中心。
- **过载保护**：策略/回测任务队列、重任务独占槽、相同输入复用、结果短期缓存、队列满返回 503 + `Retry-After`；SDK 调用 600s deadline 守护，防止不可中断的网络读卡死连坐（`tickflow/request_guard.py`）。
- **进程锁**：flock（Windows `msvcrt`）为唯一事实，持有者定位双重扫描确认，不误伤无关进程（`services/mining_process_lock.py`）。

---

## 2. 风险清单

级别定义：

- **P1 · 上线前应处理**：公网/多用户场景下可能被直接利用，或影响服务可用性。
- **P2 · 建议加固**：当前自托管单用户下风险有限，但属于明确缺口，宜在上线窗口期补齐。
- **P3 · 观察与优化**：不阻断上线，纳入后续迭代。

### P1-1 已有账号后，注册端点对公网开放且无开关

- **事实**：`POST /api/accounts/register` 的本机/内网门禁只在“系统尚无任何账号”时生效（`api/accounts.py:194`）；首个管理员创建之后，任何能访问端口的请求都能注册账号。默认新账号角色为 `editor`，而 editor 默认获得除 `page.dev` 外的全部页面权限和全部功能权限（`services/rbac.py:115-116, 120`）。
- **影响**：若面板直接暴露公网且未在反代/网络层收口，陌生人可自助注册并进入策略、回测、因子、监控、数据等页面。
- **建议（按部署形态二选一）**：
  1. **单租户自用（推荐默认）**：在 Nginx/防火墙层封禁 `/api/accounts/register`、`/api/accounts/forgot-password`、`/api/accounts/reset-password`（或只保留登录）；公网入口前置认证（VPN / SSH 隧道 / 反代 Basic Auth）。
  2. **多用户开放**：后端增加 `ALLOW_SIGNUP`（或“注册需管理员审批/邀请码”）配置项，默认关闭；同时把 editor 默认权限收窄为只读或最小页面集。
- **补充**：注册/找回端点目前未提供“关闭自助注册”的配置开关，属于产品能力缺口。

### P1-2 功能级权限码在后端未强制，RBAC 矩阵可能与实际行为不符

- **事实**：权限码定义了 `feature.strategy_edit`、`feature.backtest_run`、`feature.ai_use`、`feature.data_export`（`services/rbac.py`），但业务写端点只校验页面权限（`api/permissions.py` 的 `require_page_access`）；`require_permission(...)` 仅用于用户管理、页面管理、配置备份、数据管理等管理类端点。功能权限码未在策略保存/删除、回测发起、AI 调用、数据导出等路径上落地校验。
- **影响**：在需要给普通用户“只读 / 限制导出 / 限制 AI”的场景下，后台分配的功能权限不会真正生效，容易形成预期偏差。
- **建议**：在对应写端点补 `Depends(require_permission(rbac.FEATURE_*))`；先以策略保存、AI 调用、数据导出三处为切入点，并补回归测试。

### P1-3 自定义策略/因子在服务端执行 Python 代码，沙箱为静态检查

- **事实**：策略代码经 AST 静态黑名单与属性名拦截（`strategy/ai_generator.py`）；因子表达式使用空 builtins + AST 白名单（`factors/py_expr.py`）。但这是“静态检查 + 进程内 exec”模式，Python 沙箱在理论上不存在绝对隔离。
- **影响**：
  - 单租户自用、策略作者即部署者：风险可接受。
  - 多租户/公网开放注册：普通用户可上传策略代码在服务器运行，属于需要认真对待的代码执行面。
- **建议**：
  - 短期：配合 P1-1 收口注册；策略保存能力仅授予可信角色。
  - 中期：把策略/因子执行移入隔离子进程（独立 working dir、限制系统调用、关闭网络），超时即杀；或在多租户形态下采用容器级隔离。
  - 长期：对用户代码做依赖/网络行为审计提示（与现有“第三方插件审查”文案一致）。

### P1-4 Docker 容器以 root 运行

- **事实**：`Dockerfile` 运行阶段未创建非 root 用户，`uvicorn` 以 root 启动。
- **影响**：若应用内存在被利用的漏洞，容器内权限偏大；挂载数据目录的宿主权限也会受影响。
- **建议**：镜像内创建专用用户（如 `app`），`chown` 数据目录后 `USER app` 启动；确认 `data/` 挂载点权限与该用户匹配。

### P1-5 Go sidecar 默认 JWT 密钥为开发占位值

- **事实**：`AUTH_JWT_SECRET` 未设置时，sidecar 使用 `dev-insecure-jwt-secret-change-me` 并打印 WARNING（`backend-go/cmd/server/main.go:229-233`）。
- **影响**：当前部署中 sidecar 仅监听本机 8787、由 panel 反代把关，风险可控；若未来 full SaaS 模式或 8787 被直接暴露，占位密钥可被伪造令牌。
- **建议**：生产环境显式设置 `AUTH_JWT_SECRET`（高熵随机串）；在部署文档与 `.env.example` 中标注必填；sidecar 仅绑定 `127.0.0.1`。

### P2-1 找回密码 / 邮箱验证码：提交端无失败计数，错误不消耗验证码

- **事实**：验证码 6 位、15 分钟有效；`consume_verification_code` 校验失败不作废该码，`reset-password` / `verify-email` 提交端没有失败次数限流或锁定（`api/accounts.py:302-352`）。发送端有 60 秒间隔限流，但尝试端没有。
- **影响**：在已知已注册邮箱的前提下，6 位数字存在被在线枚举的理论空间。
- **建议**：同一验证码连续错误 5 次即作废；按 `email + IP` 增加提交限流；锁定后需重新发送验证码。

### P2-2 后台 `asyncio.create_task` 未持有强引用，存在被 GC 的理论风险

- **事实**：以下位置 fire-and-forget 未保存任务引用：`api/kline.py:1562, 1777, 1869, 1964`、`api/pipeline.py:106`、`services/data_integrity.py:755`（ruff `RUF006`）。
- **影响**：事件循环对任务只持弱引用；极端情况下任务在完成前被垃圾回收，表现为后台同步/修复任务“静默没跑”。
- **建议**：维护模块级 `set[asyncio.Task]` 强引用集合，创建后加入，`add_done_callback` 中移除；失败回调记日志。

### P2-3 限流、计数与告警状态为进程内，重启即清零，且不支持多副本

- **事实**：登录失败计数、网关限流桶、5xx 窗口、workload 统计均为进程内 dict/deque（`api/accounts.py`、`services/api_gateway.py`、`services/system_alert.py`）；调度器无持久化 job store。
- **影响**：进程重启后首次锁定/限流计数归零；这与仓库注释声明的“单副本”约束一致，但需要在运维文档中显式告知，避免后续误扩容。
- **建议**：
  - 文档明确：不要用多 worker / 多容器写同一 `data/`；横向扩容需要先把队列、锁、计数改造为外部共享存储。
  - 若未来要多副本：登录失败计数、令牌桶、任务队列需落到共享存储（Redis 或 SQLite 持久表）。

### P2-4 会话 Cookie 未设置 `Secure` 标志，会话有效期 30 天

- **事实**：会话 Cookie 为 `HttpOnly` + `SameSite=Lax`，未设置 `Secure`（`api/accounts.py:93-101`）；会话 TTL 30 天（`services/user_store.py:48`）。
- **影响**：HTTP 明文网络下 Cookie 可被窃听；长会话提高了令牌泄漏后的可用窗口。
- **建议**：HTTPS 反代部署时按请求协议或配置下发 `Secure`；评估是否缩短会话 TTL 或提供“退出全部设备”；服务端吊销会话的能力已具备（`revoke_session`）。

### P2-5 仓库缺少 systemd unit 模板，新机器自愈拉起依赖手工配置

- **事实**：`scripts/deploy_remote.sh` 依赖服务器上已存在 `tickflow` / `aistock` 两个 unit，但仓库内没有 `.service` 模板；部署文档也未给出 unit 示例。
- **影响**：换机器或重建环境时，进程崩溃后能否自动拉起不可复现；与“少人工干预”目标不符。
- **建议**：在 `packaging/systemd/` 补充 `tickflow.service`、`aistock.service` 模板：`Restart=on-failure`、`RestartSec=3s`、`MemoryMax=`、`LimitNOFILE=`、日志走 journald，并在 `docs/deployment.md` 引用。

### P2-6 请求体缺少全局大小上限

- **事实**：上传接口有 50MB 级上限（`api/ext_data.py:1001`、`data_providers/custom/packages.py`），但 JSON/表单请求体未见全局 body 限制；uvicorn 默认不限制请求体大小。
- **影响**：恶意或异常大的请求体可能占用内存。
- **建议**：在反代层（Nginx `client_max_body_size`）限制；应用层可加一个请求体大小检查中间件。

### P3-1 异常吞没面较广，48 处 `except Exception: pass`

- **事实**：`except Exception` 共 216 处，其中 48 处直接 `pass`（附录 A）。多数是有意的单源隔离，但关键路径静默失败不利于事后排查。
- **建议**：对涉及写库、落盘、任务推进的路径，`pass` 改为记录 `debug` 级计数或消息；长期逐步收敛裸捕获。

### P3-2 前端首屏包体积偏大

- **事实**：构建产物中 `index` 约 356KB、`StockAnalysis` 约 437KB、`echarts` 约 668KB（gzip 后仍偏大）。
- **建议**：后续用 `manualChunks` 拆分 echarts/图表库，按需加载；不阻断上线。

### P3-3 自定义 Webhook 不限制内网目标（SSRF 面，管理员配置）

- **事实**：自定义 Webhook 仅校验 `http/https` scheme、存在 netloc、拒绝内嵌凭据（`services/webhook_adapter.py:357-363`），允许指向局域网地址（本地部署需要）。
- **影响**：URL 由管理员在设置页配置，不面向普通用户；但如果管理员账号被盗，可借出站请求探测内网。
- **建议**：文档提示勿将 Webhook 指向内网敏感服务；后续可增加“禁止私网地址”的可选开关。

### P3-4 部署脚本硬编码默认公网主机

- **事实**：`scripts/deploy_remote.sh` 默认 `TSP_HOST=47.119.36.147`、`TSP_REMOTE=/opt/tsp`。
- **建议**：默认值改为空并在缺失时提示，或移到 `.env`/部署说明；避免脚本在他人环境误连。

### P3-5 口令复杂度下限偏弱

- **事实**：口令最小 6 位（`services/user_store.py` 校验）。
- **建议**：管理员/公网部署提高到至少 8 位并要求字母+数字；保留 6 位兼容本地桌面场景。

### P3-6 `user_store` 每次查询都跑一次 schema 检查

- **事实**：各查询方法开头调用 `_init_schema()`，内含 PRAGMA 检查与可能的 ALTER；连接每次新建、`timeout=10s`。
- **影响**：高频只读路径上有重复元数据开销；SQLite 写锁在并发下可能等待。
- **建议**：schema 版本缓存到进程内；评估只读查询复用短连接策略。

---

## 3. 健壮性与自修复能力评估

### 3.1 现有自愈机制（已实现，建议在运维文档中显性列出）

| 场景 | 机制 | 位置 |
| --- | --- | --- |
| 进程僵死/事件循环卡住 | 看门狗周期探测，失败后退出码 70，交 supervisor 重启 | `watchdog.py` |
| 启动时数据异常 | 完整性自检 + 已知异常登记；可自动/手动修补日 K，备份原文件后写入 | `services/data_integrity.py`、`api/data.py` |
| 分钟数据缺口 | 冷启动全天修复轮 + 稳态增量轮；空轮/滞后超阈值自动再跑修复轮 | 全量分钟落盘服务 |
| 中断任务 / 旧进程持锁 | flock 锁；崩溃后内核释放；持有者定位支持旧格式锁恢复 | `services/mining_process_lock.py` |
| 旧版配置/目录迁移 | 启动前检查旧目录，密钥整份保留、偏好补缺失字段、普通文件不覆盖 | 桌面版/启动迁移路径 |
| 日志与磁盘 | 日志轮转 + 备份份数；磁盘水位 warn/critical 告警；日志脱敏 | `logfile.py`、`config.py`、`ops_monitor.py` |
| 通知去重 | 告警指纹冷却、严重度升级穿透、恢复事件只发一次 | `services/system_alert.py` |
| 上游数据源抖动 | 主备切换、健康探测、降级“未知”语义；SDK 调用 deadline 守护 | 数据源健康链 / `request_guard.py` |

### 3.2 自修复缺口与补强建议

1. **外部探活不可少**：看门狗只在进程存活时有效；进程整体崩溃/宿主机宕机时，需要外部手段（systemd `Restart=`、容器 restart policy、或外部 uptime 探测）拉起。建议上线配置：
   - systemd：`Restart=on-failure`、`RestartSec=3`、`StartLimitBurst=5`。
   - Docker：`--restart unless-stopped`，`HEALTHCHECK` 打 `/ready`（503 判未就绪）。
2. **配置与账号的定期自动备份**：行情数据可重建，但 `user_data/`（账号、会话、策略偏好、监控规则、扩展页面配置）丢了无法再生。建议增加定时任务，每日打包 `user_data`（含 `users.db`、`strategies` 引用配置、监控规则）到带轮转的本地目录或对象存储；备份排除 `secrets.json` 时需单独加密保存。
3. **盘后发布窗口纪律**：交易日 16:20–16:50 不发布/重启（盘后管道 16:40 拉日 K，16:40–17:40 自动补跑，17:40 后未定版才告警）；发布后确认当天 `kline_daily` 有 `.finalized.json`。
4. **启动补跑覆盖度**：进程重启后错过的定时任务由 daily catch-up 补跑；建议在运维手册中列出“重启后应观察到的补跑日志/告警”，便于人工快速判断是否自愈成功。
5. **告警可达性演练**：上线前分别测试飞书/企业微信/自定义 Webhook 至少一条链路；确认系统告警（数据源 down、管道失败、磁盘水位、连续 5xx、watchdog 退出）能触达部署人。

---

## 4. 上线前检查清单

### 安全收口

- [ ] 公网部署：反代/防火墙封禁自助注册与找回密码端点，或后端开启“禁止注册”开关（待补，见 P1-1）。
- [ ] 管理员口令强度 ≥ 8 位；`.env` 权限 600，不进 git。
- [ ] 显式设置 Go sidecar `AUTH_JWT_SECRET`；sidecar 仅监听 127.0.0.1。
- [ ] 启用 HTTPS 反代；Cookie 在 HTTPS 下带 `Secure`（见 P2-4）。
- [ ] 确认 `API_TOKEN_LOCAL_ONLY=1`（默认）；开放 Token 公网调用前评估数据再分发合规。
- [ ] 审阅已安装的第三方策略/数据源插件代码与网络行为。

### 稳定性

- [ ] 全量后端测试通过：`cd backend && uv run pytest tests -q`。
- [ ] 前端构建通过：`cd frontend && pnpm build`。
- [ ] 进程托管具备自动拉起：systemd unit 或 Docker restart policy + healthcheck。
- [ ] 数据目录挂载与备份策略确认；`user_data` 纳入定期备份。
- [ ] 通知链路至少一条实测可达；告警冷却与严重度符合预期。
- [ ] 确认单副本运行：未同时启动第二个写同一 `data/` 的进程/容器。
- [ ] 记录盘后窗口（15:20–15:50）不发布；准备好回滚步骤（镜像/代码版本 + 数据备份）。

### 可观测

- [ ] `GET /health` 返回版本、模式、内存快照；`GET /ready` 三态检查通过。
- [ ] `GET /api/data/workload` 可看请求量、执行量、复用/缓存命中、失败、队列与 SSE 订阅数。
- [ ] 系统告警中心可看到数据源、管道、磁盘、5xx、watchdog 类事件。
- [ ] 日志目录 `data/logs/` 轮转与磁盘占用符合预期。

---

## 5. 建议的优化路线（按优先级）

1. **第一批（上线窗口内）**：P1-1 注册收口（反代或开关）、P1-4 非 root 容器、P1-5 sidecar 密钥、P2-5 systemd 模板、P2-1 验证码失败锁定。
2. **第二批（上线后 2–4 周）**：P1-2 功能权限落地、P2-2 任务强引用、P2-4 Secure Cookie、P2-6 body 上限、`user_data` 自动备份。
3. **第三批（迭代优化）**：P1-3 执行隔离、P3-1 异常收敛、P3-2 前端拆包、P3-3 私网出站开关、P3-4 脚本配置化、P3-5 口令策略、P3-6 schema 缓存。

---

## 附录 A：ruff 存量类别（`backend/app/`，2026-11-23 抽样）

| 规则 | 数量 | 说明 |
| --- | --- | --- |
| BLE001 | 216 | 裸 `except Exception`，多为有意隔离；建议逐步补日志 |
| RUF100 | 41 | 未使用的 `noqa` 注释，可自动修复 |
| I001 | 28 | 导入排序，可自动修复 |
| SIM105 | 26 | 可抑制的异常捕获 |
| UP045 | 26 | 非 PEP604 的 `Optional` 注解 |
| UP037 | 21 | 多余的注解引号 |
| F401 | 18 | 未使用导入，可自动修复 |
| B023 | 16 | 闭包捕获循环变量（本次抽查两处均为同步立即调用，未构成实际 bug，建议结合注释复核） |
| 其他 | 剩余 | 可折叠 if、默认参数调用、zip strict、时区常量等风格项 |

> 说明：B023 在 `backtest/engine.py`、`kline_sync.py` 的两处抽查中，闭包均在同一迭代内被同步调用/等待返回，未观察到跨迭代延迟执行；后续修改这些函数时需重新确认闭包生命周期。

## 附录 B：本次审查未覆盖的部分

- 长稳压测与容量验证：全量单元/集成测试已跑，但并发压测、长时间运行与内存增长需在目标服务器按实际数据量实测（见 `docs/deployment.md` 观测与验证一节）。
- 前端 React 组件级安全逐条审查（仅排查了 `dangerouslySetInnerHTML` 等主要注入面）。
- Go sidecar 内部业务逻辑逐行审查（本次聚焦鉴权默认值、监听地址与 CORS）。
- 第三方数据源/插件的远端可用性与合规条款。
