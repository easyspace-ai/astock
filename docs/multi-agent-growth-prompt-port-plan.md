# 多 Agent 研判 / 三年五倍 / 提示词管理 移植分析与实施方案

> 来源仓库：`<source-repo>/`（下称「源仓库」）
> 目标仓库：`<repo>/`（下称「tsp」）
> 整理日期：2026-11-17
> 状态：**差距分析 + 移植方案**，尚未改动业务代码。所有结论均来自两侧仓库当前代码；标注「待确认」的条目需要落地前核实。

---

## 0. 结论摘要

### 0.1 三块功能 + 一个后端的归属

| # | 需求（用户口径） | 源仓库落地位置 | 形态 |
| --- | --- | --- | --- |
| 1 | 个股分析 AI 模块里的**多轮讨论**模块 | 前端 `features/tradingAgents/*`；Go 后端 `internal/analysis/*` | 多 Agent 协同工作流：6 分析师并行 → 博弈裁判 → **多空 3 轮辩论（Loop）** → 研究总监 → 交易员 → 三方风控 → 风险经理裁决；SSE 流式推送 |
| 2 | **三年五倍**菜单 | 前端 `pages/StockGrowth.tsx` + `components/stock-growth/`；Python 后端 `api/stock_growth.py` + `services/stock_growth.py` | 纯 Python/Polars 选股扫描页，结果落 CSV，本地前复权日 K 缓存计算 |
| 3 | **提示词管理**菜单（后台、超级管理员管理） | 前端 `pages/PromptAdmin.tsx`；Go 后端 `analysis/prompts/*` + `interfaces/http/prompts_handler.go` | Agent 系统提示词查看/覆盖，覆盖值持久化为 JSON 文件，下次构建 Agent 时热加载 |
| 4 | 「**格浪**」后端项目整体移植 | 源仓库 `backend-go/`（Go module `aistock/backend`，Gin 服务，端口 8787） | **判断**：「格浪」为 **Golang** 谐音，指这套独立 Go 多 Agent 后端。源仓库内未检索到其他名为「格浪」的后端目录；若实际另指其他工程，需确认后替换本节范围（见 §8.1） |

### 0.2 总体判断

- **三年五倍**：与 tsp 现有 Python 技术栈同源（FastAPI + Polars + instruments.parquet + 日 K 缓存），移植难度**低**，主要是搬 service/api/page/菜单四类代码并对齐 tsp 的数据读取口径。
- **提示词管理**：前端页面与 Go prompts 包都可搬，但源仓库**当前没有做超管鉴权**（菜单直出、接口裸奔）。tsp 已有成熟的 admin 角色体系（`permissions.require_admin` + 前端 `AdminGuard`），落地时必须把接口挂到 `/admin` 后台并补后端鉴权，不能照搬源仓库的开放写法。
- **多 Agent 研判 + Go 后端**：体量最大。Go 后端约 **2.5 万行**，除 analysis 多 Agent 上下文外还带了一整套 `workbench` DDD 工作台、`hotmoney` 游资、内嵌 webui。analysis 上下文对 workbench 的耦合很弱（仅 3 个行情适配子包），**可裁剪移植**；但外部数据源工具（东财/同花顺/W6/bystock/本地 duckdb+lake）和 LLM 配置需要在 tsp 侧重新接通。tsp 已有「Python 主服务 + Go sidecar（`plugins/tdxhttp`，:8080）」的既有先例，架构上可直接复用这套模式。
  - **2026-11-17 复核修正**：经逐文件读源码，多 Agent 编排、LLM 调用、提示词管理均可在 tsp 内用纯 Python 复刻，**不强制引入 backend-go**；Go sidecar 仅为备选部署形态。详见 §5.5。真正缺口是外部数据工具（资金流/新闻/北向/盈利预测等），与语言无关。

### 0.3 推荐接入方式（贴合 tsp 二开契约）

依据 `docs/secondary-development.md`：
- 前端新页面/导航优先走 `frontend/src/custom/<namespace>/extension.tsx` 注册入口；复用现有插槽，避免改核心 Layout/路由。
- 后端新路由优先走 `backend/app/custom/<module>.py` 扩展注册；确需进核心模块时再小范围修改并附兼容验证。
- Go sidecar 作为独立进程部署，Python 主服务通过反向代理把 `/api/v1/*` 透传给它（复用源仓库 `aistock_proxy.py` 模式），不把 Go 逻辑翻译进 Python。

### 0.4 当前版本路线拍板（2026-11-17，用户决策）

> **本版本先做「旧版原样最小集成」，下一个版本再改造。** 不先做纯 Python 重写。

- 沿用已拷入 tsp 的 `backend-go/`（Go module `aistock/backend`，:8787）作为多 Agent 后端，尽量不改 Go 逻辑。
- 前端尽量按旧版页面实现：个股分析页「基本数据 / AI 分析」双 tab，AI tab 挂协同工作流图谱（多轮讨论）。
- 提示词管理放后台、按旧版方式落地（注册表 + JSON 覆盖 + 管理页），但鉴权补 tsp 超管守卫。
- §5.5 / §5.6 的纯 Python 复刻与原版参考评估**保留为下一轮改造的备选方案**，不影响本版本 sidecar 集成。
- 产品形态：入口为个股分析页 `/stock-analysis`，AI 分析 tab 内启动多 Agent SSE 流水线；历史报告下拉可回看、可导出。

---

## 1. 两侧项目现状对照

### 1.1 技术栈与运行形态

| 维度 | 源仓库 | tsp |
| --- | --- | --- |
| 主后端 | Python 3.11+ / FastAPI / Polars / DuckDB，端口 3018 | 同左，端口 `PORT=3018` |
| 第二后端 | Go 1.25.5 / Gin / cloudwego eino ADK，独立 sidecar，端口 8787 | 无 Go 主后端；仅有 `plugins/tdxhttp` Go sidecar 客户端（:8080，通达信行情） |
| 前端 | React 18 + TS + Vite + TanStack Query + Tailwind + ECharts/lightweight-charts | 同左 |
| 鉴权/角色 | Python 会话 token（内存+文件双存），**无角色/超管概念**；Go 侧 auth 为开发态占位 | 完整账号体系：`admin / pro / free` 三角色；`user_store.effective_role`；后端 `permissions.require_admin`；前端 `AdminGuard` + `/admin` 后台 |
| 配置 | Go 读 `OPENAI_API_KEY/BASE_URL/MODEL` 等环境变量；Python 读 `AISTOCK_URL` 反代 | Python 统一 AI 配置：`ai_provider / ai_base_url / ai_api_key / ai_model` |

### 1.2 tsp 已有、可直接复用的资产

- **权限后台**：`backend/app/api/permissions.py`（`is_admin` / `require_admin` / 各场景守卫）；前端 `router.tsx` 的 `AdminGuard`、`pages/AdminApp.tsx`（settings/data 两个 section）、`pages/Settings.tsx` 十余个 tab。
- **菜单体系**：`frontend/src/lib/navigation.ts`（`CORE_NAVIGATION` + 分析页 + 扩展导航）；`lib/navLayout` 支持分组/排序/隐藏偏好；后端 `services/nav_layout.py` 校验布局。
- **Go sidecar 先例**：`backend/app/plugins/tdxhttp/`（httpx 客户端 + 环境变量地址 + 插件失败不阻断主流程）。
- **行情数据底座**：`data/instruments/instruments.parquet` 维表、`data/kline_daily` 日 K 缓存、`services/kline_sync.py`（含 `_datetime_to_ms`、日 K 归一化、分片同步）。
- **AI 核心**：`services/ai_provider.py` 统一 OpenAI 兼容/Codex 工具调用；现有个股分析 `api/stock_analysis.py` + `services/stock_analyzer.py`（关键价位 + 四维流式分析，与源仓库 Python 端同源）。
- **扩展契约**：前端 `src/custom/<ns>/extension.tsx`；后端 `app/custom/<module>.py`；外部后端扩展包 `data/extensions/<id>/...`。

### 1.3 tsp 缺口（已核实不存在）

- 后端/前端全仓检索：无 `stock_growth` / `stock-growth` / `三年五倍` 任何代码。
- 无 `tradingAgents` 前端 feature、无 Go analysis 上下文、无 prompts 管理接口。
- 无 `/api/v1/*` 到 Go sidecar 的反向代理模块（`aistock_proxy.py` 模式在 tsp 尚未实现）。

