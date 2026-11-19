# 通达信（tdxrs）对 TSP 全量数据的覆盖对照

> 实测时点：2026-11-17（北京时间，收盘后），数据源 `tdxrs 0.6.10`（PyPI wheel），服务器 `180.153.18.170:7709`
> TSP 侧清点对象：`backup/data`（`DATA_DIR=/path/to/data`）实际落盘 + `provider` 契约 + `capabilities` 路由表
> 结论标记：✅ 可直接满足 ／ ⚠️ 部分满足（需换算或降级） ／ ❌ 不能满足（必须回退 TickFlow）

---

## 0. 结论速览

| # | TSP 数据 | 通达信 | 一句话 |
| --- | --- | --- | --- |
| 1 | `daily` 日K（raw） | ✅ | 全历史 6044 根/0.59s，vol=股（TSP 要手，÷100 即可），amount=元 |
| 2 | `adj_factor` 除权因子 | ✅ **强匹配** | `calc_fq_factors` 直接产 `div_per_share / bonus_ratio / rights_ratio / rights_price`，与 TSP 表结构逐列对齐 |
| 3 | `adj_factor_etf` ETF 因子 | ✅ | ETF 除权实测有事件（510300 共 15 条） |
| 4 | `minute` 股票分钟 | ⚠️ | 必须用 period=7；深度 90 交易日（TickFlow 基准更深，需声明 `minute_history_days`） |
| 5 | `realtime` 全市场快照 | ✅ | 80 只/批，94→71 批，≈3.5s/轮，五档/内外盘/涨速齐全 |
| 6 | `depth5` 五档 | ⚠️ | 股票/ETF 可用；**指数五档是垃圾值**，必须屏蔽 |
| 7 | `full_minute` 全量分钟 | ❌ | 无批量端点，5609 次 ≈163s/轮，打不住 6s 节奏 |
| 8 | `financial` 五表 | ❌ | 只有 36 个字段的**单期快照**，无 `period_end/announce_date` 历史 → 五张表全给不了 |
| 9 | `instruments` 维表 | ⚠️ | 有 name/pre_close/每手股数/小数位；**无 total_shares/float_shares/limit_up/limit_down/listing_date** |
| 10 | 指数日K `kline_index_daily` | ✅ | 必须走 `get_index_bars`（走 `get_security_bars` 只回 1 根且日期错） |
| 11 | 指数分钟 | ⚠️ | `get_index_bars(7)` 深度仅 4–10 个交易日；当日分时 240 点可补 |
| 12 | 同花顺行业/概念 `ext_hy_ths`/`ext_gn_ths` | ⚠️ | 通达信有板块成分（FG 20856 / GN 41420 / SZ 11554 行），但**板块名体系与同花顺不同**，只能做补充不能直替 |
| 13 | 龙虎榜 `dragon_tiger` | ❌ | 标准行情协议无此数据 |
| 14 | 资讯 `info_hub`（59M） | ❌ | 无 |

**一句话总结**：**行情四件套（日K、除权、实时、分钟）全部能接，且除权质量比现有多源拼装更好；财务、维表、全量分钟、龙虎榜、资讯必须继续走 TickFlow。**

---

## 1. TSP 实际在用的数据（实测清点）

### A. 行情底座（provider 数据集，可整体替换）

