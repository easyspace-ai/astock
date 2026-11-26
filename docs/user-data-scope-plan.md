# 前台数据权限（数据作用域）分析

> 状态：分析结论，暂未实施。范围：`/api` 业务接口 + 前端页面呈现 + 后台调度写入。
> 关联文档：[`permission-rbac-plan.md`](./permission-rbac-plan.md)（页面/功能权限）、[`deployment.md`](./deployment.md#多窗口与多人共享访问)（当前多账号契约）。

## 0. 结论摘要

现在系统里的「权限」有三层，前两层已落地，第三层基本是空的：

| 层 | 解决什么 | 现状 |
| --- | --- | --- |
| L1 认证 | 你是谁 | 已落地：邮箱账号会话 + 租户 `ContextVar` 注入（`main.py` 认证中间件） |
| L2 页面/功能权限 | 你能进哪个页面、点哪个按钮 | 页面级已落地（`services/page_access.py` + 路由 anyOf）；`feature.*` 只存在于矩阵里，服务端从未校验 |
| L3 数据作用域 | 同一页面里，这份数据是谁的 | **只覆盖 4 个存储点**：自选、AI 三类报告、助手会话、行情池自选读取 |

代码事实：`tenant.priv_path / priv_base` 全仓只有 4 处调用（`watchlist.py`、`tickflow/pools.py`、`json_report_store.py`、`custom/assistant/sessions.py`）。其余落在 `data/user_data/**` 与 `data/paper/**` 的用户资产，全部是**一台部署一份、所有账号共用**。

因此今天的实际语义是：**页面权限是「能不能看这个页面」，不是「能看到谁的数」。** 编辑者 A 建的监控规则会推给 B，A 清空告警会把 B 的记录一起删掉，A 改策略池会影响 B 的扫描结果。

这不是「二期再说」的小问题——监控、告警、持仓、模拟盘、任务这五类已经直接影响正确性，属于 P0。

## 1. 现状地图：谁在读、写在哪、被谁看见

### 1.1 已经按用户隔离的（正确）

| 数据 | 前台入口 | 落盘 | 实现 |
| --- | --- | --- | --- |
| 自选清单 + 分组 | `/watchlist` | `user_data/users/<uid>/watchlist.parquet`、`watchlist_groups.json` | `services/watchlist.py` 走 `tenant.priv_path` |
| AI 财务分析报告 | `/stock-analysis`、`/financials` | `.../ai_reports.json` | `JsonReportStore` 走 `tenant.priv_base` |
| AI 个股分析报告 | `/stock-analysis` | `.../ai_stock_reports.json` | 同上 |
| AI 大盘复盘报告 | `/review` | `.../ai_market_recaps.json` | 同上（**但后台定时写入见 §3.1**） |
| AI 助手会话历史 | `/assistant` | `.../assistant_sessions.json` | `custom/assistant/sessions.py` |

### 1.2 用户资产，但当前是全局共享（需要接入用户体系）

| # | 数据 | 前台入口 | 当前落盘 | 共享后果 | 优先级 |
| --- | --- | --- | --- | --- | --- |
| 1 | 监控规则（价格/信号/异动/日期/策略） | `/monitor`，自选/个股/策略页的规则编辑器 | `user_data/monitor_rules/*.json` | 规则互相可见、互相触发；A 删规则等于删 B 的 | P0 |
| 2 | 告警触发记录 | `/monitor`、`/dev` | `user_data/alerts.jsonl` | 全站一个文件；`DELETE /api/alerts` 清空所有账号记录；Dev 页能造演示数据污染所有人 | P0（**读放开、清空/演示已限权，按 owner 切待规则先行**，见 §1.3） |
| 3 | 持仓批次（止盈止损/到期提醒） | `/lots` | `user_data/lots/lot_*.json` | 成本价、数量、买入日期全部互见；还会派生全局监控规则 | P0 |
| 4 | 策略池 + 策略库分组/归档 | `/screener` | `user_data/strategy_pool.json` | A 的池决定 B 扫什么；归档状态互相覆盖 | P0 |
| 5 | 策略调参覆盖 | 策略/回测/挖掘 | `user_data/strategy_overrides/{sid}.json` | 改一次参数，所有人的回测与页面缓存一起变 | P0 |
| 6 | 策略结果缓存 | `/screener` 秒加载 | `user_data/strategy_cache.json` | 命中行、今日曾命中集合跨账号共享 | P0（随 5 一起） |
| 7 | 仓位策略（用户 Python 代码） | 回测设置、定义页 | `user_data/position_policies/*.py` | 代码与参数互见，且用户代码在同一进程执行 | P0 |
| 8 | 守护档案（基线/复验/组合建议） | `/guardian`、挖掘 | `user_data/strategy_guardian/*.json` | 基线、健康状态、组合建议跨账号 | P1 |
| 9 | 挖掘候选 | `/factors?tab=mining` | `user_data/research_candidates.json` | 候选池与备注互见 | P1 |
| 10 | 长任务注册表 | 回测/挖掘/滚动验证 | `user_data/task_registry.json` | **能看见并「接回」别人的运行中任务**（payload 带参数） | P1 |
| 11 | 模拟盘账户（现金/持仓/成交/净值） | `/paper` | `data/paper/accounts/*` | 账户列表与资产互见；强删可删别人账户 | P0 |
| 12 | 模拟盘跟单状态 | `/paper`（扩展） | `user_data/extensions/paper_execution/*.json` | 跟单绑定与检查点互见 | P1 |
| 13 | 界面偏好（导航布局/主题/列配置/缩放） | 全局、`/settings` | `user_data/preferences.json` 的 `ui_prefs` / `nav_*` | A 拖一次侧栏，所有人的侧栏跟着变（且这是白名单里允许普通用户写的） | P0 |
| 14 | 通知通道归属（飞书/企微/SMTP/自定义 webhook） | `/settings` | `preferences.json` + `secrets.json` | 规则命中后推到同一个群/邮箱，无法区分谁的事件 | P1（按 owner 归属） |
| 15 | 浏览器侧缓存与本地偏好 | 全局 | `localStorage`：`tf-query-cache`、`watchlist_columns`、`tf-theme`、`tf-page-zoom` 等 | 同一浏览器换账号，先渲染上一个人的自选/策略池/列配置；退出登录不清理 | P0 |

### 1.3 用户创作、但被全局计算链路消费（不能简单分区）

| 数据 | 位置 | 为什么特殊 |
| --- | --- | --- |
| 自定义信号定义 | `user_data/custom_signals/*.json` | `indicators/pipeline.py` 在生成**全局 enriched** 时加载并编译成 `csg_` 列；enriched 是全市场共享数据集，一旦按用户就变成「每人一份 enriched」 |
| 自定义/复合因子 | `user_data/custom_factors/*.json` | 同上，因子矩阵物化缓存（`factors/materialize_cache.py`）是全局 key |
| 策略源码 | `data/strategies/{custom,ai,composite}` | 策略引擎启动即全量加载（`main.py` 的 `strategy_dirs`），所有账号看到同一个策略库；策略 ID 是全局唯一命名 |

这三类是「策略研究资产」，合理的语义是**共享库 + owner 标记 + 可见性过滤**，而不是按用户复制一份数据管道。详见 §4.2。

### 1.4 应当保持全局的（不要动）

行情与维表（`kline_*`、`instruments*`、`financials`、`adj_factor`、`enriched`、因子矩阵）、指数/ETF、市场计算结果（`three_year_five_times.csv`、RPS、板块轮动、regime）、扩展数据（`ext_data`）、扩展页包（`ext_pages`）、页面菜单（`analysis_menus`）、数据源配置（`data_sources`）、AI Key / SMTP 密钥（`secrets.json`）、系统健康与完整性状态（`source_health.json`、`system_alert_state.json`、`daily_integrity_state.json`）、用户表 `users.db`。

理由：这些是「一份数据服务所有人」的共享物，按用户复制会导致重复拉取、重复计算、coverage 分裂，收益为零。

## 2. 三类用户数据，三种语义

不是所有「用户产生的数据」都该落成 `users/<uid>/`。需要先定语义，再定目录：

| 语义 | 定义 | 存储形态 | 典型 |
| --- | --- | --- | --- |
| **私有资产**（private） | 只有本人需要看，别人看到就是泄漏或干扰 | `users/<uid>/`，读写都在租户上下文内 | 自选、持仓批次、监控规则、告警、AI 报告、助手会话、模拟盘、仓位策略、策略池、任务表 |
| **共享库条目**（owned-shared） | 放在共享库里，但作者/归属可查、可见性可控 | 全局目录 + `owner_id` 元数据 + 查询过滤 | 策略源码、自定义信号、自定义因子 |
| **全局系统**（global） | 与账号无关的部署级状态 | 现状不变 | 行情、系统配置、密钥、健康状态 |

判断口径只有一句：**这份数据被第二个账号看到，是「功能」还是「事故」。** 是功能 → 共享库；是事故 → 私有。

## 3. 结构性缺陷（比清单更值得先解决）

### 3.1 后台线程没有用户上下文 → 「写全局、读用户」

`tenant.set_current_user` 只在 `main.py` 的认证中间件里调用。APScheduler 任务、行情线程、守护/挖掘子进程都没有 `ContextVar`，`priv_base()` 一律回退到全局 `user_data`。

已经发生的具体后果：

- **定时复盘**：`jobs/daily_pipeline.py` 的 `_run_scheduled_review` → `market_recap_reports.save_report()` 写到 `user_data/ai_market_recaps.json`；而 `/api/market-recap/reports` 在用户上下文里读 `users/<uid>/ai_market_recaps.json`。**用户在复盘页永远看不到定时生成的那份。**
- **告警**：`alert_store` 注释里已写死这个理由（「后台引擎无账号上下文，故保持全局单文件」）。这是把架构缺陷写成了设计。更根本的是：**产生告警的 `monitor_rules` 本身是全局的（没有 owner 字段，`JsonDefinitionStore` 直接用 `data_dir`）**，所以即便给 `AlertEvent` 加上 `owner_id`，后台引擎也填不出真值——归属顺序必须是「规则按用户 → 告警按用户」，只改告警存储等于把所有人的历史记录藏起来。
- **监控引擎**：启动时 `monitor_engine.set_rules(load_all(全局))`，一份内存规则集服务所有人；运行中新增/删除规则只影响这一份。
- **模拟盘/守护/挖掘**：`paper_watch`、`guardian_schedule`、`mining_schedule` 全部按全局目录读写。

结论：不做「后台任务按账号迭代」，只改存储路径等于把数据藏起来，功能会集体失效。这是 P0 的前置工程。

### 3.2 自定义信号/因子绑在全局指标管道上

`indicators/pipeline.py` 生成日线 enriched 时加载 `user_data/custom_signals`，把用户定义编译成列写进**共享数据集**；`factors/materialize_cache.py` 同理。如果直接把这两个目录挪进 `users/<uid>/`，enriched 会变成「谁在跑就按谁的信号生成」，其他人的列就消失——比现在更糟。

可行方向只有一个：**保持信号/因子为共享库（带 owner），个人策略引用时按定义指纹进缓存键**（`screener_work.hard_identity` 已经包含定义指纹，这条链路现在是对的）。若确实要支持「私有信号」，必须引入 `(uid, fingerprint)` 维度的 enriched 命名空间，成本要单独评估，不应顺手做。

### 3.3 `preferences.json` 一个文件装两种东西

`services/preferences.py` 顶部注释明确说「偏好是系统级配置，不随账号隔离」。但同一个文件里同时住着：

- 系统级：数据源 provider、管道排期、SMTP、webhook、AI 提示词、挖掘排期
- 个人级：`ui_prefs`（主题/缩放/声效）、`nav_layout` / `nav_hidden` / `nav_order`、`dashboard_layout`、`watchlist_columns`、`screener_result_columns`、`assistant_*`

而 `api/permissions.py` 又把个人级那 10 个路径列进 `PERSONAL_SETTINGS_PATHS` 允许普通用户写。结果是：**普通用户能写、但写的是全站共享的那一份。**

正解：`preferences.json` 只留系统级；个人级拆到 `users/<uid>/preferences.json`（读路径按租户，写路径同源）。拆的时候必须区分「调度线程 reads 系统配置」和「请求线程读写个人偏好」，否则后台读不到配置。

### 3.4 前端缓存没有用户命名空间

- `queryPersist.ts` 单一 key `tf-query-cache`，白名单里包含 `watchlist`、`watchlist-groups`、`screener-strategies`、`preferences`。换账号后首屏直接渲染上一个人的数据。
- `storage.ts` 里几十个 key（列配置、主题、缩放、分时设置）都是设备级。
- `POST /api/accounts/logout` 只 `delete_cookie`，不清 `localStorage`、不清 TanStack 缓存。

这跟后端隔离是两个独立的洞：即使后端全按用户分区，同一台电脑切账号仍然串数据。修法：持久化 key 带 `uid`（未登录用匿名前缀），登录/登出/切换账号时 `queryClient.clear()` + 清理本账号命名空间。

### 3.5 API Token 通道绕过租户

`api_gateway` 的 Bearer 通道在认证中间件里**早于**用户会话分支返回，不设置 `request.state.user`，也不 `set_current_user`。于是：

- 权限判定里 `user is None` → 视为全权限（`has_permission` 的既有约定）；
- 数据读写落在全局 `user_data`，看到的是「默认账号」的那一份。

`api_tokens.json` 的 token 记录也没有 `owner_id` 字段。开放平台要长期存在，就必须给 token 绑 owner 并注入租户上下文，否则它是一个既能越权又能串数据的外部入口。

### 3.6 `feature.*` 权限是死的，配额也是死的

- `rbac.py` 声明了 `feature.strategy_edit` / `feature.backtest_run` / `feature.ai_use` / `feature.data_export`，`permissions.py` 也下发，但**全仓没有任何 `require_permission(FEATURE_*)`**，前端也没有禁用逻辑。文档 §6.1 说「`admin.*`、`feature.*` 由后端强制」——目前只有 `admin.*` 做到了。
- `user_store.consume_quota()` 定义完整（`ai_daily` / `backtest_monthly`），但**没有任何调用点**；`/api` 层完全不提 quota。也就是说订阅等级目前只影响展示，不限制实际消耗。

这两条不解决，「用户体系」就只有登录和菜单两层皮。

### 3.7 缓存与共享计算以全局路径为键

- `strategy_cache._cache_path(data_dir)`、`strategy_run_queue` 耗时表、`screener_work.custom_work`（输入 digest）——都没有 `uid` 维度。
- `quote_service` 的市场线程调 `quote_demand.symbols(data_dir)` → `watchlist.list_symbols()` 在**无线程租户**里执行 → 读到全局默认自选。用户自己的自选不会驱动自动报价（签名里的 `watchlist.revision()` 是全局的，写一次会让所有人的缓存失效，但取到的内容仍是全局那份）。

只要数据开始分区，这些「全局 key + 全局读」的地方会立刻变成「拿 A 的输入算 B 的结果」。

## 4. 落地路线

### 阶段 0：先把「唯一出口」和「后台作用域」建好（不改业务语义）

1. 在 `tenant.py` 增加 `user_scope(uid)` 上下文管理器（后台任务 `with` 进入，退出还原）。
2. 全仓收口：业务层禁止直接拼 `settings.data_dir / "user_data"`，统一走 `tenant.priv_path/priv_base`。加一条静态测试（扫描 `services/` 与 `api/` 下的字面量），漏的用例直接失败。
3. 后台任务按账号迭代：监控评估、告警、定时复盘、守护、模拟盘跟单、挖掘排期各自改成
   `for uid in user_store.list_user_ids(): with tenant.user_scope(uid): ...`。
4. 事件与记录带 `owner_id`：`AlertEvent` 加 `owner_id`，`alert_store` 按 owner 分文件（或加列过滤），`DELETE /api/alerts` 只删自己的。

验收：单账号部署行为与现在完全一致（`priv_base()` 回退全局，兼容旧数据）；两账号并行时各写各的文件，日志能看出 owner。

### 阶段 0.5：告警的写边界（已完成 2026-11-26）

按 owner 切告警的前置是规则先按用户（§3.1），在那之前先堵住「写操作影响别人」这一层，改动面小且语义确定：

| 端点 | 处理 | 理由 |
| --- | --- | --- |
| `GET /api/alerts` | 保持开放 | 告警 toast 全局挂载，受限账号也要能收到提示 |
| `DELETE /api/alerts`（清空全部） | `admin.data_manage` | 单文件没有 owner，普通账号清空等于删掉所有人的记录 |
| `POST /api/alerts/seed`（造演示数据） | `page.dev` | 演示数据会落盘并 SSE 推给所有在线客户端 |
| `DELETE /api/alerts/{ts}`（单条） | 暂保持开放 | 单条删除伤害有限；真正的边界要等 owner 落地才能按归属判定 |

前端同步：`/monitor` 的「清空」与 `/dev` 的「清空全部」按 `admin.data_manage` 显示，无权限时不渲染（避免点了才 403）。

验证：`tests/test_alerts_api.py` 5 条（含「403 后记录仍在」「受限账号仍可读」）；`tests/test_tenant.py` 新增 3 条 AI 报告隔离回归。

### 阶段 1：私有资产迁移（P0 清单）

按 §1.2 的 1–13 项逐个改入口，每项都是「一处路径 + 一处缓存 key + 一处前端刷新」：

| 改动对象 | 关键点 |
| --- | --- |
| 监控规则 | `JsonDefinitionStore` 支持相对私有根；引擎按用户加载/热更新；规则 id 需带 owner 或全局唯一 |
| 告警 | 写入侧带 owner；查询按 owner；演示数据只进当前账号 |
| 持仓批次 | 路径 + 派生规则 owner；`lots` 页读取按租户 |
| 策略池 / 调参 / 结果缓存 | 三者必须同源迁移，否则「池是 A 的、参数是 B 的」 |
| 仓位策略 | 路径 + 代码执行隔离（同一进程执行用户代码本身是既有风险，不在本次范围但需标注） |
| 模拟盘 | `data/paper/accounts` → `users/<uid>/paper/accounts`，账户 id 作用域降为「用户内唯一」 |
| 界面偏好 | 从 `preferences.json` 拆出个人段，`/api/settings/preferences` 读写走租户 |
| 任务注册表 | 路径 + 前端「接回」只列自己的任务 |
| 前端缓存 | 持久化 key 带 uid；登出清空；切账号 `invalidateQueries(['account-me'])` + `queryClient.clear()` |

迁移必须带一次性搬迁：旧全局文件 → 首个管理员账号目录，且只在「账号数为 1」时搬，多账号时留在全局只读并提示，不允许猜归属。

### 阶段 2：共享库加归属 + 强制功能权限

1. 策略 / 信号 / 因子加 `owner_id`（策略可放 `META` 或旁挂 `strategies/.owners.json`；信号/因子加字段）。
2. 查询侧过滤：默认「自己的 + 系统内置 + 显式共享」，删除/覆盖校验 owner；ID 冲突不再静默覆盖。
3. `feature.*` 落到路由依赖（AI 分析、回测运行、策略写、导出各挂一条），前端按钮同步禁用。
4. `consume_quota` 接进 AI 与回测入口，超限 429 并给剩余额度文案。
5. API Token 加 `owner_id`，网关注入租户；未绑定 owner 的 token 只允许读共享数据。

### 阶段 3：组共享与运维面

`tenant` 从「按用户」升级为「按用户 + 权限组」；通知通道按 owner 归属（每人自己的飞书/webhook）；备份/恢复按账号目录分类（`_ACCOUNT_PRIVATE_FILES` 目前只有 5 个，`ai_market_recaps.json` 就漏了）。

## 5. 取舍与风险

| 取舍 | 说明 |
| --- | --- |
| 不为每个用户复制行情/因子/enriched | 存储与计算成本线性膨胀，且 coverage 会分裂。共享计算 + 私有结果缓存是唯一可持续的形态 |
| 自定义信号不做成私有 | 它进全局 enriched 管道；先保持共享库 + owner，私有信号需要独立的 enriched 命名空间，单独立项 |
| 后台单飞队列仍是全局 | `strategy_run_queue` 单飞是 polars 安全约束；多用户时表现为互相排队。这是容量/公平性问题，靠配额与排队提示解决，不是隔离问题 |
| 用户 Python 仓位策略同进程执行 | 已有安全边界（三关校验），但多用户后「A 的代码影响 B 的进程」风险升高，建议后续移到子进程 |
| 旧数据归属 | 只在单账号时自动搬迁到该账号；多账号时不猜，保留全局只读 + 提示 |

## 6. 验收矩阵（最低要求）

| 类型 | 用例 |
| --- | --- |
| 隔离 | 两账号各自建自选/规则/批次/模拟盘 → 互相不可见；删除只影响自己 |
| 后台 | 账号 A 开定时复盘 → 报告出现在 A 的复盘页，B 看不到；A 的规则触发只推 A 的通道 |
| 缓存 | 切换账号后策略池、结果缓存、任务列表全部换人；不出现「A 的池 + B 的参数」 |
| 前端 | 同浏览器 A→登出→B 登录：首屏无 A 的自选/列配置/导航布局；`tf-query-cache` 不串 |
| 权限 | 无 `feature.ai_use` 调 AI 接口 → 403；超 `ai_daily` → 429；admin 不限额 |
| Token | 绑定 owner 的 token 只读写该账号数据；未绑定 token 无私有数据权限 |
| 兼容 | 单账号旧数据目录启动后行为不变；`data_dir_upgrade` 不重复嵌套 `user_data` |
| 备份 | 导出含各账号私有文件；导入按原账号路径落回，不合并到全局 |

## 7. 一句话给决策

**页面权限已经够了，缺的是「同一页面里的数据归属」。** 先做阶段 0（后台作用域 + 唯一出口），再做阶段 1 的 13 项私有资产，最后才谈共享库的 owner 与组共享。跳过阶段 0 直接改路径，会把「共享」变成「谁都读不到」。