---

## 2. 功能一：三年五倍

### 2.1 源实现拆解

#### 前端

| 文件 | 职责 |
| --- | --- |
| `frontend/src/pages/StockGrowth.tsx`（355 行） | 页面：搜索框、年份筛选（过去 10 年 / 不限）、排序、分页（每页 50）、扫描进度条、左侧清单表 + 右侧 K 线验证图 |
| `frontend/src/components/stock-growth/StockGrowthChart.tsx` | 谷底日/峰值日标注的 K 线图（ECharts/轻量图） |
| `frontend/src/lib/api.ts`（约 2152–2186 行） | `stockGrowthList / stockGrowthStatus / stockGrowthRefresh / stockGrowthKline` 四个请求 + `StockGrowthRow` 等类型 |
| `frontend/src/lib/queryKeys.ts`（87–91 行） | `stockGrowthStatus / stockGrowth / stockGrowthKline` 查询键 |
| 路由/菜单 | `router.tsx` 注册 `/stock-growth`；`Layout.tsx` nav 项 `{ to: '/stock-growth', label: '三年五倍', icon: Rocket }` |

#### 后端（Python）

| 文件 | 职责 |
| --- | --- |
| `backend/app/api/stock_growth.py` | 路由前缀 `/api/stock-growth`：`GET /` 清单分页、`GET /status` 扫描状态、`GET /kline` 窗口 K 线、`POST /refresh` 后台线程触发全量扫描 |
| `backend/app/services/stock_growth.py`（498 行） | 扫描与查询核心（见下） |

#### 算法与数据口径（已读源码核实）

- 常量：`WINDOW_DAYS=756`（约 3 年交易日）、`GAIN_THRESHOLD=5.0`（5 倍）、`ANALYSIS_YEARS=10`、`BATCH_SIZE=100`、数据起点 2013-01-01。
- 口径：前复权日 K；过去 N 年内，**任意连续 ≤756 根交易日**中，窗口内最低价 → 之后某日最高价，涨幅 ≥5 倍；输出谷底日/峰值日/初始价/最高价/涨幅/倍数。
- 算法：`_best_trough_to_peak` 用单调队列（滑动窗口最小值）O(n) 求每只股票最佳谷底→峰值对；`find_3y5x` 按 symbol 分组计算，结果去重、按涨幅降序。
- 数据链路：
  1. `get_all_symbols()` 读 `instruments/instruments.parquet`（缺失则走 TickFlow provider 拉标的清单）；
  2. `fetch_daily_chunks()` 经 TickFlow client `klines.batch(adjust="forward")` 分片拉前复权日 K，落缓存目录 `scripts/cache_daily_fwd/*.parquet`；
  3. `load_all_daily()` 汇总缓存；`get_name_map()` 取名称；
  4. 结果写 `data/user_data/three_year_five_times.csv`；`load_results()` 读 CSV 做年份/涨幅/关键词过滤、排序、分页；
  5. `get_symbol_kline()` 从本地前复权缓存取窗口 K 线（前后各垫 90 自然日）供右侧验证图。
- 并发/状态：模块级 `threading.Lock` + `_run_status{running,stage,pct,message}`；`/refresh` 起守护线程，409 防重复运行。

### 2.2 与 tsp 的差距

| 项 | 源仓库 | tsp 现状 | 差距/改造点 |
| --- | --- | --- | --- |
| 标的维表 | `instruments.parquet` | 已有 `data/instruments/instruments.parquet` | 基本一致，核对列名 `symbol/name` |
| 前复权日 K 缓存 | 独立目录 `cache_daily_fwd`，TickFlow `adjust=forward` 批量拉取 | 现有 `kline_daily` 缓存；需核实是否前复权、是否含 `low/high/volume` | **待确认**：复用 tsp 现有日 K 缓存，还是保留独立前复权缓存目录；复权口径必须与现有 K 线页面一致 |
| 行情客户端 | `app.tickflow.client.get_client()` + `data_providers.normalizer.normalize_daily` | tsp 有 `app/tickflow`、`data_providers` 抽象与 `kline_sync` | 按 tsp provider 契约改走现有同步/归一化链路，禁止 API 层直读本地文件（CONTRIBUTING §2.2） |
| 结果存储 | CSV（`user_data/three_year_five_times.csv`） | tsp 有 `json_report_store` / parquet 结果目录先例 | 可先沿用 CSV 最小移植；如扫描结果量大再评估 parquet |
| 前端页面/菜单 | 独立页 + 静态 nav | 导航由 `navigation.ts` + navLayout 驱动 | 在 custom 扩展注册导航项与路由；页面组件搬移后对齐 tsp 主题类名与 `PageHeader/EmptyState/Toast` 既有组件 |
| 扫描任务 | 后台线程 + 内存状态 | tsp 有 `heavy_job_limiter`、`pipeline_jobs`、任务锁先例 | 长扫描应纳入 tsp 现有重任务限流/状态机制，避免裸线程 |

### 2.3 移植清单（三年五倍）

**后端（建议 `app/custom/stock_growth/` 或并入 `services/` + `api/`）**
1. 搬 `services/stock_growth.py`：常量、滑动窗口算法、CSV 读写、分页查询保留；数据获取段改为调用 tsp 现有 provider/kline_sync。
2. 新增薄路由 `/api/stock-growth`（list/status/kline/refresh），参数校验与响应映射留在 API 层，计算留在 service。
3. 扫描状态接入 tsp 重任务限流与错误日志口径。

**前端**
4. 搬 `StockGrowth.tsx`、`StockGrowthChart.tsx`；在 `lib/api.ts` 补类型与请求函数、`queryKeys.ts` 补查询键（遵循 tsp 集中管理约定）。
5. 经 custom 扩展注册 `/stock-growth` 路由与「三年五倍」导航项；复用现有 K 线组件风格。

**验证（对照 CONTRIBUTING §1.4）**
6. 快乐路径：有缓存时清单分页/排序/搜索/右侧 K 线正常；边界：无缓存空态、扫描中 409 防重、CSV 损坏降级；失败路径：行情源不可用时的提示。

---

## 3. 功能二：个股 AI 多轮讨论（多 Agent 协同研判）

### 3.1 源实现拆解

#### 前端（`frontend/src/features/tradingAgents/`，约 3.7k 行）

| 文件 | 职责 |
| --- | --- |
| `useMultiAgentAnalysis.ts` | 分析编排 hook：启动/停止/切股同步；AbortController 中止；把 SSE 事件分发到 store |
| `analysisStore.ts`（544 行） | 全局分析状态：任务态、当前标的、各 Agent 状态/快照/报告/流式分块、风险项/关键指标/目标价止损价、最终报告 |
| `types.ts`（475 行） | 事件/报告/快照/令牌等类型契约 |
| `agentMeta.ts` | 15 个节点的 UI 元数据（名称/标签/目标/报告 section/辩论分组/图标/徽标色/文件 slug） |
| `AiAnalysisPanel.tsx` | 面板：开始/停止/一键下载、协同图谱入口、决策卡、风险雷达、关键指标、Agent 报告抽屉 |
| `components/AgentCollaboration.tsx`（415 行） | 截图中的协同工作流图谱（节点分组、连线、状态徽标、点击选 Agent） |
| `components/DecisionCard / RiskRadar / KeyMetrics / ReportAgentSheet` | 决策卡、风险雷达、指标、单 Agent 报告抽屉 |
| `AnalysisHistoryDropdown.tsx` | 历史报告下拉（按标的恢复） |
| `resolveAgentReport.ts / reportText.ts / downloadReports.ts` | 报告文本归并、价格/决策解析、逐 Agent Markdown + ZIP 导出 |
| `lib/aistockApi.ts` | `streamTradingAnalysis`：POST `/api/v1/analysis/chat/completions`，SSE 读取解析；`listAgentPrompts / updateAgentPrompt`；服务地址 `VITE_AISTOCK_API_URL`、Bearer token 取自 env/localStorage |

页面挂载：`pages/StockAnalysis.tsx` 为独立个股分析页，「基本数据 / AI 分析」两个 tab，AI tab 挂 `AiAnalysisPanel`；页头挂历史下拉。

#### Go 后端（`backend-go/internal/analysis/`）