| 数据 | 落盘 | 规模 | 结构 |
| --- | --- | --- | --- |
| 股票维表 | `instruments/instruments.parquet` | 5537 行 | `symbol,name,code,exchange,region,type,listing_date,total_shares,float_shares,tick_size,limit_up,limit_down,as_of` |
| ETF 维表 | `instruments_etf/` | 1769 行 | `symbol,name,code,asset_type` |
| 指数维表 | `instruments_index/` | 599 行 | `symbol,name,code,asset_type` |
| 日K raw | `kline_daily/date=*/` | **1.3G**，逐日分区 | `symbol,close,open,high,low,volume,amount,quote_ts,date` |
| 日K enriched | `kline_daily_enriched/` | 431M | 前复权价 + `raw_*` + `limit_up_price/limit_down_price` + 换手 |
| 指数日K | `kline_index_daily/date=*/` | 246 分区 | 同日K形状 |
| 分钟K | `kline_minute/date=*/` | 181 分区，5.2M | `symbol,datetime,open,high,low,close,volume,amount` |
| 除权因子 | `adj_factor/all.parquet` | **27610 行** | `symbol,trade_date,ex_factor,dividend,bonus,allot,allot_price,prev_close` |
| ETF 除权因子 | `adj_factor_etf/` | 0B（未启用） | 同结构 |
| 财务五表 | `financials/` | 7.9M | `metrics`(5580) / `income`(44633) / `balance_sheet`(44632) / `cash_flow`(44632) / `shares`(当前无文件) |
| 五档 | `depth5/` | 0B（按需） | 契约：`bid_prices/bid_volumes/ask_prices/ask_volumes/timestamp` |
| 实时 | 内存 `QuoteService` | 6s 轮询 | `symbol,last_price,prev_close,open/high/low,volume(手),amount,change_pct(小数),timestamp,name` |

### B. 分类扩展

| 数据 | 落盘 | 规模 | 结构 |
| --- | --- | --- | --- |
| 同花顺行业 | `ext_data/ext_hy_ths/` | 5581 行 | `股票代码,股票简称,所属同花顺行业`（"服装家纺-饰品" 二级） |
| 同花顺概念 | `ext_data/ext_gn_ths/` | 5583 行 | `所属概念`（多值分号拼接） |

### C. 本地派生（不需要外部源，通达信不涉及）

`kline_daily_enriched`、`regime_history`(1699 行市场状态)、`mainline_history`(48296 行主线梯队)、`pools`、`research`、`screener_results`、`graph`、`strategies`、`paper`

### D. 另类/资讯

`dragon_tiger`（当前空）、`info_hub`（59M 资讯）、`cache/fuyao`（175M 源缓存）

---

## 2. 逐项实测证据

### ✅ 日K：口径正确，只需 ÷100

| 项 | 实测 |
| --- | --- |
| 全历史 | `get_security_bars_all(4, ..., count=8000)` → **6044 根 / 0.59s**（2001-08-27 → 2026-11-17） |
| 单请求 | 800 根上限，5 根 0.04s |
| 单位 | `vol` **股**（TSP 要手 → ÷100）；`amount` **元**；`datetime` `YYYY-MM-DD` |
| 交叉验证 | 600519 2026-11-13：close 1435.26、vol 4,177,984 股、amount 5,954,833,920 元，`amount≈close×vol` 自洽 |
| 复权 | `fq=0` 原始价，符合「provider 不得自行复权」红线 |

### ✅ 除权因子：TSP 表结构与接口逐列对齐

`calc_fq_factors(market, code)` 返回逐事件 `{date, close_before, qfq_factor, hfq_factor, div_per_share, bonus_ratio, rights_ratio, rights_price}`，600519 实测 30 个事件 / 0.42s：

| TSP 列 | tdxrs 字段 | 是否对齐 |
| --- | --- | --- |
| `trade_date` | `date`（YYYYMMDD） | ✅ |
| `ex_factor` | `qfq_factor`（逐事件，可累乘成序列） | ✅ |
| `dividend` | `div_per_share`（**已是每股**，xdxr 原始每 10 股 ÷10） | ✅ |
| `bonus` | `bonus_ratio` | ✅ |
| `allot` | `rights_ratio` | ✅ |
| `allot_price` | `rights_price` | ✅ |
| `prev_close` | `close_before` | ✅ |

唯一要处理的：底层 f32 尾数（`fenhong=280.2423095703125`）→ 落表前 `round(x, 4)`，或照 tradeflow 的 `decimal_business_f32`（最短往返十进制）洗一遍。ETF 同样可用（`get_fund_xdxr_info`，510300 实测 15 条，含 2026-01-19 每 10 份派 1.23）。

### ⚠️ 分钟：深度够用但不是无限

