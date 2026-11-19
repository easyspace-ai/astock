# Go sidecar × TSP 数据链路对照（哪些可复用 TSP、哪些保留直连外部）

> 范围：`backend-go/internal/analysis/tools/` 下 20 个研判工具，逐项对照 TSP 后端已落地的数据能力。
> 目的：判断 Go 自行拉取的数据哪些与 TSP 重复 / 口径冲突，哪些可改为读 TSP 已有能力，哪些必须保留 Go 直连外部。
> 结论基于实际源码与真实数据目录（`/path/to/data`）核实，未在 TSP 落地的能力如实标注「无」。
> 日期：2026-11-17。

## 1. 结论速览

### 1.1 三类划分

| 类别 | 含义 | Go 工具 |
| --- | --- | --- |
| **A. 可复用 TSP** | TSP 已有同口径数据与可对接接口，Go 可改为回源 TSP，不再直连外部 | 日 K（`get_stock_data`，已落地 panel 回源）、个股新闻（`get_news`，**2026-11-17 已落地回源** `/api/info/news`）；复权因子、基础信息、概念归属经核实暂缓（见 §5.2） |
| **B. 口径需对齐后再评估** | TSP 有相近能力，但数据源 / 频率 / 字段口径不同，本版本先不切，下轮核对字段后再定 | 全市场当日龙虎榜（`get_daily_dragon_tiger`）、行业板块排名（`get_industry_comparison`）、交易日历（`get_trade_calendar`）、分红除权事件（`get_dividend` 部分） |
| **C. Go 独有，保留直连外部** | TSP 无对应数据集 / 无对等能力，本版本继续由 Go 直连东财 / 同花顺 / 新浪 / W6 | 估值快照（`get_daily_basic`、`get_stock_basic` 估值部分）、个股龙虎榜回看、个股资金流（分钟 / 120 日）、解禁、两融、大宗交易、股东户数、机构持仓、分红方案明细、盈利预测、北向资金、热门股、W6 舆情预取 |

### 1.2 一句话回答用户的核心疑问

- **K 线确实两边都有**：Go 取日 K 的首选链路已经回源 TSP（`GET /api/kline/daily`），腾讯 / 新浪 / 本地 tsdb 只是兜底；这一项不构成重复拉取，复权口径需对齐（见 §4.1）。
- **真正重复度高、值得下轮切掉的外部直连**：个股概念归属（TSP ext_data 已有）、个股新闻（TSP info_hub 已有 `/api/info/news`）。
- **TSP 没有、Go 必须自己拉的**：估值倍数（PE/PB/市值/换手）、资金流、龙虎榜个股回看、解禁、两融、大宗、股东户数、机构持仓、分红方案、盈利预测、北向、热门股、W6 舆情——这些是 TSP 底座之外的专题另类数据。

## 2. 盘点范围与方法

### 2.1 Go 侧取数入口（20 个工具）

工具注册于 `internal/analysis/tools/`，分四个文件：

- `stock_tools.go`：行情 / 基础信息（panel → 腾讯 → 新浪 → 本地 tsdb 降级链）。
- `eastmoney_tools.go`：东财数据中心 + 板块 + 资金流 + 新闻。
- `ths_tools.go`：热门股 / 北向 / 盈利预测（北向、预测实际也打东财 datacenter）。
- `w6_tools.go`：W6 舆情研究会话（外部 LLM SaaS）。

### 2.2 TSP 侧数据底座（已核实）