**工作流（`graph/trading_graph.go`，基于 cloudwego eino ADK）**

```
1. 6 分析师并行（ParallelAgent）
   技术面 market / 舆情 sentiment / 新闻 news / 基本面 fundamentals / 宏观 macro / 主力资金 smart_money
2. 博弈裁判 Game Theory Manager
3. 多空辩论（LoopAgent，MaxIterations=3 轮）   ← 「多轮讨论」核心
   多头 Bull Researcher ⇄ 空头 Bear Researcher
4. 研究经理 Research Manager（收敛多空论据 → 投资计划）
5. 交易员 Trader（研究结论 → 可执行指令）
6. 三方风控并行（ParallelAgent）：激进 / 中性 / 稳健
7. 风险法官 Risk Judge
8. 风险经理 Risk Manager（最终裁决）
```

**目录结构**

| 包 | 内容 |
| --- | --- |
| `agents/analysts/{market,sentiment,news,fundamentals,macro,smartmoney}` | 6 个分析师，各含 `agent.go` + `prompt.go` |
| `agents/researchers/{bull,bear}` | 多空研究员（辩论 Loop 参与者） |
| `agents/managers/{gametheory,research,risk}` | 博弈裁判 / 研究经理 / 风险经理 |
| `agents/riskmgmt/{aggressive,neutral,conservative,judge}` | 三方风控 + 风险法官 |
| `agents/trader` | 交易员 |
| `agents/common` | Agent Builder、模型装配、工具错误中间件 |
| `agents/registry.go` | Agent 注册汇总 |
| `prompts/` | 提示词默认值注册 + 覆盖持久化（见 §4） |
| `tools/` | Agent 可调用工具：`stock_tools / eastmoney_tools / ths_tools / w6_tools`、schema 归一化；`data/` 下有 duckdb、lake parquet、checkpoint/manifest |
| `types/state.go` | 工作流共享状态 |
| `interfaces/http/` | Gin handler、任务管理、SSE、报告 SQLite 存储、提示词 handler、类型契约、运行入口 |

**HTTP 契约（`/api/v1/analysis`，`server.go` 注册）**

| 方法 & 路径 | 用途 |
| --- | --- |
| `POST /chat/completions` | 主入口：body `{messages:[{role,content}], stream:true, selected_analysts?}`；SSE 推送 `job.ready / job.created / job.running / agent.status / agent.token / agent.snapshot / agent.report / agent.report.chunk / job.completed / job.failed`，结束 `[DONE]`；内含 15s keepalive 防空闲断连 |
| `POST /analyze` | 任务式启动分析（返回 job_id） |
| `GET /jobs/:id`、`GET /jobs/:id/result`、`GET /jobs/:id/events` | 任务状态/结果/SSE 事件重放 |
| `GET /reports`、`POST /reports`、`GET /reports/:id`、`DELETE /reports/:id` | 历史报告 CRUD |
| `GET /market/kline`、`GET /market/minute`、`GET /market/stock-search` | 面板内行情补充（K 线可注入 Python 行情仓储，缺失时降级模拟/TDX） |
| `GET/PATCH /config`、`/auth/*`、`/watchlist`、`/scheduled`、`/tokens`、`/announcements/latest` | 源仓库附带的管理/占位能力，**移植时多数可裁剪** |

**报告持久化（`report_store.go`，SQLite，默认 `data/aigostock.db`，env `AIGOSTOCK_SQLITE_PATH`）**

`reports` 表字段：`id, user_id, symbol, trade_date, status, error, decision, direction, confidence, target_price, stop_loss_price, result_data, risk_items, key_metrics, analyst_traces` + 8 个分 Agent 报告文本字段（market/sentiment/news/fundamentals/macro/smart_money/game_theory/investment_plan/trader_investment_plan/final_trade_decision）+ 时间戳；索引 symbol/status/created_at。方法：`UpsertCompleted / UpsertFailed / Delete / Count / List / Get / InsertManual`。

**外部依赖（go.mod 已核实）**

`cloudwego/eino` ADK 与 openai ext、`easyspace-ai/bystock`、`easyspace-ai/w6sdk`、Gin、gorilla/websocket、gorm + sqlite3、jwt、resty、fsnotify 等；tools 层对接东财/同花顺/W6 联网预取与本地 duckdb/lake 数据。

### 3.2 与 tsp 的差距

| 项 | 源仓库 | tsp 现状 | 差距/改造点 |
| --- | --- | --- | --- |
| 多 Agent 工作流 | Go eino ADK 8 阶段图 | 无；Python 侧仅有四维单轮流式分析 | 不建议翻译为 Python；按 sidecar 模式引入 Go 服务 |
| 前端协同图谱/面板 | `features/tradingAgents` 整套 | 无；现有个股分析为 `stock-analysis` 对话框（Dialog Host 挂 Layout） | 两种落地选择：① 仿照源仓库新增独立个股分析页 + AI tab；② 在现有股票预览插槽 `stock-preview.tabs` 内嵌面板。**待确认**产品形态 |
| SSE 通道 | Go 直出 SSE；生产经 Python 反代同源访问 | tsp 主服务直出 SSE 有 `ndjson_heartbeat` 先例；无 `/api/v1` 反代 | 移植 `aistock_proxy.py` 式反代（流式透传 + 长超时 + 心跳），或复用 tdxhttp 客户端风格按需取数；图谱实时性要求高，**优先反代透传 SSE** |
| 行情供给 | Go `klinefetch/klinecompat/ports` 注入 `StockRepository`；可回退 TDX/模拟 | tsp 有完整行情仓储与 tdxhttp sidecar | sidecar 启动时注入 tsp 行情仓储的 HTTP 地址；缺失数据源时按源仓库降级策略处理 |
| 报告存储 | Go 自带 SQLite reports 表 | tsp 有 `stock_reports` / `json_report_store` Python 报告体系 | **待确认**：历史报告统一进 tsp Python 报告库，还是保留 Go SQLite；统一存储更利于现有历史面板复用 |
| LLM 配置 | Go 环境变量 `OPENAI_*` | tsp 统一 `ai_provider/ai_base_url/ai_api_key/ai_model` | sidecar 启动时从 tsp 配置/环境变量注入同一套 OpenAI 兼容密钥与模型名 |
| 鉴权 | Go 侧 auth 为开发占位；前端带 Bearer | tsp 会话 + 角色体系 | sidecar 内网监听；经 Python 反代时由主服务完成会话校验，不把 Go 开发态登录暴露到公网 |

### 3.3 移植清单（多 Agent 研判）

**Go sidecar（裁剪版 `backend-go` → tsp 部署目录，如 `sidecar/analysis/`）**
1. 保留 `internal/analysis/` 全量：agents / graph / prompts / tools / types / interfaces/http（裁掉 watchlist/scheduled/tokens/announcements 等占位路由）。
2. 保留 analysis 直接依赖的 `internal/workbench/{ports,klinefetch,klinecompat}` 与 `internal/appenv`；**裁剪** workbench 其余大块（auth/domain/database/filesystem/proxy/ws/services）、`hotmoney`、`webui` 内嵌前端（耦合度已核实：analysis 仅引用上述 3 个 workbench 子包）。
3. 保留 `cmd/server` 启动装配；LLM、数据目录、行情上游、SQLite 路径全部走环境变量，对齐 tsp `.env.example`。
4. tools 数据源按 tsp 实际可用源接通：东财/HTTP 工具可保留；W6/bystock/duckdb/lake 若 tsp 无对应数据，先做降级开关（工具缺失不阻断工作流）。

**Python 主服务**
5. 新增 sidecar 反代模块：`/api/v1/*` → `AISTOCK_URL`（默认 `127.0.0.1:8787`），SSE 流式透传、长读超时、心跳透传；sidecar 未启动时 502/503 友好降级，不阻断主流程（插件化原则）。
6. 会话校验在反代前完成；open-platform 网关不默认开放该 SSE 通道（与 `api_gateway.py` 现有策略一致）。

**前端**
7. 搬 `features/tradingAgents` 整套（store/hook/组件/类型/导出工具）；API 层适配 tsp `lib/api.ts`/queryKeys 约定。
8. 面板挂载形态按 §3.2 产品决策落地；历史报告对接选定的报告存储。

