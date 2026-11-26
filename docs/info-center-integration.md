# 公告 / 新闻 / 快讯集成 TSP 方案（待确认）

> 状态：**只出方案，暂不改代码**。本文基于当前仓库真实扩展入口和两个独立项目（`cninfo-crawler`、`stock-news`）的现有实现整理，供确认后再落地。

## 一、结论

采用 **L2 二次开发**，不修改核心源码：

| 能力 | 落地方式 |
| --- | --- |
| 页面 | `frontend/src/custom/info-center/extension.tsx` 注册路由 + 导航 + 个股详情插槽 |
| 后端 API | `backend/app/custom/info_hub/` 扩展包，`setup(registrar)` 注册独立 `APIRouter` |
| 数据 | 扩展自有数据目录 `<DATA_DIR>/info_hub/`，分「原始层 / 统一层 / 索引层」 |
| 定时任务 | 扩展 `startup(context)` 内启动后台调度器，不新增核心调度钩子 |
| 数据源 Provider | **不接入**。公告/新闻是事件流，不是 `daily/realtime/financial` 等行情数据集 |
| 扩展数据 `ext_data` | **不作为主存储**。首版不引入；第二阶段如需要「公告数/新闻热度」数值因子，再派生一张日频宽表 |

已经验证过：`cninfo_graph` 就是同一套模式的成功先例——后端 custom 包 + 自有 `store` + `/api/graph` 路由 + 前端 custom 页面 + 个股详情 tab，没有改 `app/main.py` 或 `router.tsx` 等核心热点。

**首版建议同时交付：公告中心、新闻中心、快讯、个股「资讯」Tab 四块。** 公告和新闻不再依赖独立服务在线，TSP 页面只读本地统一数据；源站故障只影响抓取，不影响查询。

---

## 二、扩展机制复核

### 2.1 前端

- 入口：`frontend/src/custom/<namespace>/extension.tsx`，构建时自动发现。
- 可注册 `routes`、`navigation`、`slots`，`apiVersion: 1`。
- 完整页面必须走「路由 + 导航」，不要塞进某个插槽；页面组件用 `React.lazy` 延迟加载。
- 路由冲突时只禁用该扩展，不影响核心页面。
- 已开放插槽中有 `stock-preview.tabs`，context 为 `{ symbol, name }`，适合放「资讯」。
- 扩展导航不需要声明 `parentId`，可在「设置 → 页面管理 → 菜单与页面」归入一级分类。

### 2.2 后端

- 入口：`backend/app/custom/<module>.py`，普通模块自动发现；包形式同样可加载（`cninfo_graph` 已验证）。
- 模块必须定义：
  - `EXTENSION_ID`
  - `EXTENSION_API_VERSION = BACKEND_EXTENSION_API_VERSION`
  - `setup(registrar)`，内部 `registrar.include_router(router)`
  - 可选 `startup(context)`
- `ExtensionContext` 只暴露 `api_version`、`data_dir`、只读 `RepositoryAccess`，不暴露整个 `app.state`。
- 注册是「先暂存、校验通过后原子提交」；重复 ID、版本不匹配、路由冲突都会整体拒绝，不会半注册。
- 启动钩子失败只记录错误，不阻止主程序启动。

### 2.3 为什么不用数据源 Provider

`backend/app/data_providers/` 的契约是 7 类标准化市场数据：

```text
daily / adj_factor / realtime / minute / depth5 / full_minute / financial
```

Provider 的输出是 `polars.DataFrame`，供策略、回测、 enriched、缓存和前端行情链路消费。公告/新闻的特征是：

- 事件流，不是按标的、按交易日的数值矩阵；
- 有标题、正文、PDF、来源、原始 ID、多源变体；
- 同一事件可能有 2~3 个来源，需要合并而不是覆盖；
- 查询维度是日期、公司、类型、来源、关键词，不是资产类型 + 周期 + 日期。

如果把 `news` / `announcement` 塞进 capability，需要同时改 schema、normalizer、能力路由、缓存、插件配置和测试矩阵，范围远超本次集成，而且会把「资讯」和「行情」两条链路绑死。