| 数据集 / 服务 | 落点 | 标准化字段（核实自 normalizer / schemas / 数据目录） |
| --- | --- | --- |
| 日 K | `kline_daily/`、`GET /api/kline/daily` | symbol, date, open, high, low, close, volume, amount, quote_ts |
| 复权因子 | `adj_factor/`（含 dividend/bonus/allot/allot_price/prev_close 明细列） | symbol, trade_date, ex_factor |
| 分钟线 | `kline_*_minute/`、`/api/kline/minute*` | 分钟 OHLCV |
| 实时行情 | provider `get_realtime`（不落分区） | TickFlow quotes 原始列 |
| 五档 | `depth5/` | — |
| 财务三表 + 股本 + 指标 | `financials/{income,balance_sheet,cash_flow,shares,metrics}`、`/api/financials/*` | 指标表为财务比率（ROE/ROA/毛利率/净利率/增速/周转/偿债），**无 PE/PB/市值/换手** |
| 标的清单 | `instruments/`、`/api/kline/instruments/*` | symbol, name, code, exchange, asset_type, listing_date, status, source |
| 交易日判断 | `services/trading_day.py` `is_trading_day()` | 布尔判断；**无区间日历列表端点** |
| 龙虎榜 | `services/dragon_tiger.py`、`GET /api/market-recap/dragon-tiger` | fuyao 源，按日全市场三榜（all/org/hot_money），落 JSON 缓存 |
| 概念 / 行业归属 | `ext_data/ext_gn_ths`、`ext_data/ext_hy_ths` | symbol, code, 股票代码, 股票简称, 所属概念 / 所属同花顺行业 |
| 个股新闻 / 公告 / 快讯 | `info_hub/by_code/{news,announcements,flash}`、`GET /api/info/{news,announcements,flash}` | title, summary, content, url, media, publish_time, source_primary, heat |
| 板块轮动榜 | `services/sector_rotation`、`GET /api/sector-rotation?kind=concept|industry` | 盘中分钟聚合：涨幅 / 成交额 / 动量 / 资金流综合分，top N |

> 核实为「不存在」的 TSP 能力：北向资金、两融、大宗交易、解禁、股东户数、机构持仓、盈利预测、个股资金流（主力 / 超大单）、热门股榜、行业板块日级涨跌幅排名、区间交易日历列表。

## 3. 逐项对照

### 3.1 行情与基础信息（stock_tools.go）

| Go 工具 | Go 数据源与口径 | TSP 现有 | 归类 | 复用建议与口径注意 |
| --- | --- | --- | --- | --- |
| `get_stock_data` 日 K | panel(TSP `/api/kline/daily`) → 腾讯 `web.ifzq.gtimg.cn` → 新浪 `money.finance.sina` → 本地 tsdb；返回 OHLCV 表 | 日 K `kline_daily/`，panel 已是首选 | **A 可复用** | 已落地。对齐点：复权方式（TSP 有 adj_factor 复权视图，需确认 `/daily` 返回前复权 / 不复权）、volume/amount 单位、日期范围与缓存。腾讯 / 新浪 / tsdb 保留为兜底，不删。 |
| `get_stock_basic` 基础信息 | 腾讯快照（名称 / 现价 / 市值 / PE/PB）→ tsdb；注释明确腾讯快照不含行业 / 上市日期 | instruments（名称 / 代码 / 上市日期 / 状态）+ realtime 价格；无标准化估值列 | **A 部分 + C 部分** | 名称 / 代码 / 上市日期可走 TSP instruments；PE/PB/市值 TSP 底座缺，估值字段保留腾讯快照或下轮补 TSP 估值聚合。 |
| `get_daily_basic` 每日指标 / 估值 | 腾讯实时快照：PE(TTM)、PE 静态、PB、总市值、流通市值、换手率；**忽略 start/end 入参，本质是实时快照** | financials/metrics 为财务比率（无估值倍数）；realtime 价格；无 PE/PB/市值/换手标准化列 | **C 保留直连** | TSP 当前不能替代。注意该工具名「daily_basic」易被误解为历史每日指标，实际是实时快照；下轮若 TSP 补估值快照聚合再切。 |
| `get_trade_calendar` 交易日历 | 腾讯上证指数日 K 日期序列 → tsdb | `trading_day.is_trading_day()` 布尔判断；本地 `kline_daily` 分区日期集合（`dragon_tiger._local_trading_days` 同款做法）；无区间列表端点 | **B 口径待对齐** | TSP 本地日 K 分区即交易日集合，可由 TSP 提供区间日历；当前仅布尔接口，需补只读日历端点。不补则保留腾讯 / tsdb。 |
| `get_adj_factor` 复权因子 | 本地 tsdb `GetAdjFactor` | `adj_factor/` 数据集，除权事件明细完整 | **A 可复用** | Go 当前只读本地 tsdb，可改为回源 TSP adj_factor。对齐因子链口径与除权事件明细列。 |