**验证**
9. 单机起 sidecar + 主服务：选股 → 图谱节点状态按序推进 → 各 Agent 报告可读 → 最终决策/目标价/止损价落库 → 历史可恢复；中止/切股/sidecar 不可用/LLM 缺 Key 四条失败路径有明确提示。

---

## 4. 功能三：提示词管理（后台 · 超级管理员）

### 4.1 源实现拆解

**Go prompts 包（`internal/analysis/prompts/store.go`）**

- 每个 Agent 在自身 `prompt.go` 里 `prompts.Register(id, name, label, defaultPrompt)` 注册编译期默认值。
- `Resolve(id, fallback)`：有覆盖值用覆盖值，否则用默认值；Agent 构建时调用（`agents/*/agent.go`），因此**保存覆盖后下一次构建 Agent 即生效，无需重启**。
- 覆盖持久化：`<data_dir>/prompts_override.json`（`{id: prompt}`）；启动 `Init(dataDir)` 读取；写盘走临时文件 + rename 原子替换。
- 内存态：`registry` 元信息、`defaults`、`overrides`，读写锁保护。

**Go HTTP（`interfaces/http/prompts_handler.go`，挂在 `/api/v1/analysis/prompts`）**

| 方法 & 路径 | 行为 |
| --- | --- |
| `GET /prompts` | 列出全部 Agent：`{id, name, label, prompt, overridden}` |
| `GET /prompts/:id` | 单条提示词 |
| `PUT /prompts/:id` | body `{prompt}` 非空校验；`Set` 持久化覆盖，返回更新后条目；未知 id 404 |

**前端 `pages/PromptAdmin.tsx`（176 行）**

左侧 Agent 列表（含「已改」徽标）+ 右侧 textarea 草稿；脏检查、切换未保存确认、保存 mutation 后同步 query 缓存；API 走 `aistockApi.listAgentPrompts / updateAgentPrompt`。

### 4.2 源仓库的权限缺口（必须在 tsp 补上）

- 前端：`router.tsx` 直接注册 `/prompt-admin`，`Layout.tsx` nav 静态直出，**无 admin 判断**。
- 后端：prompts 三个接口**无任何鉴权中间件**。
- 结论：源仓库这套只能视为「功能原型」；移植到 tsp 时不能照搬开放程度。

### 4.3 tsp 落地设计（后台 + 超管）

**后端**
1. prompts 数据留在 Go sidecar（与 Agent 默认值同包，热加载语义不变）；Python 主服务反代 `/api/v1/analysis/prompts*`。
2. 在反代路由上挂 tsp 既有守卫：写接口（`PUT`）走 `permissions.require_admin(request)`；列表/读取同样仅 admin 可见（提示词属系统配置，不向下发给普通用户）。对照 `permissions.py` 现有 `guard_settings_access / guard_ext_pages_access` 模式新增一个 `guard_prompts_access`。
3. 不新增 open-platform token scope；该接口只服务 Web 会话后台。

**前端**
4. 页面**不进前台 `CORE_NAVIGATION`**；挂入 `/admin` 后台：建议作为 `AdminApp` 的新 section，或 `Settings.tsx` 现有 tab 体系内新增「提示词管理」tab（`TABS` 数组追加一项，面板懒加载）。
5. 路由经 `AdminGuard` 守卫（未登录→登录页，非 admin→回看板），与现有 `/admin`、`/dev` 隐藏路由一致。
6. 搬 `PromptAdmin.tsx` 列表 + 草稿 + 脏检查交互；API 走主服务同源路径，复用 tsp 错误提示与 toast。

**验证**
7. admin 可见可改、保存即时生效于下次分析；pro/free 角色菜单不可见、直接访问 URL 被守卫回退、直调 API 返回 403；覆盖 JSON 文件损坏/缺失时回退默认提示词且服务可启动。

---

## 5. Golang 后端整体移植评估（「格浪」）

### 5.1 体量与边界

- Go 代码总量约 **25,117 行**（`internal` + `cmd`）。
- 与本次三块功能强相关的是 `internal/analysis/`；其余 `workbench`（完整 DDD 工作台：application/auth/database/domain/filesystem/infrastructure/ports/proxy/services/tags/utils/ws）、`hotmoney`（游资/UZI）、`webui`（内嵌前端静态资源）属于源仓库另一套工作台，**不随本次功能必须迁移**。
- 耦合核实：`analysis` 包跨包引用仅 `workbench/ports`、`workbench/klinefetch`、`workbench/klinecompat`、`appenv`、`webui`（webui 可在裁剪时去掉静态挂载）。

### 5.2 建议裁剪范围

| 保留 | 裁剪/可选 |
| --- | --- |
| `analysis/agents` 全量、`analysis/graph`、`analysis/prompts`、`analysis/types` | `workbench` 中除 ports/klinefetch/klinecompat 外的全部 |
| `analysis/tools`（stock/eastmoney/ths；w6/bystock 按可用性留降级） | `hotmoney/` 游资与 UZI 集成 |
| `analysis/interfaces/http`（handler、job_manager、sse、report_store、prompts_handler、types、report_resolve） | `webui/` 内嵌前端、watchlist/scheduled/tokens/announcements 占位 API |
| `cmd/server` 启动入口、`appenv` 数据目录解析 | 源仓库独立内嵌前端的静态托管路由 |

### 5.3 部署形态建议（复用 tsp 既有 sidecar 模式）

```
浏览器
  │  同源 /api/*（Python FastAPI :3018，会话校验 + 角色守卫）
  ├─ /api/stock-growth/* ──────────────→ Python service（三年五倍）
  ├─ /api/v1/analysis/* ── 反向代理 ──→ Go sidecar :8787（多 Agent + prompts + reports SQLite）
  │                                       ├─ LLM：读 tsp 注入的 OpenAI 兼容配置
  │                                       ├─ 行情：回源 tsp 行情仓储 / tdxhttp sidecar
  │                                       └─ 数据：analysis 自身 data 目录（prompts 覆盖、reports.db、tools 缓存）
  └─ 其余既有 API ─────────────────────→ Python 主服务
```

- 进程管理对齐 tsp 现有部署方式（`dev.sh` / docker-compose / PM2 之一，待确认 tsp 生产实际用哪种）。
- 配置项纳入 `.env.example`：sidecar 地址、端口、LLM 密钥/模型、数据目录、SQLite 路径、分析超时、代理读超时。

### 5.4 数据与外部服务核对清单（落地前逐项确认）

| 依赖 | 源仓库位置 | tsp 对应情况 | 动作 |
| --- | --- | --- | --- |
| 行情日 K / 分钟 | Go `klinefetch` + ports；TDX 服务 | `kline_daily` 缓存、tdxhttp sidecar | 定义 sidecar → tsp 行情 HTTP 契约，补缺失字段 |
| 东财/同花顺工具 | `eastmoney_tools.go / ths_tools.go` | 待核实 tsp 是否已有同类 HTTP 工具 | 能复用则复用；不可用则工具降级 |
| W6 新闻预取 | `w6_tools.go` + w6sdk，env `AI_SDK_*` | 未见对应配置 | 无 W6 凭据时新闻分析师降级，不阻断流程 |
| bystock | go.mod `easyspace-ai/bystock` | 待核实 | 确认其用途（行情/搜索）后决定保留或替换 |
| duckdb / lake parquet | `analysis/tools/data/duckdb`、`data/lake` | tsp 数据目录无同名结构 | 迁移现有缓存或改为空启动重建；不提交本地样本数据到 Git |
| 报告 SQLite | `data/aigostock.db` | tsp Python 报告库 | 二选一：Go 自带库 / 统一进 tsp 报告库（见 §3.2） |

### 5.5 纯 Python 复刻可行性（不引入 backend-go）— 2026-11-17 源码复核

> 本节是对 §3.2 / §5.3「Go sidecar 接入」路线的复核与修正：经逐文件读源码，**两块功能都可用 tsp 现有 Python 技术栈复刻，不需要移植或并行运行 backend-go**；Go sidecar 降级为备选部署形态，不是必需项。

**结论：可以纯 Python 实现。** Go 在这套实现里只承担三类可被 Python 等价替代的机制；真正的工作量在外部数据工具补齐，与所用语言无关。

**Go 机制 → tsp Python 等价物**