**结论：公告/新闻放在扩展内部，作为「扩展私有数据 + 扩展 API」；需要给策略消费时，再派生数值指标，而不是直接暴露原始事件流。**

### 2.4 为什么不用 `ext_data` 作为主存储

`ext_data` 是列式快照/时序数据集，字段类型只有 `string/int/float/bool`，适合数值因子和横截面表，不适合：

- 多源变体（同一公告的巨潮版和东财版）；
- 数组字段（`sources`、`type_codes`、`sec_codes`、`tags`）；
- 长文本正文、PDF 链接、来源健康状态；
- 需要「原始层可重放、统一层可重建」的审计语义。

首版用扩展自有 JSONL；如果后续需要给选股/因子用，可从统一层派生：

```text
info_metrics_daily: symbol, date, announcement_count, important_count,
                    news_count, report_count, flash_hit_count,
                    latest_announcement_at, latest_news_at
```

这张表才适合放进 `ext_data` 或因子平台，且不影响原始事件存储。

---

## 三、总体架构

```text
┌─────────────────────────────────────────────────────────────┐
│ TSP 前端                                                     │
│  /info/announcements   公告中心                              │
│  /info/news            新闻中心                              │
│  /info/flash           快讯                                  │
│  个股详情「资讯」Tab    公告/新闻/研报/快讯时间线             │
└──────────────────────────┬──────────────────────────────────┘
                           │ /api/info/*
┌──────────────────────────▼──────────────────────────────────┐
│ backend/app/custom/info_hub                                  │
│  routes.py      参数校验、分页、筛选、聚合                    │
│  service.py     查询 / 统计 / 时间线                          │
│  store.py       原始层、统一层、索引层、状态层                 │
│  merge.py       跨源匹配、合并、对账、人工覆盖                 │
│  sources/       cninfo / em / ths / ths_flash 适配器          │
│  scheduler.py   抓取任务、健康检查、熔断、回补                 │
└──────────────────────────┬──────────────────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────────────┐
│ <DATA_DIR>/info_hub/                                         │
│  raw/           按源、按日保留的原始 JSONL（不可变）           │
│  unified/       合并后的统一事件（可重建）                     │
│  by_code/       关注池代码索引（可重建）                       │
│  state/         源健康、运行记录、断点状态                     │
│  overrides/     人工裁决的 same / diff                        │
└─────────────────────────────────────────────────────────────┘
```

模块目录建议：

```text
backend/app/custom/info_hub/
  __init__.py
  routes.py
  service.py
  store.py
  models.py
  merge.py
  config.py
  scheduler.py
  import_cli.py
  sources/
    __init__.py
    cninfo.py
    em_ann.py
    ths.py
    em_news.py
    ths_flash.py

frontend/src/custom/info-center/
  extension.tsx
  AnnouncementsPage.tsx
  NewsPage.tsx
  FlashPage.tsx
  StockInfoTab.tsx
  infoClient.ts
```

---

## 四、数据保存位置与目录契约

### 4.1 根目录

统一放在 TSP 的 `DATA_DIR` 下，不写入仓库、不提交 Git：

```text
<DATA_DIR>/info_hub/
```

当前本机 `DATA_DIR=/path/to/data`，即：

```text
/path/to/data/info_hub/
```

Docker / 其他部署只认 `<DATA_DIR>/info_hub`，代码里不写死绝对路径。

### 4.2 目录布局

```text
<DATA_DIR>/info_hub/
  config.json
  watchlist.json

  raw/
    announcements/
      cninfo/dt=YYYY-MM-DD/announcements.jsonl
      em/dt=YYYY-MM-DD/announcements.jsonl
      fuyao/dt=YYYY-MM-DD/announcements.jsonl      # 预留，首版不启用
    news/
      ths/dt=YYYY-MM-DD/news.jsonl
      em/dt=YYYY-MM-DD/news.jsonl
    flash/
      ths_flash/dt=YYYY-MM-DD/flash.jsonl

  unified/
    announcements/dt=YYYY-MM-DD.jsonl
    news/dt=YYYY-MM-DD.jsonl
    flash/dt=YYYY-MM-DD.jsonl

  by_code/
    announcements/<symbol>.jsonl
    news/<symbol>.jsonl
    flash/<symbol>.jsonl

  state/
    sources.json
    sync_state.json
    runs.jsonl
    sync.lock

  overrides/
    announcement_matches.json
    news_matches.json

  crosscheck/
    dt=YYYY-MM-DD.json
```