### 3.2 东财 / 同花顺专题（eastmoney_tools.go / ths_tools.go）

| Go 工具 | Go 数据源（reportName / 端点） | TSP 现有 | 归类 | 复用建议与口径注意 |
| --- | --- | --- | --- | --- |
| `get_dragon_tiger_board` 个股龙虎榜回看 | 东财 `RPT_DAILYBILLBOARD_DETAILSNEW`，按个股回看 N 日 | 仅当日全市场三榜，无个股回看接口 | **C 保留直连** | TSP 无对应；继续走东财。 |
| `get_daily_dragon_tiger` 全市场当日龙虎榜 | 东财 `RPT_DAILYBILLBOARD_DETAILSNEW`，按净买额排序 | `GET /api/market-recap/dragon-tiger`（fuyao，当日全市场 all/org/hot_money，自动回退上一交易日） | **B 口径待对齐** | 方向一致可评估复用；差异：源 fuyao vs 东财、归一化字段不同、TSP 有上一交易日回退标记。联调核对字段与 fuyao 可用性后再定。 |
| `get_concept_blocks` 个股所属概念 | 东财 push2 `qt/slist/get` | ext_data `ext_gn_ths`（个股 → 所属概念，同花顺源，snapshot） | **A 可复用** | 口径高度重合。差异：源同花顺 vs 东财、概念分类体系不同；字段映射需对齐。 |
| `get_fund_flow` 个股分钟级资金流 | 东财 push2 `qt/stock/fflow/kline/get`（主力 / 超大 / 大 / 中 / 小单净流入） | 无个股资金流数据集；板块级用扩展资金流做轮动榜；watchlist 可选 `fund_flow.net_inflow` 外部列 | **C 保留直连** | TSP 无个股资金流。 |
| `get_fund_flow_120d` 个股 120 日资金流 | 东财 push2his `qt/stock/fflow/daykline/get` | 同上，无 | **C 保留直连** | 同上。 |
| `get_industry_comparison` 行业排名 | 东财 push2 `qt/clist/get`（行业名 / 涨跌幅 / 上涨家数） | `sector_rotation` 概念 / 行业盘中轮动榜（涨幅 / 成交额 / 动量 / 资金流综合分） | **B 口径待对齐** | 都是板块排名，但口径差异大：TSP 盘中分钟聚合 + 综合评分，Go 日级快照 + 单纯涨跌幅 / 家数。本版本保留 Go 直连，下轮评估接 TSP 板块榜。 |
| `get_lockup_expiry` 解禁到期 | 东财 `RPT_LIFT_STAGE` | 无 | **C 保留直连** | — |
| `get_margin_trading` 两融 | 东财 `RPTA_WEB_RZRQ_GGMX` | 无 | **C 保留直连** | — |
| `get_block_trade` 大宗交易 | 东财 `RPT_DATA_BLOCKTRADE` | 无 | **C 保留直连** | — |
| `get_holder_count` 股东户数 | 东财 `RPT_HOLDERNUMLATEST` | 无（代码中 `holder` 命中均为 SQL `placeholders` 占位符，非股东户数） | **C 保留直连** | — |
| `get_org_hold_summary` 机构持仓 | 东财 `RPT_F10_MAIN_ORGHOLD` | financials 无机构持仓表 | **C 保留直连** | — |
| `get_dividend` 分红 | 东财 `RPT_SHAREBONUS_DET`（分红送转方案 / 派息明细） | adj_factor 含 dividend/bonus/allot 除权事件明细（服务于复权，非分红方案展示）；无分红方案明细接口 | **B 部分同源** | 除权事件可参考 TSP adj_factor；分红方案 / 派息明细展示 TSP 无，保留直连。 |
| `get_profit_forecast` 盈利预测 | 东财 `RPT_WEB_RESPREDICT` | 无 | **C 保留直连** | — |
| `get_northbound_flow` 北向资金 | 东财 `RPT_MUTUAL_DEAL_HISTORY`（市场级日净流入，非个股） | 无 | **C 保留直连** | — |
| `get_hot_stocks` 热门股 | 同花顺 `dq.10jqka.com.cn/fuyao/hot_list_data` 热度榜 | 无热门股榜（`hot` 关键词命中为热点缓存 / 他处借用词） | **C 保留直连** | — |