| 项 | 实测 |
| --- | --- |
| 股票 1min | **必须 period=7**（`KLINE_EXHQ_1MIN`）。`8` 返回 24 根同时间戳假数据 |
| 深度 | 21600 根 / 2.33s，**90 个交易日**（2026-07-07 → 2026-11-17） |
| 当日根数 | 240 根 `09:31→15:00`；末根 vol 171,300 股 = 1,713 手（与分时末点一致） |
| 其他周期 | 5min 8000 根（约 167 交易日）、15min 7856 根（约 491 交易日） |
| 单位 | 分钟线 `vol` 是**股**（与日K的"手"不同！TSP 统一要手 → ÷100），`amount` 元 |
| 时区 | 返回的 `datetime` 已是北京墙钟 naive，不需要 `TZ=` |

→ 声明 `minute_history_days = 90` 会让个股分时档位自动收窄到可行范围。

### ✅ 实时：71–94 批一轮

| 项 | 实测 |
| --- | --- |
| 单批上限 | 服务器硬上限 **80 只**（tdxrs 自限 60 → 94 批；放宽到 80 → 71 批） |
| 速度 | 29–37 ms/批；30 批/1800 只 1.17s → **全市场 ≈3.5s/轮** |
| 覆盖率 | 连跑两轮均 **100%** |
| 字段 | `price/prev_close/open/high/low`(元 float)、`vol`(手)、`amount`(元)、`b_vol/s_vol`、`bid1-5/ask1-5`、`bid_vol/ask_vol`、`speed`(0.01%)、`servertime` |
| 指数 | **五档无效**（`bid1=376173.22` 垃圾），price/amount 可用 |
| 退市码 | 回包 `code=600839` 垃圾 → 必须做回包代码校验 + 隔离名单（TSP 维表里 61 只带"退市"字样，另加 `*ST`） |

### ✅/⚠️ 指数

- 日K：**必须用 `get_index_bars`**。用 `get_security_bars` 只回 1 根且日期陈旧（是协议层对指数不适用）
- 分钟：`get_index_bars(7)` 上证 799 根（3.3 天）、深证成指 2399 根（10 天），翻页不稳定 → **指数分钟深度只有 4–10 天**
- 当日分时：240 点可用（`get_minute_time_data`，`price/avg_price/vol`）→ 可兜底画指数分时线

### ⚠️ 维表：能补 4 列，缺 5 列

`get_security_list` 1000 条/页，字段 `{code, name, pre_close, volunit, decimal_point}`，沪 `count=27881`、深 `24715`（含债券/基金，需按前缀过滤）。

- **能补**：`name`、`pre_close`、`tick_size`（=10^-decimal_point）、每手股数（volunit，股票 100/ETF 100）
- **缺**：`total_shares`、`float_shares`、`limit_up`、`limit_down`、`listing_date`
- `get_finance_info` 里有 `zongguben/liutongguben`（万股，单期）→ 可用于**当期**换手率，但 §3.4 要求的**历史股本**给不了

结论：`get_instruments` 继续不实现（现有各源均不实现），避免把 `limit_up` 写成 null。

### ❌ 财务：只有单期快照

`get_finance_info` 36 个字段：`zongguben/liutongguben/zongzichan/liudongzichan/gudingzichan/jingzichan/zhuyingshouru/zhuyinglirun/jinglirun/jingyingxianjinliu/zongxianjinliu/cunhuo/yingyelirun/...` + `ipo_date/province/industry/updated_date`。

- **没有** `period_end`、`announce_date`、`fiscal_year` → 无法构建 TSP 五张表的**时序**
- 45 个命名指标（`gpcw*.dat`）只在 Rust 侧，Python 未暴露，且需本地文件
- → `financial` 数据集不声明，自动回退 TickFlow

### ⚠️ 板块：有成分和指数，但命名体系不同