### 4.3 三层职责

| 层 | 是否可改 | 说明 |
| --- | --- | --- |
| `raw/` | 只增不改 | 每个源原样保留，字段不统一，用于审计、重放、重新合并 |
| `unified/` | 可重建 | 查询和页面只读这一层；合并规则变化时可全量重算 |
| `by_code/` | 可重建 | 关注池代码倒排索引，减少按公司查询时的全量扫描 |
| `state/` | 运行态 | 源健康、上次成功、断点、运行日志 |
| `overrides/` | 人工维护 | 误合并/漏合并的人工裁决，不参与自动规则 |

### 4.4 写入规则

- 所有写入使用临时文件 + 原子替换（沿用 TSP `fs_utils.atomic_write_text` 的口径）。
- 同一天同一源重复抓取：按源 ID 幂等覆盖；不重复追加。
- 合并层只由「原始层 + 合并规则」生成，不允许页面直接写合并层。
- 统一层为空时不得覆盖已有文件；源全失败时保留上一份有效统一层，并记录 `degraded`。
- 运行锁：进程内按 `DATA_DIR` 的单飞锁（`service._sync_lock`）。同一时刻只有一个抓取在跑；
  定时任务排队（`wait=True`），手动触发并发时返回 409。多 worker 部署目前只保证单进程内
  串行，需要部署侧让抓取请求落到同一进程。

### 4.5 保留窗口

建议初始值，全部可配置：

| 数据 | 保留 |
| --- | --- |
| 全市场公告统一层 | 400 天 |
| 关注池公司公告 | 5 年（按公司单独保留） |
| 新闻 / 研报 | 180 天 |
| 快讯 | 30 天 |
| 原始层 | 与统一层同周期；关注池公司可更长 |

---

## 五、统一字段

### 5.1 公告统一事件

```json
{
  "event_id": "ann:600519.SH:2026-11-13:7daf38e646905b60",
  "event_kind": "announcement",
  "sources": ["cninfo", "em"],
  "ids": {"cninfo": "1225670011", "em": "AN202611131831704214"},
  "source_primary": "cninfo",
  "match_status": "both",
  "match_score": 1.0,
  "match_note": "exact",
  "symbol": "600519.SH",
  "code": "600519",
  "name": "贵州茅台",
  "market": "sh",
  "org_id": "gssh0600519",
  "title": "……",
  "title_norm": "……",
  "ann_date": "2026-11-13",
  "ann_time": "18:20",
  "disclosed_at": "2026-11-13T18:20:00",
  "time_precision": "min",
  "biz_date": "2026-11-13",
  "session": "post",
  "type_primary": "定期报告",
  "type_domain": "finance",
  "type_codes": ["01010503"],
  "type_source": "东财分类",
  "is_correction": false,
  "is_material": true,
  "summary": null,
  "content": null,
  "pdf_url": "https://static.cninfo.com.cn/...",
  "pdf_source": "origin",
  "pdf_size_kb": 1024,
  "detail_url": "https://www.cninfo.com.cn/new/disclosure/detail?...",
  "industry_l1": "食品饮料",
  "industry_as_of": "2026-11-13",
  "variants": [
    {"source": "cninfo", "id": "1225670011", "title": "……", "url": "……", "time": "2026-11-13"},
    {"source": "em", "id": "AN...", "title": "……", "url": "……", "time": "2026-11-13T18:20:00"}
  ],
  "dup_of": null,
  "fetched_at": "2026-11-13T20:40:00"
}
```

### 5.2 新闻 / 研报统一事件