### 3.3 新闻与舆情

| Go 工具 | Go 数据源 | TSP 现有 | 归类 | 复用建议与口径注意 |
| --- | --- | --- | --- | --- |
| `get_news` 个股新闻 | 东财 `search-api-web.eastmoney.com` 搜索 → 新浪 `vip.stock.finance.sina.com.cn` 兜底（GB18030 正则解析）；`NewsArticle{Title,Date,URL,Source,Content}` | info_hub `/api/info/news?code=<6位>&size=`，字段 title / summary / content / url / media / publish_time / source_primary / heat，近 7 天，ths+em 源 | **A 已落地回源（2026-11-17）** | Go `GetNews` 首选 panel `/api/info/news`，解析 `items[]` 映射 NewsArticle（summary 优先、空取 content；publish_time 优先、空取 publish_date）；panel 失败 / 空列表仍走原东财 → 新浪兜底链。网关 `_RULES` 已登记 `GET /api/info/news` → `read:market`。实测 Bearer 200、5 条解析正确。另有 `/api/info/announcements`（公告）、`/api/info/flash`（快讯）可后续接入。 |
| `get_global_news` W6 舆情预取 | W6 网关 `w6.hc.infhis.cn`：新建 OSINT 会话 → 发 prompt → 回传文章与 artifact 下载 | TSP 无对等能力 | **C 保留直连** | 属外部 LLM 舆情 SaaS，不是行情数据；凭据与可用性联调时确认。 |

## 4. 口径冲突与对齐风险（即使复用也要处理）

### 4.1 复权方式
- TSP 日 K 经 adj_factor 提供复权视图；Go panel 回源只解析 `rows[].date/open/high/low/close/volume`。需确认 `/api/kline/daily` 默认返回前复权、不复权还是后复权，避免与腾讯 / 新浪兜底链口径不一致，导致多空技术面判断前后矛盾。

### 4.2 字段单位
- 价格（元）、成交量（股 / 手）、成交额（元 / 万元）、市值（亿）在东财 / 新浪 / 腾讯之间单位不一；Go 侧 `formatAmount` 已做亿元换算。复用 TSP 时以 TSP normalizer 标准化单位为准，不要再按外部源单位二次换算。

### 4.3 频率与时效
- Go 部分工具是实时快照（`get_daily_basic` 忽略日期入参）；TSP 落库数据多为日级，盘中实时值走 realtime。研判在盘中 / 盘后不同时点，取数口径要标明。

### 4.4 缓存口径
- TSP 日 K / 龙虎榜 / info_hub 均按日落库分区并有缓存；Go panel 回源不落地、每次实时查。复用 TSP 时需说明新鲜度（盘后数据 vs 盘中实时），避免研判引用陈旧快照。

### 4.5 标的代码格式
- TSP symbol 形如 `600901.SH`；Go 内部用 tsCode，`splitTSCode` / `toTSCode` / `sinaMarketPrefix` 已做 sh/sz/bj 前缀转换。对接 TSP 接口时统一使用 TSP 代码格式，info_hub `/api/info/news` 用 6 位 code。

### 4.6 回源鉴权前置（Bearer 通道）
- Go sidecar 回源 TSP 走 Bearer Token（scope `read:market`）。当前网关 `_RULES` 开放 kline / intraday / index / overview / screener 部分 GET 路径，以及本轮新增的 `GET /api/info/news`；`/api/info/announcements`、`/api/info/flash`、ext_data、sector_rotation 等未登记，Bearer 通道会按「未对外开放」403。
- 因此下轮把任一 Go 工具切到 TSP 新端点前，需先在 `api_gateway.py` 规则表登记对应 GET 路径与 scope（或扩展 Token scope）；现状下 Go 回源已开放的 `/api/kline/daily` 与 `/api/info/news`。浏览器会话（cookie）通道不受此清单限制。