| 项 | 实测 |
| --- | --- |
| 成分 | `get_industry_blocks()` 20856 行、`get_concept_blocks()` 41420 行、`get_index_blocks()` 11554 行 → 板块名 + 成分股代码（多对多） |
| 板块指数 K 线 | `get_block_bars(4, "880001")` 正常（11-17 收 9934.179） |
| 板块指数行情 | `get_block_quotes(["880001","881155"])` → 9934.18 / 866.71 可用 |
| 与同花顺差异 | 通达信板块名是自家体系（"融资融券"、"通达信88"、880/881 段），**没有"行业-二级子行业"层级**；TSP 用的是同花顺 `服装家纺-饰品` 这种二级行业 |

→ 只能做「补充/映射源」，不能直接替换 `ext_hy_ths`。映射成本高，建议 P2。

---

## 3. 通达信能给、而 TSP 现在**完全没用**的能力（净增量）

| # | 能力 | 实测 | 可以支撑的新功能 |
| --- | --- | --- | --- |
| 1 | **逐笔成交**（当日 + 历史按日） | 0.10s/100 笔，单次上限 2000 | 大单/资金流、分笔明细、主动买卖占比 |
| 2 | **当日分时 240 点**（股票/ETF/指数） | 0.04s，`price/avg_price/vol` | 真分时图（现在只能靠 1min K 拼） |
| 3 | **历史分时按日** | `get_history_minute_time_data(date)` | 历史分时回放 |
| 4 | **板块指数**（880/881/399 段） | K线 + 行情均可用 | 板块强度、轮动、主线梯队的指数口径 |
| 5 | **板块成分**（风格/概念/指数三类） | 20856 / 41420 / 11554 行 | 题材扩散、概念热度、成分股联动 |
| 6 | **ETF 全链路** | 列表 827 只 + 分类 + 行情 + K线 + 分时 + 除权 + 财务 | ETF 专区（现在 ETF 挤在股票接口里，无分类/无折溢价基础） |
| 7 | **除权事件明细**（14 类 category 语义化） | 除权除息/送配股上市/股本变化/扩缩股/权证… | 分红送转日历、股本变动追溯 |
| 8 | **北交所** | `(2,"920826")` 行情 + K线可用 | 北交所标的 |
| 9 | **本地 `.day/.lc5` 解析 + 批量下载器** | Reader 类 26 个方法 | 离线库、历史回补 |
| 10 | **F10 公司资料** | 需源码 `maturin --features f10`，pip 包不含 | 公司档案（成本高，P2） |

---

## 4. 硬缺口（通达信给不了，必须保留 TickFlow 或别的源）

1. **财务五表时序**（`period_end/announce_date`）—— 影响所有财务分析、成长性、ROE 序列
2. **维表 5 列**：`total_shares/float_shares/limit_up/limit_down/listing_date` —— 影响涨跌停判定、换手率、股本口径
3. **历史股本 `shares` 表** —— §3.4 的"历史股本优先"契约
4. **全量分钟批量端点** —— `full_minute` 的 6s 增量轮打不住
5. **龙虎榜、资讯**（`dragon_tiger`、`info_hub`）
6. **同花顺行业/概念分类**（体系不同，需映射）

---

## 5. tdxrs 插件接口规划（把接口做丰富）

设计原则：**契约方法只做能保证正确性的 5 个数据集；增量的丰富能力用 `tx_` 前缀做扩展方法，不参与路由、不污染契约。**

### 5.1 `plugin.yaml`

```yaml
name: tdxrs
runtime: python
datasets: [realtime, depth5, daily, adj_factor, minute]
# financial / full_minute / instruments 不声明 → 自动回退 TickFlow
```

`minute_history_days: 90`（实测深度）。

### 5.2 契约方法（路由用，10 个）