```json
{
  "event_id": "news:600519.SH:2026-11-13:xxxx",
  "event_kind": "news",
  "news_kind": "news",
  "sources": ["ths", "em"],
  "ids": {"ths": "123456", "em": "202611131234"},
  "source_primary": "ths",
  "match_status": "both",
  "symbol": "600519.SH",
  "code": "600519",
  "name": "贵州茅台",
  "sec_codes": ["600519"],
  "sector": "白酒与食品",
  "title": "……",
  "title_norm": "……",
  "summary": "……",
  "content": "",
  "media": "同花顺",
  "publish_time": "2026-11-13 18:20:00",
  "publish_date": "2026-11-13",
  "time_precision": "sec",
  "url": "……",
  "match_type": "source",
  "source_rank": 1,
  "heat": 0,
  "variants": [
    {"source": "ths", "id": "123456", "url": "……", "media": "同花顺"},
    {"source": "em", "id": "202611131234", "url": "……", "media": "东方财富"}
  ],
  "dup_of": null,
  "fetched_at": "……"
}
```

### 5.3 快讯统一事件

在新闻字段基础上增加：

- `flash_id`
- `sec_codes` / `sec_names`
- `tags`
- `encoded_seq`
- `match_type`：`title` / `summary` / `market`
- `code` / `name`：命中的第一只关注池公司，可空

### 5.4 代码与时间口径

- 对外统一返回 `symbol`（`600519.SH` / `000001.SZ` / `920xxx.BJ`），同时保留 `code` 六位，兼容两个独立项目的现有数据。
- 所有时间均为北京时间（Asia/Shanghai），存储为 ISO 字符串。
- 公告有 `ann_date`、`ann_time`、`disclosed_at`、`biz_date` 四个时间语义，页面必须分别展示，不能混成一个「日期」。
- 新闻/快讯 `publish_time` 到秒；公告无精确时刻时 `time_precision=day`，UI 显示「仅日期」，不伪造时分。

---

## 六、双源合并与「94 条巨潮独有」怎么处理

### 6.1 原则

**原始层不合并，统一层才合并；独有记录不删除。**

- 巨潮独有：`match_status=cninfo_only`，`sources=["cninfo"]`。
- 东财独有：`match_status=em_only`，`sources=["em"]`。
- 双源匹配：`match_status=both`，`sources=["cninfo","em"]`，`ids` 保留两个源 ID，`variants` 保留两个原始版本。
- 同源重复：按源 ID 去重；同源不同 ID 但标题相同，保留为两条并标 `related`，不自动删。

这样「94 条巨潮独有」不会消失，只是变成统一层里的 94 条 `cninfo_only`。页面默认显示合并视图，但提供来源筛选：

```text
全部 | 双源匹配 | 仅巨潮 | 仅东财 | 仅同花顺
```

列表每条记录显示来源徽标（`巨潮` / `东财` / `同花顺`）；点开详情可看到每个来源的原始标题、原始 URL、原始时间。需要审计时切到「原始来源」视图。

### 6.2 公告匹配规则

沿用 `cninfo-crawler/cninfo/unify.py` 已验证的做法：

1. 取披露日 T-1、T、T+1 三天的两源数据，全局匹配，再只写 T 分区。
2. 先按 `(code, 归一标题)` 精确匹配。
3. 再按归一标题相似度 + 同代码 + 时间窗做模糊匹配；阈值和窗口放配置。
4. 匹配成功后以巨潮为主体，东财补精确时间、细分类型、镜像 PDF；`ids` 和 `variants` 保留两边。
5. 同一份公告跨多日出现时，按 `fp = code|归一标题|biz_date` 再合一次；被合并记录写 `dup_of`，不删除。
6. 人工覆盖：`overrides/announcement_matches.json` 支持 `same` 强制配对、`diff` 阻断配对。

### 6.3 新闻匹配规则

- 同源按 `news_id` 幂等去重。
- 跨源：同代码 + 标题相似度 ≥ 阈值 + 发布时间在窗口内，才合并。
- 默认阈值偏保守（例如 0.85，窗口 6 小时），**宁可不合并，也不误合并**。
- 合并后保留 `sources`、`ids`、`variants`；正文/摘要取信息更全的一条，其他版本仍可在详情里查看。
- 疑似重复但不满足阈值：标 `related_group`，UI 显示「可能相关」，不直接合并。

### 6.4 对账与可解释性

每日产出 `crosscheck/dt=YYYY-MM-DD.json`：