### 4.7 数据源凭据与风控
- TSP 侧外部源走 fuyao / tickflow / 同花顺等 custom provider，凭据在 TSP 配置内；东财 / 新浪 / 腾讯无凭据限制但有风控与限流。Go 已做强制 IPv4、连接重试、throttle 限流。复用 TSP 能力时受 TSP provider 可用性约束；保留的 Go 直连工具仍需自带降级与重试。

## 5. 本版本建议（最小改动，不先改造）

### 5.1 先不动（沿用现状）
- 日 K panel 回源已落地；其余 C 类专题工具继续 Go 直连外部，保留现有降级链。
- 不在本版本为切 TSP 而新增 TSP 后端封装或数据集（符合「本版本不做外部缺口数据源新封装」约束）。

### 5.2 A 类逐项落地状态（2026-11-17 核实）

| 项 | 状态 | 核实结论与暂缓原因 |
| --- | --- | --- |
| `get_news` 个股新闻 | ✅ **已落地回源** | 首选 panel `/api/info/news`，失败/空回退东财→新浪；网关已登记 `read:market`；实测 200、5 条解析正确。 |
| `get_stock_data` 日 K | ✅ 此前已落地 panel 回源 | 见 §3.1；复权口径待联调核对。 |
| `get_adj_factor` 复权因子 | ⏸ 暂缓 | Go `GetAdjFactor` **未注册进研判工具链**（`GetAllTools` 未包含，属未调用路径）；且 TSP 无按 symbol/日期返回因子序列的只读端点，仅 data.py 有统计聚合。无实际收益，本轮不动。 |
| `get_stock_basic` 基础信息 | ⏸ 暂缓 | TSP `/api/kline/instruments/names` 仅返回 `{symbol: name}`，无上市日期/行业；腾讯快照已提供名称与现价，切 TSP 不增信息。PE/PB/市值属 C 类，待 TSP 补估值聚合（§5.3）。 |
| `get_concept_blocks` 概念归属 | ⏸ 暂缓（口径 B） | TSP ext_data `ext_gn_ths` 仅提供概念名称清单，**缺 Go 现用的板块当日涨跌幅与龙头股**；且 ext-data 属 `read:ext` scope，sidecar Token 现为 `read:market`。字段缩水 + scope 调整，留待下轮评估。 |
| `get_daily_dragon_tiger` 全市场龙虎榜 | ⏸ 待评估（口径 B） | 方向接近，先核对 fuyao 字段、可用性与上一交易日回退口径，再定是否切 TSP。 |

### 5.3 需 TSP 先补能力、Go 才能切的项
- 复权因子按标的/日期的只读序列端点（当前仅统计聚合）。
- 区间交易日历列表端点（当前仅布尔判断）。
- 估值快照聚合（PE/PB/总市值/流通市值/换手率）。
- 个股资金流数据集、行业板块日级排名（若下轮要统一到 TSP 底座）。

## 附录 A：Go 外部数据源域名清单（tools 目录统计）

| 域名 | 用途 |
| --- | --- |
| `datacenter-web.eastmoney.com` | 龙虎榜 / 解禁 / 两融 / 大宗 / 户数 / 机构持仓 / 分红 / 北向 / 盈利预测 |
| `push2.eastmoney.com` | 概念板块 / 资金流分钟 / 行业排名 |
| `push2his.eastmoney.com` | 资金流 120 日 |
| `web.ifzq.gtimg.cn` / `qt.gtimg.cn` | 日 K 兜底 / 腾讯实时快照 |
| `money.finance.sina.com.cn` / `vip.stock.finance.sina.com.cn` | 日 K 兜底 / 个股新闻 |
| `dq.10jqka.com.cn` / `iwencai.com` | 热门股 / 问财 |
| `w6.hc.infhis.cn` | W6 舆情研究会话 |
| `http://127.0.0.1:3018` | panel 回源 TSP（日 K，唯一已对接的内部接口） |

## 附录 B：TSP 已核实「无对应」清单（Go 保留直连的依据）

北向资金、两融、大宗交易、解禁、股东户数、机构持仓、盈利预测、个股资金流（主力 / 超大单 / 大单）、热门股榜、行业板块日级涨跌幅排名、区间交易日历列表、估值倍数标准化列（PE/PB/市值/换手）。