| 方法 | 落点 | 关键换算 |
| --- | --- | --- |
| `get_daily` | `kline_daily` | vol ÷100（股→手）、amount 元、date 拆列 |
| `iter_daily` | 同上（流式） | 按 symbol 分批，单帧 ≤N 行 |
| `get_adj_factors` | `adj_factor` | `qfq_factor→ex_factor`、`round(div_per_share,4)`、ETF 走 fund 客户端 |
| `get_minute` | `kline_minute` | **period=7**、vol ÷100、datetime 已是北京墙钟 |
| `iter_minute` | 同上（流式） | 逐标的 yield，不攒全市场 |
| `get_realtime` | `QuoteService` | 80/批、回包 code 校验、退市隔离、`change_pct` 小数制 |
| `get_realtime_symbols` | 自选/详情补拉 | 同上；只补未覆盖标的 |
| `get_realtime_indices` | 4 个核心指数 | 走 quotes 的指数分支，**丢弃五档** |
| `get_depth_batch` | `depth5` | 复用同轮快照的五档，timestamp=服务端毫秒 |
| `test_dataset` | 设置页试拉 | 每个数据集返回首 3 行预览 |

### 5.3 扩展方法（`tx_` 前缀，12 个，不参与路由）

| 方法 | 能力 |
| --- | --- |
| `tx_kline(symbol, period, count, fq)` | 8 周期（1/5/15/30/60 分、日/周/月）× 3 复权 |
| `tx_index_kline(symbol, period, count)` | 指数专用（内部 `get_index_bars`） |
| `tx_minute_time(symbol, date=None)` | 当日/历史分时 240 点 |
| `tx_transactions(symbol, count, offset, date=None)` | 逐笔（收盘后需过滤 `vol=0` 填充记录） |
| `tx_xdxr(symbol)` | 14 类除权事件明细 |
| `tx_finance_snapshot(symbol)` | 36 字段单期快照（明确标注"非时序"） |
| `tx_security_list(market)` | 代码表（名称/前收/每手/小数位，含缓存） |
| `tx_block_components(kind)` | 板块成分 FG/GN/SZ |
| `tx_block_quotes(codes)` | 板块指数行情 |
| `tx_block_kline(code, period, count)` | 板块指数 K 线 |
| `tx_fund_list(market)` / `tx_fund_kline(...)` | ETF 列表/分类/K线 |
| `tx_server_status()` | 连接池、服务器、限流、退市隔离名单状态 |

### 5.4 内部工具（不算接口，但必须做）

1. **代码归一**：`600519.SH ↔ (1,"600519")`；`.BJ→2`；ETF 用后缀判别而不是首位数字
2. **回包校验 + 退市隔离**：`code` 不匹配即剔除；同一 symbol 连续失败进 24h 隔离名单，`info` 级日志
3. **单位适配表**：日K/实时 vol=手、分钟 vol=股、amount=元、price=元
4. **GIL 收敛**：所有 tdxrs 调用进单一专用线程 + 队列（实测 8 线程加速比 1.00×，多线程无收益）
5. **指数/ETF 分支**：指数走 `get_index_bars`、ETF 走 fund 客户端（或标准 bars，实测两者都对）

---

## 6. 落地顺序建议

| 阶段 | 范围 | 验收 |
| --- | --- | --- |
| P0 | `daily` + `realtime`（含指数） | 与现有 TickFlow 数据逐标的对比：日K `amount≈close×vol`、实时行数覆盖率 ≥99%、6s 轮询不变形 |
| P1 | `adj_factor`（含 ETF）+ `minute` | 前复权价与 `kline_daily_enriched` 偏差 <0.1%；分钟 `datetime` 全在交易时段、当日 240 根 |
| P2 | `depth5` + `tx_` 扩展（逐笔/分时/板块） | 页面接入 + `test_dataset` 全绿 |
| P3 | `full_minute`（多进程压测）、板块映射 | 压测 6s 节奏、映射覆盖率评估 |

`financial`、`get_instruments`、`dragon_tiger`、`info_hub` 明确不在本插件范围。

---

## 7. 落地状态（2026-11-17，`app/plugins/tdxrs`）

插件已按本文规划落地，实现与实测结论保持一致。与规划的两处偏差及原因如下。

### 7.1 实际实现