| Go 侧机制 | 源码核实 | tsp 等价物 |
| --- | --- | --- |
| ADK 工作流编排（Parallel / Loop / Sequential） | `graph/trading_graph.go`：6 分析师并行、bull/bear `LoopAgent` 3 轮、8 阶段顺序串联 | `asyncio.gather` 做并行；辩论用 `for` 循环（≤3 轮）；顺序阶段直接函数串联，无需引入 ADK 框架 |
| LLM 调用（工具型聊天模型） | `agents/common/model.go`：OpenAI 兼容 client（`OPENAI_API_KEY/BASE_URL/MODEL`）；分析师 QuickThink、决策 DeepThink 两档 | `services/ai_provider.py`：`stream_ai_text` 流式、`generate_ai_text` 一次性、`generate_ai_text_with_tools` 工具循环；配置复用 `ai_provider/ai_base_url/ai_api_key/ai_model` |
| 智能体构造 | `agents/common/builder.go`：`name + instruction(prompts.Resolve) + model + tools`；研究员/经理/风控**不绑工具**，仅基于上游文本辩论 | 轻量 Agent 结构：角色元信息 + 系统提示词（覆盖层解析）+ 模型档位 + 可选工具表；辩论节点为纯文本推理 |
| 提示词注册表 + JSON 覆盖 | `prompts/store.go`：`Register/Resolve/List/Get/Set`，`prompts_override.json` 临时文件 + rename 原子写 | Python service：注册表 + 内存默认/覆盖 + 全局 JSON 覆盖文件（对齐三年五倍 CSV 的全局落盘约定，不按用户分区） |
| 报告持久化 | `report_store.go` SQLite `reports` 表 | 复用 `stock_reports` / `json_report_store` |
| 流式事件通道 | `interfaces/http/sse.go` + `analysis_run.go`：`agent.status / agent.token / progress / job.*` | 复用 `ndjson_heartbeat` + `StreamingResponse` 既有通道；前端事件协议对齐 Agent 卡片 |

**数据工具对照（Go tools → tsp 现状）**

已可由 tsp 本地数据承接：

- `get_stock_data` 日 K、`get_trade_calendar`、`get_stock_basic`、`get_daily_basic`（部分）→ `repo.get_daily / get_daily_asset`、工具表、财务视图。
- 基本面 → 财务五表 DuckDB 视图 + `financial_analyzer`。
- 概念/行业板块 → `sector_rotation / concept_rotation_analyzer / sector_monitor`。
- 龙虎榜 → `dragon_tiger` 服务（fuyao 源）。

缺口（tsp 无现成 Python 取数服务，需新写 HTTP 工具或在该角色降级）：

| 缺口工具 | Go 位置 | 说明 |
| --- | --- | --- |
| 资金流 `get_fund_flow / get_fund_flow_120d`、北向 `get_northbound_flow` | eastmoney_tools / ths_tools | 主力资金分析师输入 |
| 个股/全局新闻 `get_news / get_global_news`、W6 联网调研预取 | eastmoney_tools / w6_tools | tsp `news_search` 尚未落地；W6 需独立凭据 |
| 股东户数 `get_holder_count`、分红 `get_dividend`、解禁 `get_lockup_expiry`、两融 `get_margin_trading`、大宗 `get_block_trade` | eastmoney_tools | 基本面/风控补充维度 |
| 热门股 `get_hot_stocks`、盈利预测 `get_profit_forecast`、行业对比 `get_industry_comparison` | ths_tools / eastmoney_tools | 市场/宏观分析师输入 |
| 实时快照 `daily_basic` 完整字段 | stock_tools | 落地前核实 `quote_service` 覆盖度 |

**关键判断**

- Go 行情工具**首选回源 Python 面板** `/api/kline/daily`（`stock_tools.go` `panelAPIURL`，默认 7300，env `PANEL_API_URL`），失败才降级腾讯/新浪/本地 tsdb。即 Go 本就是外挂 sidecar，主数据本来就来自 Python 面板；去 Go 后 Python 直读 repo，链路更短、少一层 HTTP。
- 16 份角色提示词共约 467 行纯文本，可直接搬运到 Python 注册表。
- 提示词管理整块是「注册表 + JSON 覆盖 + CRUD」，Python 复刻量很小；鉴权用 `permissions.require_admin`（密码全局模式默认管理员，邮箱模式仅 admin），比旧版接口裸奔更规范。
- 旧版「多轮讨论」实为**智能体之间的辩论轮次**（bull/bear ≤3 轮）；用户侧 `messages` 历史仅用于提取标的，每次重跑完整流程，**并不保留长上下文**。若产品要真正的用户多轮追问，需另设会话历史存储与回灌（旧版未实现，见 §8.2）。

**纯 Python 分期（替代原阶段 C 的 Go sidecar 路线）**

- **P1 提示词管理后台**：注册表 + JSON 覆盖 + admin 守卫 + 前端管理页；不依赖新数据，可先上。
- **P2 多智能体骨架**：Python 顺序链路 + 多空辩论循环；分析师先用 tsp 现有本地数据（K 线/财务/价位/板块/龙虎榜）；事件通道对齐前端卡片。
- **P3 数据工具补齐**：按缺口清单逐个在 Python provider/工具层实现（直连东财/腾讯/同花顺，与 Go tools 同源），接 `generate_ai_text_with_tools`；缺数据的分析师走保守降级，不阻断流程。
- **P4 报告持久化与前端集成**：复用 `stock_reports`；搬入 `features/tradingAgents` 整套并适配 tsp API/store 约定。

**风险与前置确认**

- 外部数据源凭据/可用性（东财/同花顺/fuyao/W6）在 tsp 环境哪些可用；不可用的角色先降级。
- 产品形态：独立个股分析页 vs 嵌入现有股票预览 tab。
- 是否需要用户侧长上下文多轮对话（旧版没有）。
- 缺口工具的取数频次与缓存口径，对齐 tsp 既有缓存/插件化约定。

### 5.6 原始 Python 项目 TradingAgents-AShare 参考评估（2026-11-17 复核）

> 路径 `<upstream>/TradingAgents-AShare`（v0.2.0，Python ≥3.10）。这是 Tauric TradingAgents 开源框架的 AShare 改造版，也是 `backend-go` 的**上游 Python 原版**：Go 的 API、报告 schema、前端组件均源自本项目。

**结论：以原版 Python 作为多智能体逻辑的主参考；Go 版作为 API/事件契约与提示词覆盖层的对照。** 不直接照搬原版代码，需在编排、LLM、数据、周边四处做适配替换。

**血缘核实（三处可追溯）**

- 报告表：Go `report_store.go` 字段与本项目 `api/database.py` `ReportDB` 一一对应（id/symbol/trade_date/status/decision/direction/confidence/target_price/stop_loss/result_data/risk_items/key_metrics/analyst_traces + 各分报告文本列）。
- 前端：本项目 `components/AgentCollaboration.tsx / DebateDrawer.tsx / DebateTimeline.tsx`、`stores/analysisStore.ts`、`pages/Analysis.tsx` 即 Go 版 `features/tradingAgents/*` 的源头。
- 提示词：本项目 `prompts/zh.py`（555 行）是 Go 各 `prompt.go` 默认值的原始文本。

**原版技术栈与结构（已核实）**

- 编排：**LangGraph**（`langgraph` + `langgraph-checkpoint-sqlite`），`graph/setup.py` 建 `StateGraph`：START 并行选中分析师（analyst + ToolNode + Done）→ Bull/Bear 辩论条件循环 → Research Manager → Trader → 三方风控并行 → Risk Judge；状态 `AgentState / InvestDebateState / RiskDebateState`（辩论历史、claims、轮次、裁决）。
- LLM：`llm_clients/` 工厂按 provider 选 langchain `ChatOpenAI / Anthropic / Google`；deep/quick 两档；`llm.astream/invoke`。
- 数据：`dataflows/providers/` 注册表（akshare / baostock / investoday / yfinance / alpha_vantage），按类别（行情/指标/基本面/新闻/实时/A股资金面）配 vendor；`graph/data_collector.py` 预取工具结果到缓存池，分析师从池取数，避免重复拉取。
- Agents：7 分析师（fundamentals/macro/market/news/smart_money/social_media/volume_price）+ bull/bear + research/risk manager + 三方风控 + trader；工具在 `agents/utils/`。
- API：FastAPI `api/main.py`（4775 行）：`/v1/analyze`、`/v1/jobs/:id(/result|/events)`、`/v1/chat/completions`、`/v1/reports CRUD`、`/v1/market/*`，外加 auth JWT、tokens、config、watchlist、定时任务、回测、持仓导入、跟踪看板、邮件/企微通知、VLM 持仓解析、反馈/赞助等周边。
- 提示词：`prompts/zh.py` 一个 `PROMPTS` dict，`get_prompt(key)` 读取；**原版无提示词管理后台/覆盖层**（覆盖是 Go 版后加的）。