```json
{
  "date": "2026-11-13",
  "cninfo_rows": 1438,
  "em_rows": 2748,
  "merged": 1638,
  "matched": 1349,
  "cninfo_only": 88,
  "em_only": 201,
  "time_filled": 1101,
  "degraded": false
}
```

页面顶部显示「双源匹配 / 仅巨潮 / 仅东财」计数；`/api/info/health` 同时返回最近一次对账和源健康。这样「为什么少了/多了」可以直接解释，不靠猜。

---

## 七、API 契约

统一前缀 `/api/info`，避免和现有 `/api/graph`、`/api/news` 混淆。

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/api/info/health` | 源健康、数据量、最近成功、是否降级、最近对账 |
| GET | `/api/info/stats?date=` | 当日公告/新闻/快讯计数，按来源、类型、板块分组 |
| GET | `/api/info/watchlist` | 关注池与板块 |
| PUT | `/api/info/watchlist` | 保存关注池；校验代码、去重、限制 200 只 |
| GET | `/api/info/announcements` | 公告分页查询 |
| GET | `/api/info/announcements/{event_id}` | 公告详情 + 多源 variants |
| GET | `/api/info/news` | 新闻/研报分页查询 |
| GET | `/api/info/news/{event_id}` | 新闻详情 + 多源 variants |
| GET | `/api/info/flash` | 快讯分页查询 |
| GET | `/api/info/timeline?code=&days=&kinds=` | 公司时间线（公告+新闻+研报+快讯） |
| POST | `/api/info/sync` | 手动抓取：`kinds=announcements,news,flash`；`live=true` 联网抓取；公告可带 `date=YYYY-MM-DD`（默认今天）。持有任一资讯页权限即可触发，非管理员限近 7 天且不能 `force`/`legacy_root`；同账号 60 秒冷却；抓取进行中返回 409 |
| POST | `/api/info/reload` | 重读本地数据（用于导入后刷新缓存） |

### 7.1 公告查询参数

```text
date=YYYY-MM-DD          单日
from=YYYY-MM-DD&to=...   区间
code=600519 / 600519.SH  公司
industry=食品饮料         行业
type=定期报告             类型
source=cninfo|em|both|only_cninfo|only_em
important=1
q=关键词
page=1&size=50            size 上限 200
```

返回：

```json
{
  "items": [],
  "total": 1638,
  "page": 1,
  "size": 50,
  "stats": {"both": 1349, "cninfo_only": 88, "em_only": 201},
  "degraded": false,
  "source_status": {"cninfo": "ok", "em": "ok"}
}
```

### 7.2 新闻查询参数

```text
code / sector / kind=news|report / source=ths|em|both
date / from / to / q / page / size
```

### 7.3 时间线

```text
GET /api/info/timeline?code=600519.SH&days=30&kinds=announcement,news,report,flash
```

返回按时间倒序的事件流，每条带 `event_kind`、`sources`、`match_status`、`url`，供个股「资讯」Tab 和公司图谱复用。

### 7.4 错误语义

- 无数据：200 + `items=[]` + `total=0`，不返回 500。
- 单源降级：200 + `degraded=true` + `source_status`，仍返回可用数据。
- 全部源失败且本地无数据：503 + 明确错误，不返回空列表伪装成功。
- 非法代码/日期/分页：400，detail 用中文可读信息。

---

## 八、页面设计

### 8.1 公告中心 `/info/announcements`

- 顶部：日期区间、公司搜索、行业、类型、来源/合并状态、重要公告、关键词。
- 统计条：`双源匹配 1349 · 仅巨潮 88 · 仅东财 201`。
- 列表：标题、公司、披露时间、类型、来源徽标、PDF/详情入口。
- 详情抽屉：统一字段 + 「原始来源」多版本切换。
- 默认按 `biz_date` 倒序；支持按 `ann_date` / `disclosed_at` 切换。
- 右上角提供「抓取今日 / 抓取该日」：按当前选择日期调用 `POST /api/info/sync?kinds=announcements&live=true&date=...`，抓取完成后自动重读并刷新列表；旁边的刷新图标只重查当前视图，不联网。
- 历史日期抓取可能需要几分钟；页面显示「抓取中」，完成提示会给出巨潮/东财条数。前端以运行日志判断完成，不把 HTTP 先超时当作抓取失败。

### 8.2 新闻中心 `/info/news`

- 板块（新能源 / 半导体与算力 / 白酒与食品）、公司、类型（新闻/研报）、来源、日期、关键词。
- 列表：标题、摘要、媒体、发布时间、来源徽标、热度。
- 默认只显示关注池；可切「全部已抓取」。
- 研报单独筛选，不与新闻混排。

### 8.3 快讯 `/info/flash`

- A 股全市场快讯流，按时间倒序。
- 「命中关注池」开关、板块/公司筛选、关键词。
- 30 秒自动刷新；页面显示「最后更新时间」。
- 只读本地数据，不直接请求同花顺；源故障时显示 stale，不空白。

### 8.4 个股「资讯」Tab

在 `stock-preview.tabs` 注册：

```tsx
{
  name: 'stock-preview.tabs',
  id: 'info-timeline',
  label: '资讯',
  order: 95,
  component: StockInfoTab,
}
```

组件接收 `{ symbol, name }`，内部再分「全部 / 公告 / 新闻 / 研报 / 快讯」，调用 `/api/info/timeline`。它复用个股详情弹窗，不自己造第二套切股/弹窗。

### 8.5 导航

建议注册三个路由和三个导航项：

```text
/info/announcements  公告中心
/info/news           新闻中心
/info/flash          快讯
```

扩展导航不声明 `parentId`；上线后在「设置 → 页面管理 → 菜单与页面」把三者归入「资讯」一级分类即可。如果不想增加三个一级项，也可以只注册一个 `/info` 聚合页，内部用 Tab 切换；但快讯要求独立模块，推荐仍保留独立路由，只是归到同一分类下。

---

## 九、定时任务、降级与断点

### 9.1 调度方式

`startup(context)` 内启动扩展自己的后台调度器（项目已有 APScheduler 依赖），只在扩展内运行，不改核心 `daily_pipeline`。单进程桌面模式可直接运行；多进程部署时每个进程都会启动调度器，而单飞锁只在本进程内生效，需要部署侧保证抓取只有一个执行者（或将调度放到独立进程）。

建议任务表沿用两个项目已验证的时间：

| 任务 | 时间 | 说明 |
| --- | --- | --- |
| 快讯 | 07:00–23:30，每 30 分钟 | 财联社/见闻窗口短，必须高频 |
| 个股新闻 | 08:30–22:00，每 30 分钟 | 同花顺为主，东财补充 |
| 公告首抓 | 交易日 16:40 | 巨潮当日 |
| 东财公告 | 交易日 17:10 | 东财当日，随后合并 |
| 公告补抓 | 交易日 20:30 | 盘后公告越晚越全 |
| 合并重算 | 交易日 20:40 | 重算 unified + crosscheck |
| 次日回补 | 交易日 06:50 | 补上一公告日深夜批次 |
| 研报回补 | 每日 22:30 / 每周日 | 90 天窗口，密度低 |
| 重要公告 PDF 摘要 | 交易日 21:40 | 可选，首版可不做 |

所有任务必须：

- 幂等：重复跑不重复写；
- 单实例：文件锁；
- 失败隔离：单个任务/单个源失败只影响自己；
- 可观测：`runs.jsonl` 记录开始、结束、条数、耗时、错误、降级源。

### 9.2 源不可用时的降级

配置示例：

```json
{
  "sources": {
    "announcements": {
      "order": ["cninfo", "em"],
      "strategy": "all",
      "retries": 1
    },
    "news": {
      "order": ["ths", "em"],
      "strategy": "all",
      "retries": 1
    },
    "flash": {
      "order": ["ths_flash"],
      "strategy": "all"
    }
  }
}
```

- `strategy=all`：所有启用源都跑，合并结果；主源失败时副源仍产出。
- `strategy=prefer`：按顺序尝试，拿到足够条数就停；适合限量 Key。
- 单源健康：`ok/fail/consecutive_fail/last_ok_at/last_error/avg_latency_ms/cooldown_until`。
- 连续失败 3 次进入冷却（默认 15 分钟），冷却期内跳过该源；恢复后自动清零。
- 公告只有巨潮成功：统一层写 `sources=["cninfo"]`、`degraded=true`，页面提示「东财不可用，当前为巨潮单源」。
- 新闻只有东财成功：同理，标 `sources=["em"]`。
- 两个源都失败：**不写空 unified**，保留上一份有效数据；`health` 返回 stale，页面顶部显示「数据可能延迟」。
- 快讯源失败：保留最近数据，页面显示最后更新时间；不伪造新条目。

### 9.3 停机 / 断月后的回补

`state/sync_state.json` 记录每个模块最后成功日期。启动时做一次 gap 检查：

- 公告：从最后成功日 +1 到昨天，逐日补抓；默认最多回补 30 天，可配置到 90 天。超过上限的日期标 `unavailable`，不静默跳过。
- 新闻：同花顺/东财都只能提供有限回看窗口；按源能力回补，超过窗口的日期标 `partial` 或 `unavailable`。
- 快讯：源只给约 6 小时窗口，停机超过窗口的部分**不可恢复**，明确标缺口，不假装补齐。
- 报告/研报：按 90 天窗口回补。
- 回补任务优先级低于当日任务，避免阻塞当日抓取；每次启动最多跑一个回补批次。

### 9.4 源恢复后的切换

- 冷却期结束后做一次轻量探针（请求 1 页/1 条），成功才恢复正常抓取。
- 恢复后自动回补冷却期间缺失的日期（受上面的窗口限制）。
- 页面 `/api/info/health` 显示 `sources` 状态、`degraded_sources`、`last_success`、`stale_seconds`。
- 不自动删除单源数据；恢复后重新合并，`sources` 从单源升级为双源。

---

## 十、历史数据导入与切换

### 10.1 导入范围

| 来源 | 导入内容 |
| --- | --- |
| `cninfo-crawler/data` | `dt=YYYY-MM-DD/announcements.jsonl`、`announcements.em.jsonl`、`unified/dt=*.jsonl`、`industry/as_of=*` |
| `stock-news/data` | `dt=*/news.jsonl`、`by_code/*.jsonl`、`flash/dt=*/flash.jsonl`、`state/sources.json` |
| `stock-news/pool` | `watchlist.txt`、`sectors.json` |
| `cninfo-graph` | 首版不导入；`data/graph/companies/*/announcements.json`、`news.json` 作为第二阶段收敛对象 |

导入方式：`python -m app.custom.info_hub.import_cli --announcements <path> --news <path> --flash <path>`。

导入规则：

- 先写 `raw/`，保留源 ID 和原始字段；
- 再按统一规则重建 `unified/` 和 `by_code/`；
- 已存在记录按源 ID 幂等跳过；
- 导入完成写一份 `state/import_manifest.json`，记录来源路径、条数、日期范围、校验和；
- 导入失败不删除已有数据，可重跑。

### 10.2 切换顺序

1. 导入历史数据 → 核对条数/日期范围；
2. TSP 页面和 API 先只读本地数据；
3. 确认页面可用后，停掉独立项目的定时任务，避免双写；
4. 由 TSP 扩展调度器接管抓取；
5. 独立项目保留为「采集验证/备用工具」，不再作为生产数据源。

首版不建议双写。双写会产生两个 `news_id` 体系、两个健康状态和两套去重结果，后续很难对齐。

---

## 十一、分阶段实施与验收

### Phase 0：骨架与导入（约 0.5 天）

- 新增 `info_hub` 包、配置、store、models、health API。
- `import_cli` 导入现有公告/新闻/快讯数据。
- 验收：`GET /api/info/health` 返回条数、日期范围、源状态；导入后条数与源项目一致。

### Phase 1：公告中心（约 1–2 天）

- 公告查询/详情/统计 API。
- 公告页、来源筛选、原始来源详情。
- 双源合并与对账。
- 验收：11-13 样例 `matched=1349`、`cninfo_only=88`、`em_only=201` 可复现；筛选「仅巨潮」能看到独有条目。

### Phase 2：新闻/研报/快讯（约 1–2 天）

- 新闻/研报/快讯查询 API。
- 新闻页、快讯页、关注池与板块筛选。
- 验收：29 只关注池全部有数据或明确「暂无」；1185 条新闻、245 条快讯可查；来源徽标正确。

### Phase 3：个股资讯 Tab + 调度 + 降级（约 1 天）

- `stock-preview.tabs` 注册「资讯」。
- 调度器、健康检查、熔断、回补。
- 验收：`GET /api/info/timeline?code=600519.SH&days=30` 返回公告+新闻+研报+快讯；手动停掉东财 Key，公告仍能单源产出并标 `degraded`。

### Phase 4：收敛与可选指标（约 0.5 天）

- `cninfo_graph` 的公告/新闻改为读 `info_hub`，不再自己抓（保留旧数据只读回退）。
- 可选：派生 `info_metrics_daily` 给因子/策略用。
- 验收：公司图谱详情与资讯中心同一公司条数一致；删除旧缓存不产生 404。

### 统一验收命令

```bash
cd backend
uv run pytest tests/test_info_hub_*.py -q
uv run ruff check app/custom/info_hub tests/test_info_hub_*.py

cd ../frontend
pnpm build

cd ..
git diff --check
git status --short
```

必须确认最终 diff **不包含**：

- `backend/app/main.py`
- `frontend/src/router.tsx`
- `frontend/src/components/Layout.tsx`
- `frontend/src/lib/api.ts`
- `frontend/src/lib/queryKeys.ts`
- `data/` 下的运行数据

---

## 十二、风险与不做

### 风险

| 风险 | 处理 |
| --- | --- |
| 跨源误合并 | 保守阈值 + `variants` 保留 + 人工 `diff` 覆盖 + 「仅某源」筛选 |
| 源站限流/封禁 | 每源 QPS/页数/超时/重试配置；熔断冷却；不并发轰炸 |
| 新闻只能拿摘要 | 首版明确「摘要级」，不承诺全文；需要全文再单独抓 URL |
| 快讯窗口短 | 明确不可回补区间，页面显示缺口 |
| 公告 PDF 解析成本高 | 首版只存链接；重要公告摘要作为可选项，后续再加 |
| 数据体积增长 | 保留窗口 + 按日分区 + 关注池按公司历史；必要时加 DuckDB 物化索引 |
| 多进程重复调度 | 进程内单飞锁只约束单进程；多 worker 部署需让抓取请求落到同一进程，或改为外部锁 |
| 与 `cninfo_graph` 重复 | Phase 4 收敛，graph 只保留公司档案/股东/高管，公告/新闻读统一层 |

### 明确不做（首版）

- 不把公告/新闻注册为 market data provider capability。
- 不把原始事件流塞进 `ext_data`。
- 不复制独立项目的 HTML 页面；只复用数据契约和抓取逻辑。
- 不做全量 PDF 正文抽取和全文检索。
- 不做跨市场/港美股的公告新闻。
- 不做自动交易或策略信号（资讯只作为展示和后续因子输入）。
- 不删除独立项目的数据；切换后只停调度，不删目录。

---

## 十三、待确认的 4 个决策

| # | 决策 | 推荐 |
| --- | --- | --- |
| 1 | 页面组织：公告/新闻/快讯三个独立路由，归入一个「资讯」分类；还是只做一个聚合页 | **三个独立路由 + 页面管理归组**（快讯可独立模块，个股 Tab 复用同一 API） |
| 2 | 数据归属：一次性导入 TSP，TSP 成为唯一生产源；还是 TSP 只读独立项目数据 | **导入后 TSP 唯一生产源，停掉独立项目调度**，避免双写 |
| 3 | 首版范围：公告 + 新闻 + 快讯 + 个股资讯 Tab；还是先只做公告/新闻 | **四块一起做**；快讯数据量小、复用同一存储和 API，单独拖到下期反而增加改造成本 |
| 4 | 是否首版就派生 `info_metrics_daily` 给策略/因子用 | **Phase 4 可选**，不进入首版；先把事件查询和页面跑通 |

确认以上 4 项后，按 Phase 0→3 开始落地；Phase 4 的 `cninfo_graph` 收敛和指标派生可以单独排期。