| 项 | 结果 |
| --- | --- |
| 清单 | `backend/app/plugins/tdxrs/plugin.yaml`，`runtime: python`，`datasets: [realtime, depth5, daily, adj_factor, minute]`，无 `api_key_env`/`billing` |
| 契约方法 | `get_realtime` / `get_realtime_symbols` / `get_realtime_indices` / `get_depth_batch` / `get_daily` / `iter_daily` / `get_adj_factors` / `get_minute` / `iter_minute` / `test_dataset` |
| 类属性 | `builtin = True`、`minute_history_days = 90` |
| 扩展接口 | 13 个 `tx_*`：`tx_kline` `tx_index_kline` `tx_minute_time` `tx_transactions` `tx_xdxr` `tx_finance_snapshot` `tx_security_list` `tx_block_components` `tx_block_quotes` `tx_block_kline` `tx_fund_list` `tx_fund_bars` `tx_server_status` |
| 依赖 | `requirements.txt` 仅 `tdxrs>=0.6.10`（PyO3 原生 wheel，无 sidecar） |
| 测试 | `backend/tests/test_tdxrs_provider.py`，49 条，假 client 注入，不依赖网络 |

全市场实测（收盘后，macOS arm64，tdxrs 0.6.10）：

| 观测 | 数值 |
| --- | --- |
| 股票池（沪+深，北交所该服务器不可用） | 5225 只，首次枚举 11.8s（12h 缓存） |
| `get_realtime` 全市场 | **5224 条 / 3.7s**（87 批 × 60） |
| 日K 增量（100 只样本外推） | 全市场约 6 分钟 |
| 除权因子（100 只样本外推，含事件筛选） | 全市场约 8 分钟 |
| 1 分钟K 单标的 21600 根 | 约 2.3s |

### 7.2 偏差一：除权因子不用 `calc_fq_factors`

实测 `calc_fq_factors(market, code, start, count)` **只对「事件落在本次返回的 bars 窗口内」正确**：
窗口外的旧事件会拿窗口边界价当前收盘（600519 `start=800` 时 2025 年事件的 `close_before`
变成 1838.03），按 `start` 分页拼接会产出大量错误因子。

因此改为与 `tdxquant` 完全同口径的公式推导：`get_xdxr_info` 事件（每 10 股 ÷10 转每股）
→ 一次原始日K拿除权前收盘 P → `ref=(P−D+AR·AP)/(1+S+AR)` → `ex_factor=P/ref`
→ 涨跌停自检剔除。5 只标的（含 ETF 510300）与 `calc_fq_factors` 的 `1/qfq_factor`
逐事件比对，**偏差 0**。

`calc_fq_factors` 仍保留在 client 里（`fq_factors`），仅在事件窗口已知时作交叉校验用。

### 7.3 偏差二：`full_minute` 不声明、`financial` 不接入

- `full_minute`：上游无批量端点，逐标的增量一轮 3–4 分钟，无法满足 6s 盘中节奏；
  `get_intraday_batch/latest` 因此不实现，避免出现「声明了却永远落后」的假覆盖。
- `financial`：`get_finance_info` 只有 36 字段单期快照（无 `period_end/announce_date`），
  不能替代财务三表时序；作为 `tx_finance_snapshot` 暴露，仅供当期换手率/规模参考。

### 7.4 已知缺口与后续项

1. **北交所**：当前服务器 `get_security_count(2)` 返回 `E2005`，北交所代码表不可用
   （股票池缺 BJ，但显式代码的报价/K线仍可拉）。换服务器或等上游修复后再补。
2. **退市码顶包**：`600355` 等无效代码回包被服务器换成 `600839` 全零，client 按
   `(market, code)` 校验剔除；`get_realtime` 因此会有约 1 条缺行。
3. **指数分钟**：`get_index_bars(7)` 深度仅 4–10 个交易日且翻页不稳，故 `minute`
   数据集不含指数（`tx_index_kline` 可显式取，但深度受限）。
4. **桌面包**：`packaging/tsp.spec` 目前只声明 fuyao/tdxquant 双声明；tdxrs 进桌面包
   需要额外把 `tdxrs` 的原生 wheel 收进 `binaries`，属独立打包任务，未在本轮改动。