**与 TSP 对照：可直接借鉴 / 必须替换**

| 层 | 原版做法 | TSP 现状 | 移植动作 |
| --- | --- | --- | --- |
| Agent 节点 | `create_xxx(llm,...)` 返回 async node，输入/输出普通 dict；节点逻辑、claims/辩论状态机、意图解析可读 | 无多智能体 | 节点结构、辩论状态机、分析师报告字段可搬；LLM 调用换成 TSP `ai_provider` |
| 编排 | LangGraph StateGraph + 条件边 + sqlite 检查点 | 未装 langgraph/langchain；现有后台线程 + NDJSON 通道 | 起步用 `asyncio.gather` + `for` 辩论循环轻量复刻；确需图拓扑/断点续跑再评估引入 langgraph |
| LLM | langchain ChatOpenAI 等，多 provider | `ai_provider.py` 自封装：`stream_ai_text/generate_ai_text/generate_ai_text_with_tools`，多 provider 多协议 | 沿用 TSP ai_provider；不引 langchain；messages 对齐 TSP Message |
| 数据 | akshare/baostock/yfinance/stockstats/investoday 直连 | 本地 parquet/DuckDB + 插件 provider（tickflow/fuyao/market_data）；无上述库 | provider 抽象接口可借鉴；工具实现改接 TSP repo/插件；指标用 TSP indicators；外部缺口同 §5.5 清单 |
| 提示词 | `zh.py` dict + `get_prompt` | 无 | 默认值从 zh.py 搬入注册表；覆盖层/admin 后台按 Go `prompts/store.go` 机制在 Python 实现 |
| 报告存储 | SQLAlchemy `ReportDB` | `stock_reports` / `json_report_store` | 复用 TSP 报告库；字段映射原版/Go 报告表 |
| 周边 | 自选/定时/持仓看板/回测/通知/VLM/多用户 JWT | 多数 TSP 已有独立模块或本次不在范围 | 本次三块功能先裁掉，不搬 |

**分析师命名映射（原版 7 → Go 6 → TSP 落地）**

| 原版 Python | Go | TSP 数据输入 |
| --- | --- | --- |
| market | market | 日 K + 技术指标/关键价位 |
| social_media | sentiment | 舆情（缺口，需降级或接源） |
| news | news | 个股/全局新闻（缺口） |
| fundamentals | fundamentals | 财务五表 + 报表 |
| macro | macro | 宏观/行业对比（部分缺口） |
| smart_money | smartmoney | 资金流/北向/龙虎榜（部分缺口） |
| volume_price | （并入技术面/市场） | 量价指标，可并入 market |

**参考分工建议**

| 内容 | 主参考 |
| --- | --- |
| Agent 节点逻辑、辩论状态机/claims、数据预取装配、意图解析、中文提示词原文 | 原版 Python |
| 8 阶段节点清单、SSE/NDJSON 事件协议、报告字段、前端卡片/抽屉/时间线组件结构 | Go 版（与原版同源，对照差异） |
| 提示词注册表 + JSON 覆盖 + admin 后台机制 | Go 版（原版无此层） |
| LLM 调用、数据仓储、权限、报告库、前端插槽与查询约定 | TSP 现有 |

**风险**

- LangGraph 若引入：需评估与 TSP 异步任务/NDJSON 通道、插件化失败不阻断约定的兼容；不引则编排自写，图能力（可视化拓扑/断点续跑）需自行实现。
- 原版数据直连外部公开源，稳定性/字段口径与 TSP 本地库不一致；工具适配层要做字段归一化与降级。
- 原版为单进程产品，auth/多用户/定时等与 TSP 角色体系不同，不复用其鉴权。

---

## 6. 推荐分阶段计划（长期改造路线，备选）

> 本版本执行清单以 §7 为准（sidecar 先行）；本节为下一轮纯 Python 改造的分期参考。每阶段都以可运行、可验证为出口；遵循「最小改动、先理解调用链、测试覆盖快乐路径/边界/失败路径」。

### 阶段 A：三年五倍（纯 Python/前端，已交付并验证）
1. 后端 service + 薄路由落地，对齐 tsp provider/缓存口径；补扫描任务状态与限流。
2. 前端页面、图表、API 类型与查询键、导航注册。
3. 验证：缓存命中清单/分页/排序/搜索/K 线；空态与重扫防重；行情源失败降级。

### 阶段 B：提示词管理后台（纯 Python，独立、不依赖新数据，优先做）
1. 后端 prompts service：注册表 + 内存默认/覆盖 + 全局 JSON 覆盖文件（原子写）；默认值从原版 `zh.py` 搬入；守卫用 `permissions.require_admin`。
2. 前端后台管理页：列表/草稿/保存交互；接口契约对齐 Go `/prompts`（List/Get/PUT）。
3. 验证 admin/pro/free 权限矩阵；覆盖热加载生效；缺数据角色不受影响。

### 阶段 C：多智能体骨架（纯 Python 复刻，不引 Go sidecar、起步不引 LangGraph）
1. 编排：`asyncio.gather` 并行分析师 + `for` 多空辩论循环 + 顺序阶段串联；节点为普通 async 函数、state 为 dict（结构参考原版 `agents/utils/agent_states.py`）。
2. LLM 走 `ai_provider`（`stream_ai_text/generate_ai_text/generate_ai_text_with_tools`）；事件复用 NDJSON + `StreamingResponse`。
3. 分析师先用 tsp 现有本地数据（日 K/财务五表/关键价位/板块/龙虎榜）；缺数据角色保守降级、不阻断流程。
4. 验证：完整 8 阶段跑通；辩论轮次收敛；三/四类失败路径就绪。

### 阶段 D：多 Agent 前端集成
1. 搬 `features/tradingAgents` 整套并适配 tsp API/store 约定（组件源头为原版 `components/*`）。
2. 确定面板挂载形态（独立页 tab 或股票预览插槽）；接历史报告与导出。
3. 端到端验证：事件流、辩论抽屉/时间线、裁决卡片与历史回看。

### 阶段 E：数据补齐、报告落库与上线核对
1. 缺口外部工具（资金流/北向/新闻/股东户数/分红/解禁/两融/大宗/热门股/盈利预测/行业对比）逐项在 Python provider/工具层接通或降级；缓存口径对齐 tsp 约定。
2. 报告统一进 tsp `stock_reports`/`json_report_store`，字段映射原版/Go 报告表；不另起 SQLite。
3. `.env.example`、部署脚本、文档与测试补齐；压测与失败路径回归。

---

## 7. 本版本最小集成清单（sidecar 先行，2026-11-17）

> 目标：不改 Go 业务逻辑、不做 Python 重写，把旧版多 Agent 研判 + 提示词后台原样接进 tsp；跑通完整 8 阶段 SSE 流水线、历史回看、提示词覆盖即可。下一轮再按 §5.5 / §5.6 改造。

### 7.1 已就位 / 待搬运资产

**已在 tsp（本轮核对）**

- `backend-go/` 已整目录拷入 tsp 根：module `aistock/backend`，入口 `cmd/server/main.go`，149 个 Go 文件；自带 `data/`（`aigostock.db` 历史报告、`auth.db`、`stock.db`、`lake/`、`duckdb/`、`stockapi-cache/`）；PM2 `ecosystem.config.cjs`；`.env`。

**从源仓库 `<source-repo>/` 搬运**

| 层 | 源文件 | 落到 tsp |
| --- | --- | --- |
| Python 反代 | `backend/app/api/aistock_proxy.py`（154 行，`/api/v1` 全方法透传 + SSE 流式 + 长读超时） | `backend/app/api/aistock_proxy.py` |
| Python 配置 | `config.py`：`aistock_url`（默认 `http://127.0.0.1:8787`）、`aistock_proxy_read_timeout`（默认 2700s） | 同名字段加到 tsp `Settings` |
| Python 注册 | `main.py` `include_router(aistock_proxy.router)` | 同位置注册 |
| 前端 API | `frontend/src/lib/aistockApi.ts` | `frontend/src/lib/` |
| 前端多 Agent | `frontend/src/features/tradingAgents/`（agentMeta / AiAnalysisPanel / AnalysisHistoryDropdown / analysisStore / downloadReports / reportText / resolveAgentReport / types / useMultiAgentAnalysis + `components/{AgentCollaboration,DecisionCard,KeyMetrics,ReportAgentSheet,RiskRadar}.tsx`） | `frontend/src/features/tradingAgents/` |
| 前端个股壳 | `frontend/src/components/stock-analysis/`（AnalysisKChart / StockAnalysisBubble / StockAnalysisDialog / StockAnalysisHost） | **复用 TSP 现有自研对齐版**（Host 与旧版相同；Bubble/Dialog/KChart 已按 TSP 链路适配），本轮不覆盖 |
| 前端页面 | `frontend/src/pages/StockAnalysis.tsx`、`frontend/src/pages/PromptAdmin.tsx` | `frontend/src/pages/` |
| 前端路由 | `/stock-analysis`（前台 Layout children）；提示词并入后台 `/admin/prompts`（AdminGuard，AdminApp 第三 section）；旧 `/settings/agent-prompts` 重定向不搬（TSP 后台已统一为 `/admin` 体系） | `router.tsx` |
| 前端导航 | 前台 `lib/navigation.ts` 加「个股分析」；「提示词管理」进后台 AdminApp 段导航，不进前台侧边栏 | `lib/navigation.ts` / `pages/AdminApp.tsx` |

### 7.2 配置对齐与联调项

1. **编译（已通过，2026-11-17）**：磁盘清理后可用空间 9.1Gi；`cd backend-go && GOTOOLCHAIN=auto go build -o bin/aistock-server ./cmd/server` 成功，产物 `bin/aistock-server`（约 114MB）。临时覆盖 `AI_DATA_DIR/PANEL_API_URL/PORT` 启动，`:8787` 监听、`GET /healthz` 200，analysis/picker/market 路由与 prompts 三接口全部注册。go.mod 要求 1.25.5，本机 1.25.1，工具链由 `GOTOOLCHAIN=auto` 自动拉取。
2. **数据目录 `AI_DATA_DIR`（已固化）**：`.env` 已改为绝对路径 `<repo>/backend-go/data`；启动日志 `root=.../backend-go/data sidecar=true`，无 Fatal。注意：Go 自带 `data/` 很小（stock.db 124K、lake 276K、duckdb 12K、stockapi-cache 空），行情主数据不在其中，依赖面板回源或外部降级链。
3. **面板回源 `PANEL_API_URL` + 鉴权（方案 A 已落地，2026-11-17）**：`.env` 已配 `PANEL_API_URL=http://127.0.0.1:3018`。tsp 已初始化，`/api/kline/daily` 走鉴权，Go 不带凭证会 401；该接口在 Token 开放清单（scope `read:market`）。落地方式（方案 A）：
   - tsp 新建专用 Token `tok_59e878b5`（name `aistock-sidecar`，scope `read:market`；明文仅存于 `backend-go/.env` 的 `PANEL_API_TOKEN`，`.env` 已被 .gitignore 忽略）。
   - Go `stock_tools.go` 新增 `panelAPIToken()`，`fetchKlinePanel` 非空时带 `Authorization: Bearer`（空则不带，向后兼容）。
   - 已验证：该 Token 带 Bearer 调 tsp `/api/kline/daily` 返回日 K，rows 含 `date/open/high/low/close/volume`，字段与 Go 解析对齐。
   - Go 回源面板仅此一个接口；其余工具直连东财/新浪/腾讯/W6，不经 tsp 面板。
4. **Go 侧已验证（2026-11-17）**：`go build` 成功产出 `bin/aistock-server`（约 114MB）；`:8787` 监听、`/healthz` 200；`GET /api/v1/analysis/prompts` 200，返回 16 份 `{id,name,label,prompt}`。panel 回源的端到端取数留待工作流联调阶段验证。
5. **LLM**：`.env` 已配 `OPENAI_BASE_URL=https://api.deepseek.com`、`OPENAI_MODEL=deepseek-v4-pro`，`OPENAI_API_KEY` 已填；模型可用性待工作流联调时确认。分析师 QuickThink / 决策 DeepThink 两档模型在 Go 内配置。
6. **W6 新闻预取**：`AI_SDK_BASE_URL` 已配 `https://w6.hc.infhis.cn`；缺失 key 时新闻侧降级，服务仍可启动。
7. **报告 SQLite**：`AIGOSTOCK_SQLITE_PATH` 默认 `data/aigostock.db`（拷来的库含历史报告）；本版本沿用 Go 自带 SQLite，不并入 tsp 报告库（改造期再统）。
8. **提示词后台鉴权（已落地，2026-11-17）**：Go `/api/v1/analysis/prompts` 三接口旧版无鉴权中间件。本版本按 tsp 要求由**超管**管理：Python 反代层 `app/api/aistock_proxy.py` 对 `analysis/prompts` 与 `analysis/prompts/{id}` 调 `permissions.require_admin`（不改 Go）；前端菜单走 `AdminGuard` 后台入口。已验证守卫矩阵：free 用户访问列表/详情 → 403；admin → 200 透传；非守卫路由（如 `market/global-indexes`）不拦截。密码全局模式（`state.user=None`）视为管理员放行。
9. **Python 反代（已落地，2026-11-17）**：新增 `app/api/aistock_proxy.py`，`/api/v1` 与 `/api/v1/{path:path}` 全方法透传到 Go sidecar；`Settings` 加 `aistock_url`（默认 `http://127.0.0.1:8787`）、`aistock_proxy_read_timeout`（默认 2700s，对齐 SSE/W6 长链路）；`main.py` 注册 `aistock_proxy.router`。httpx 流式 `send(stream=True)` + `aiter_bytes()`，过滤 hop-by-hop 头；上游不可达 502、超时 504、未配置 503。进程内验证：经反代取 prompts 200、16 份、25KB；不可达返回 502。注意：**Token（Bearer）通道**对未登记进开放清单的 `/api/v1/*` 仍按网关规则 403，本版本前端走浏览器会话（cookie）通道访问反代；dev 下 Vite 是否再补 `/api/v1` 直连 8787 规则，留待前端阶段确认，生产统一走 3018 反代。
10. **前端搬运与依赖对齐（已落地，2026-11-17）**：`features/tradingAgents/` 14 文件、`lib/aistockApi.ts`、`lib/symbols.ts`、`pages/StockAnalysis.tsx`、`pages/PromptAdmin.tsx` 已搬入 TSP；逐文件核对公共模块引用（`api.instrumentSearch/instrumentNames`、`QK.stockLevels`、`useLastStock`、`StockFinancialSearch`、`LastStockChip`、`AnalysisKChart` 类型导出、`Toast`/`cn`）均对齐。补齐 4 个缺失依赖：`@xyflow/react@^12.11.2`、`zustand@^5.0.14`、`react-markdown@^10.1.0`、`remark-gfm@^4.0.1`（framer-motion/echarts TSP 已有，feature 未直接引用）。验收：`pnpm build`（`tsc -b && vite build`）通过，StockAnalysis 与 PromptAdmin 按需分包；`navOrder/navLayout` 15 项测试通过。未搬旧版周边（picker 三扫描器、自选定时、持仓看板、回测、通知、VLM）。
11. **dev 代理口径（已确认）**：TSP Vite 现有 `/api` → `:3018` 前缀代理，无前缀 rewrite，`/api/v1/*` 同源请求经 Vite → 3018 Python 反代 → 8787；本版本 dev 不另补 `/api/v1` 直连 8787 规则。`aistockApi` 的 `VITE_AISTOCK_API_URL` 留空即走同源。若联调发现长 SSE 被 Vite/Node 空闲断开，再评估补直连规则与 `proxyTimeout`。
12. **Go × TSP 数据链路对照（2026-11-17）**：20 个研判工具逐项对照 TSP 已有数据能力，分为「可复用 TSP / 口径待对齐 / Go 独有保留直连」三类，详见 [`go-tsp-data-mapping.md`](./go-tsp-data-mapping.md)。已落地回源：日 K（panel `/api/kline/daily`）、个股新闻（panel `/api/info/news`，网关登记 `read:market`，失败/空回退东财→新浪）；其余专题继续 Go 直连外部。复权因子/基础信息/概念归属经核实暂缓，原因见对照文档 §5.2。

### 7.3 执行步骤（每步可验证）

1. ✅ 清磁盘 → 编译 `bin/aistock-server` 成功；`AI_DATA_DIR / PANEL_API_URL / LLM` 配置就位；Go 单进程起在 :8787，探活 200（2026-11-17）。
2. ✅ Python 侧搬 `aistock_proxy.py` + 配置 + 路由注册；进程内经反代打到 Go，`/analysis/prompts` 列表 200、16 份；守卫矩阵与 502 分支已验证（2026-11-17）。
3. ✅ 搬前端 API / feature / 两页面 + 路由 + 导航；`pnpm build` 通过，导航测试 15 项通过（2026-11-17）。壳组件复用 TSP 现有自研版。
4. 🟡 端到端（2026-11-17 浏览器实测，已跑完一次完整闭环）：登录会话下历史报告列表（51 条）下拉回看、详情 hydrate、自动切 AI tab 均通过；研判 SSE 启动并推进，6 名分析师 + 博弈裁判 + 多空辩论 + 研究经理 + 交易员 + 三方风控 + 风险法官 + 风险经理完整链路跑完（21m30s，POST 200，status=completed，decision=HOLD，direction=观望）；panel 日 K 回源成功、news panel empty 走兜底、W6 WebSocket 连接成功；导出 ZIP（合并稿 + 逐 Agent md，97.6 KB）产出正常。**遗留（#23）**：顶层 confidence/risk_items/key_metrics 为空，target_price/stop_loss 因 HOLD 按设计留空。结构化字段由 `report_resolve.go` 从 Agent 自由 Markdown 正则抽取（置信度「置信度: 70%」、风险项「主要风险：」行/「【风险提示】」/`-` 列表、关键指标「名: 值」或表格行）；本次最终报告为自由叙述（风险用 `### 风险1：` 小节、PE 写作 `PE(TTM) 12.96`），未命中正则。需核对最终节点提示词是否要求标准结构化输出行，必要时增强正则。W6 单次预取约 10 分钟且被多次调用，是耗时主因。
5. ✅ 提示词后台（2026-11-17 浏览器实测）：超管会话下 16 份 Agent 提示词列出、查看正文、`PUT /prompts/:id` 200、覆盖标记（overridden）持久化、重新 GET 内容一致；反代层 `require_admin` 守卫就位。普通角色 403 分支未在浏览器实测（守卫为后端代码层强制）。
6. 🟡 失败路径：旧报告 `status=failed` 根因已定位——旧 sidecar 进程环境变量带了一把无效 LLM key（尾号 6a05，401 Authentication Fails）；`.env` 的 key 对 DeepSeek `/models` 与 chat/completions 均 200、模型 `deepseek-v4-pro` 在列，重启后新进程读 `.env` 有效 key，研判启动无 401。前端错误文案映射（连接中断/超时/sidecar 不可用）在 `aistockApi.describeAistockClientError` 代码层；sidecar 未启动、面板取数失败、W6 缺失等路径尚未主动触发实测。

### 7.4 本版本明确不做（留待下一轮改造）

- 不把 Go 编排/LLM/工具翻译为纯 Python（§5.5 / §5.6 备选）。
- 不并入 tsp 统一报告库、不做用户侧长上下文会话存储。
- 不补齐外部缺口数据源的新封装；缺口角色沿用 Go 现有降级链。
- 不搬旧版周边功能（定时自选、持仓追踪看板、回测、邮件/企微、VLM 解析、赞助/反馈）。

---

## 8. 待确认事项

### 8.1 「格浪」后端的确切所指
- 当前判断：源仓库 `backend-go`（Golang 多 Agent 后端）。源仓库内未发现其他名为「格浪」的工程目录。
- 若「格浪」另指某个独立目录/仓库（如本机其他路径下的 Go/Python 服务），请提供路径，本报告 §3/§5 的范围需按实际代码重新核对。

### 8.2 产品与口径决策
1. 多 Agent 面板形态：独立「个股分析」页（基本数据/AI 双 tab），还是嵌入现有股票预览弹窗的新 tab？
2. 历史报告存储：保留 Go SQLite，还是统一进 tsp Python 报告库供现有历史面板复用？
3. 三年五倍日 K 口径：复用 tsp 现有日 K 缓存（先核实复权方式与 low/high 字段），还是沿用源仓库独立前复权缓存目录？
4. 多智能体长任务在 tsp 现有异步模型中的承载方式：后台线程 + NDJSON，还是引入任务队列？
5. W6、bystock、东财/同花顺工具在 tsp 环境哪些有凭据、哪些需要下线降级？
6. 编排框架：起步用 Python `asyncio` 轻量自写（不引图框架），还是直接引入原版 LangGraph 以复用图拓扑/状态/断点续跑？（见 §5.6）

### 8.3 移植时必须守住的 tsp 约束（CONTRIBUTING / secondary-development）
- API 层保持轻薄，计算与数据源编排进 service；不允许页面/API 直读本地数据文件绕过仓储与归一化。
- 新页面/路由/后端优先走 custom 扩展注册；进核心源码的改动保持聚焦并附兼容验证。
- 插件/sidecar 未启用或失败不得阻断主流程；新功能测试覆盖快乐路径、关键边界、失败路径。
- 本地样本数据、账号、绝对路径不进 Git；运行时数据只放 `data/`。

---

## 附录 A：源仓库关键文件速查

### A.1 三年五倍
- 后端：`backend/app/api/stock_growth.py`、`backend/app/services/stock_growth.py`
- 前端：`frontend/src/pages/StockGrowth.tsx`、`frontend/src/components/stock-growth/StockGrowthChart.tsx`、`frontend/src/lib/api.ts`（stockGrowth*）、`frontend/src/lib/queryKeys.ts`

### A.2 多 Agent 研判
- 前端：`frontend/src/features/tradingAgents/`（`useMultiAgentAnalysis.ts`、`analysisStore.ts`、`types.ts`、`agentMeta.ts`、`AiAnalysisPanel.tsx`、`components/*`、`AnalysisHistoryDropdown.tsx`、`resolveAgentReport.ts`、`reportText.ts`、`downloadReports.ts`）；`frontend/src/pages/StockAnalysis.tsx`；`frontend/src/lib/aistockApi.ts`
- Go：`backend-go/internal/analysis/graph/trading_graph.go`、`agents/**`、`tools/**`、`types/state.go`、`interfaces/http/{handler,analysis_run,job_manager,sse,report_store,types,report_resolve,prompts_handler}.go`
- 上游原版 Python：`<upstream>/TradingAgents-AShare`；图 `tradingagents/graph/{setup,trading_graph,data_collector,conditional_logic,propagation,reflection,signal_processing,intent_parser}.py`；Agent `tradingagents/agents/{analysts,researchers,managers,risk_mgmt,trader,utils}/`；提示词 `tradingagents/prompts/{zh,en}.py`；数据 `tradingagents/dataflows/providers/`；LLM `tradingagents/llm_clients/`；API `api/main.py`、`api/services/report_service.py`、`api/database.py`；前端 `frontend/src/{pages/Analysis.tsx,stores/analysisStore.ts,components/{AgentCollaboration,DebateDrawer,DebateTimeline}.tsx,services/api.ts}`

### A.3 提示词管理
- Go：`backend-go/internal/analysis/prompts/store.go`、`agents/*/prompt.go`（Register）、`interfaces/http/prompts_handler.go`
- 前端：`frontend/src/pages/PromptAdmin.tsx`、`aistockApi.ts`（listAgentPrompts/updateAgentPrompt）

### A.4 反代与部署
- Python 反代：`backend/app/api/aistock_proxy.py`；配置 `backend/app/config.py`（`aistock_url`、`aistock_proxy_read_timeout`）
- 前端开发代理：`frontend/vite.config.ts`（`/api/v1` → Go:8787 长连 SSE 插件）
- Go 配置：`backend-go/.env.example`（`AI_DATA_DIR / PORT / PANEL_API_URL / TUSHARE_TOKEN / OPENAI_* / AISTOCK_SIDECAR / AIGOSTOCK_SQLITE_PATH / AUTH_JWT_SECRET`）
