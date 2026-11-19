# tdxrs 与现有数据源对照评估（接口数量 / 使用方式 / 补全清单）

> 2026-11-17 更新：`tdxhttp`（TDX-HTTP）插件已按需求删除，本地通达信能力改由 `app/plugins/tdxrs`（tdxrs 自建直连）承接；本文仅作历史记录。

> 实测时间：2026-11-17 19:00–19:20（北京时间，收盘后）
> 实测对象：`/film/tdxrs`（v0.6.10，PyPI wheel `tdxrs-0.6.10-cp311-macosx_11_0_arm64`，纯 pip 安装，1MB）
> 对照对象：`/film/tdx-api`（Go + HTTP，现行 tdxhttp 插件的后端）、`backend/app/plugins/tdxquant`（官方 tdxaidata SDK，积分计费）
> 本文所有数字均为本机实测，未验证项已标注「待实测 / 未暴露」。

---

## 0. 结论摘要

**tdxrs 是目前四套里最接近"进程内直连 + 全套能力"的方案，可作为 TSP 的主行情源；但它解决不了财务与维表，且有两个必须先处理的工程约束（GIL、1 分钟 K 线 category）。**

| TSP 能力（数据集） | tdxrs | tdx-api(Go) | tdxquant | 结论 |
| --- | --- | --- | --- | --- |
| `realtime` 全市场快照 | **可用**（60 只/批，94 批 ≈ **3.7s/轮**） | 可用（80 只/批，6 并发 **0.57s**） | 可用（600 只/批，30–60s） | tdxrs 够用，比 Go 慢 6 倍但仍进 6s 节奏 |
| `depth5` 五档 | **可用**（快照自带，指数无效） | 可用 | 可用 | 等价 |
| `daily` 原始日K | **可用**（vol=股、amount=元，6044 根/0.59s） | 可用（另加前复权端点，易误用） | 可用 | tdxrs 只有单一干净口径，更不易踩错 |
| `adj_factor` 除权因子 | **可用（且强于两者）** `calc_fq_factors` 直接给逐事件 `qfq_factor` + `div_per_share` | **不具备**（硬缺口，需自己反推，已实测失败） | 可用（事件+公式推导） | tdxrs 首次把这块补齐 |
| `minute` 1 分钟K | **可用，但必须用 category=7**（category=8 返回 24 根垃圾）；深度 21600 根 ≈ **90 交易日** | 可用（23780 根 ≈ 99 日） | 可用（约 40 日） | tdxrs 深度接近 Go，需绕开 category 8 |
| `full_minute` 全量分钟 | **不可用（无批量端点）**，5609 次单请求 ≈ **163s/轮**（单连接、且 GIL 挡住多线程） | 无批量端点，估 1.5min（6 并发） | 无批量端点，约 10min | 三者都缺，tdxrs 需多进程补 |
| `financial` 财务 | **不具备**（仅 34/36 快照字段；gpcw 45 指标只在 Rust 侧、需本地 .dat） | 不具备 | 不具备 | 继续回退 TickFlow |
| `get_instruments` 维表 | **不建议实现**（`security_list` 无 limit_up/limit_down/股本） | 不建议 | 不建议 | 三者一致，维表仍走 TickFlow |

**一句话**：tdxrs 把 tdx-api 唯一做不到的 `adj_factor` 补上了，代价是 `full_minute` 仍要自己扛、财务仍要回退。

---

## 1. 项目定位差异（这决定"接口使用方式"）

| 维度 | **tdxrs** | tdx-api (Go) | tdxquant (tdxaidata) |
| --- | --- | --- | --- |
| 形态 | Rust 库 + PyO3 扩展，**pip 装的本地 wheel** | 独立 Go 二进制 + HTTP/JSON 服务 | ctypes 包装的私有 TCP SDK |
| 调用方式 | **Python 进程内直接函数调用**，返回 `list[dict]` / `list[tuple]` / `DataFrame` | Python 侧 httpx 打 `127.0.0.1:8080`，JSON 解包 | Python 进程内 ctypes 调 |
| 多一层进程 | **无** | 有（sidecar，需单独起、单独看日志、启动 56s、时区靠 `TZ=` 环境变量） | 无 |
| 鉴权 | 无 | 无（但服务裸监听，需自己绑 127.0.0.1） | **需要 Key + 积分** |
| 端口/时区耦合 | 无 | 端口硬编码 `:8080`；分钟时间戳依赖进程时区，必须 `TZ=Asia/Shanghai` | 无 |
| 协议细节 | 内置服务器池、握手模式（默认 `skip_cmd3`，实测能供数）、黑名单、空响应自动换服务器重试 | Go 侧已实现服务器池/分页，但改口径要改 Go | 黑盒 DLL |
| 限流 | 内置，连接级：默认 50 rps、日K 15 rps、分时固定 10 rps；交易时段自适应 | 无 | 由服务端积分/频率约束 |
| 复权 | **客户端算**（fq=0/1/2，`calc_fq_factors` 可导出因子） | 无（另有 `/api/kline` 走同花顺前复权，是陷阱） | 服务端/自算事件 |
| 打包 | `pip install tdxrs`，MIT，**可随安装包分发** | **仓库无 LICENSE**，分发前需授权 | 已在后端依赖里 |
| 依赖面 | 零 Python 依赖（pandas 可选） | 多一个二进制 + 端口 | 多一个原生库 |

> **对 TSP 的意义**：tdxhttp 那套"Go sidecar + Python HTTP 客户端"的两层结构可以整体省掉；插件直接 `runtime: python` 调 `tdxrs`，不需要 sidecar 守护、不需要 `TDX_HTTP_URL`、不受进程时区影响。

---

## 2. 接口数量对比

### 2.1 tdxrs 暴露面（Python 侧，按类统计实测）

| 类 | 暴露方法数 | 说明 |
| --- | ---: | --- |
| `TdxHqClient` | **46** | 主力：连接/服务器池/限流/配置(约 20) + 数据接口(约 26) |
| `TdxHqFundClient` | **33** | 基金专用（含静态 `is_fund`/`classify_fund`） |
| `AsyncTdxHqClient` | **32** | tokio 异步版，接口与主客户端对齐 |
| `TdxDirectClient` | **17** | 每请求独立 TCP |
| `TdxSmartClient` | **11** | 自动探活 + 缓存 |
| `TdxBlockClient` | 内嵌于 py_fund/f10 统计 | 板块 K 线/行情/成分 |
| `TdxF10Client` | **13** | **仅源码编译（`--features f10`）**，pip 包不含 |
| Reader 类（4 个） | **26** | 本地 `.day`/`.lc5`/`.dat`/gpcw 解析 |

**数据接口去重后约 20 类**：

K 线（12 周期 × 3 复权 + 自动分页 + tuple/DataFrame）、指数 K 线、批量行情（60/批）、证券列表/数量、分时（当日/历史）、逐笔（当日/历史）、财务快照、除权除息、板块成分（行业/概念/指数成分）、板块 K 线/行情、基金行情/K 线/列表/分时/逐笔/除权/财务、F10 资料（源码版）、4 类本地 Reader、批量下载器、CLI。

### 2.2 tdx-api（Go）暴露面

**31 个 HTTP 端点**，其中：数据类 24、任务/状态/静态页 7。数据类里真正给 TSP 用的是 `/api/quotes` 与 `/api/kline-raw` 两个（其余是给自带网页前端用的、口径不干净的端点）。

### 2.3 tdxquant / tdxhttp 暴露面

| 源 | 数据集数 | 明细 |
| --- | ---: | --- |
| tdxquant | **6** | realtime / depth5 / daily / adj_factor / minute / full_minute |
| tdxhttp | **4** | realtime / depth5 / daily / minute（adj_factor、full_minute 未接） |

### 2.4 数量对比的一句话

> tdx-api 的 24 个数据端点里，TSP 只用 2 个；tdxrs 的 20 类数据接口里，TSP 能直接用 5 类（realtime/depth5/daily/adj_factor/minute），另有 3 类（逐笔、板块成分、分时当日 240 点）是 TSP 目前**根本没有**的能力，属于净增量而不是替换。

---

## 3. 逐能力实测（收盘后 19:10，价格=当日收盘）

### 3.1 实时快照 / 五档

| 项 | 实测 |
| --- | --- |
| 单批上限 | **60 只**（请求 61 只：告警 + 自动截断到 60） |
| 单批延迟 | **29–37 ms** |
| 30 批 / 1800 只 | **1.17 s，覆盖 100%**（连跑两轮均 100%） |
| 全市场 5609 只 | 94 批 ≈ **3.7 s/轮**（单连接顺序） |
| 字段 | `price/last_close/open/high/low`=**元(float)**、`vol`=**手**、`amount`=**元**、`b_vol/s_vol`、`bid1..5/ask1..5`、`bid_vol1..5/ask_vol1..5`、`servertime` |
| 指数 | `(1,"000001")` 价格可用（3584.58），**bid1=376173.22 是垃圾**，五档必须判无效 |
| 退市代码 | `600355/600421` 回包 `code=600839`、`price=0` → **必须做回包代码校验**（与 tdxhttp 同一坑） |

### 3.2 日K

| 项 | 实测 |
| --- | --- |
| `category=4, fq=0` | 原始价正确；`vol`=**股**、`amount`=**元**、`datetime="YYYY-MM-DD"` |
| 单请求 | 5 根 0.04s；800 根上限 |
| 全历史 | `get_security_bars_all(count=8000)` → **6044 根 / 0.59s**（2001-08-27 → 2026-11-17） |
| 复权 | `fq=1/2` 客户端算；最近无除权日时 raw==qfq（已交叉验证） |

### 3.3 1 分钟K（关键差异点）

| category | 结果 |
| --- | --- |
| `8` (`KLINE_1MIN`) | **不可用**：24 根、时间戳全是 `09:30`、仅首根有量（等于当日汇总），且 `start` 翻页全空 —— 实测 3 台服务器（含 `180.153.18.170`、`124.71.187.122`）表现完全一致 |
| `7` (`KLINE_EXHQ_1MIN`) | **可用且干净**：今日 240 根 `09:31 → 15:00`；末根 `vol=171300 股`（=1713 手，与分时末点 1713 手吻合）；`get_security_bars_all(count=24000)` → **21600 根 / 2.33s**，覆盖 2026-07-07 → 2026-11-17（**90 交易日**） |

> 结论：**1 分钟 K 必须固定用 category=7**。category=8 是 tdxrs 或服务端的已知坏路径，文档未标注，踩上去会静默拿到一天的假数据。

### 3.4 分时 / 逐笔（TSP 目前没有的能力）

| 接口 | 实测 |
| --- | --- |
| `get_minute_time_data`（当日 240 点） | 0.04s，`{time:"09:31".."15:00", price, avg_price, vol=手}` |
| `get_history_minute_time_data(date)` | 按日期取历史分时（未逐日实测） |
| `get_transaction_data`（逐笔，当日/历史） | 0.10s/100 笔，`{time, price, vol=手, num, buyorsell}`，单次上限 2000 |

### 3.5 除权除息（原 tdx-api 的硬缺口）

`calc_fq_factors(market, code)` → `{factors:[{date, close_before, qfq_factor, hfq_factor, div_per_share, bonus_ratio, rights_ratio, rights_price}], cumulative_qfq, cumulative_hfq}`

茅台实测 0.42s，**30 个事件**，最近三条：

```
20250626  close_before=1435.86  qfq=0.980727  div_per_share=27.673
20251219  close_before=1431.00  qfq=0.983259  div_per_share=23.957
20260626  close_before=1212.10  qfq=0.976880  div_per_share=28.024
```

- `div_per_share` **已经是"每股"口径**（对应 xdxr 原始 `fenhong=280.24` 每 10 股，÷10 后 = 28.024）
- `qfq_factor/hfq_factor` 是**逐事件**因子，可直接转成 TSP 要的 `[symbol, trade_date, ex_factor]` 序列
- 精度提示：底层 xdxr 字段是 **f32**，`fenhong=280.2423095703125` 这类尾数噪声需要 round 后再用，因子链长历史会累积误差 → 落地要跑一次"TDX 因子复原前复权 vs tdxrs 自带 fq=1"的偏差回归

### 3.6 财务 / 维表 / 板块

| 项 | 实测 |
| --- | --- |
| `get_finance_info` | **36 个字段**，股本 `zongguben=125008.16`（万股）、`jingzichan` 等（未做单位换算）、`updated_date/ipo_date/province/industry` |
| 45 个命名指标 | **Python 未暴露**（`TdxFinanceClient.get_finance_indicators` 只在 Rust 侧，且需本地 `gpcw*.dat`） |
| 三表历史（income/balance/cash_flow/shares） | **不具备** |
| `get_security_list` | 1000 条/页，`{code, name, pre_close, volunit, decimal_point}`；沪 `count=27881`、深 `24715`（含债券基金，需按代码前缀过滤） |
| 板块成分 | `get_industry_blocks()` → 20856 条（板块名+成分股代码）；`get_concept_blocks()` → 41420 条 |
| 板块 K 线 | 需板块指数代码（880xxx 段），传普通股票代码返回空（已踩） |
| 北交所 | `(2,"920826")` 可用；`(2,"832735")` 回包 `600839` 垃圾 |

### 3.7 并发 / GIL（最容易被忽略的工程约束）

| 测试 | 结果 |
| --- | --- |
| 顺序 40 批（1800 只） | 1.17 s（29 ms/批） |
| **8 线程 40 批** | **4.56 s，加速比 1.00×** |
| `AsyncTdxHqClient` 顺序 40 批 | 12.33 s（308 ms/批，比同步慢一个量级） |

> **PyO3 绑定全部是 `&self` 方法，且代码里 `allow_threads` 出现 0 次** → 调用期间**持有 GIL**。
> 后果有两条：
> 1. Python 侧线程池**无法并行**这批请求（实测 1.00× 证实）；
> 2. 在 FastAPI 后端里同步调用会**阻塞整个 Python 侧**（0.03s 的小请求无感，但 1 分钟历史那种 2.3s、全量分钟那种上百秒的调用会卡住实时轮询与接口响应）。
>
> 这不是 tdxrs 的 bug，但没有 `allow_threads` 就等于"进程内直连"的并发红利拿不到。要真正跑满，只有三条路：**给 tdxrs 提交 `py.allow_threads` 补丁**、**TSP 侧多进程**、或**退回 sidecar 模式**。

---

## 4. 补全清单（要接 TSP，必须补什么）

### P0 — 不做则不能上线

| # | 缺口 | 补法 | 验收 |
| --- | --- | --- | --- |
| 1 | **1 分钟 K 用错 category 会静默拿假数据** | provider 固定 `category=7`；断言"当日根数=240、末根时间=15:00"，否则丢弃本轮 | 连续 3 轮根数=240 且末根 OHLC 与分时末点一致 |
| 2 | **GIL 阻塞** | ① 优先给 tdxrs 加 `py.allow_threads`（一行级改动，可提 PR 或本地 fork）；② 过渡期把 TDX 调用收敛到单一专用线程 + 队列，避免和 FastAPI/轮询线程抢 GIL | 8 线程压测加速比 ≥2×（补丁后）；或后端 P99 延迟在同步期间 <200ms（收敛方案） |
| 3 | **退市码回包污染** | 复用 tdxhttp 已验证的"回包 code 校验 + 全量隔离名单"（61 只退市 + 若干 `*ST` 无数据） | 一轮全市场 `list` 无 `code` 不匹配项，日志零 WARNING |
| 4 | **60 只/批 → 94 批/轮** | provider 内 94 批循环 + 批间失败隔离；轮询间隔保持现有 6s | 全市场一轮 ≤5s、覆盖率 ≥99%、单批失败不影响其他批 |
| 5 | **adj_factor 要转成 TSP 的 ex_factor 序列** | 用 `calc_fq_factors` 的 `factors[]`：`ex_factor = qfq_factor`（或按 TSP 现有因子链定义做倒数/累计），`div_per_share` 落明细列；f32 值先 round | 与 TickFlow/fuyao 前复权收盘偏差 <0.1%（抽 30 只长历史股） |

### P1 — 可以后置，但会明显影响体验

| # | 缺口 | 补法 |
| --- | --- | --- |
| 6 | `full_minute` 无批量端点，单连接 163s/轮 | 二选一：① 多进程（4–6 进程各持独立 client，降到 40–60s，注意服务器限流）；② 降级为"当日分时 240 点补洞 + 分钟K 只在修复轮全量"。不建议声明 `full_minute` 直到压测达标 |
| 7 | `financial` 不具备 | 保持不声明 → 自动回退 TickFlow；若要补，只能落地 `gpcw*.dat` 下载 + 45 指标（工作量大，收益低） |
| 8 | `instruments` 缺涨跌停/股本 | 不实现 `get_instruments`（与 tdxhttp 同结论）；仅用 `security_list` 做**名称/前收/每手股数**补全，避免整表替换把 `limit_up` 写成 null |

### P2 — 净增量，TSP 现在没有的能力

| # | 能力 | 落点 |
| --- | --- | --- |
| 9 | 逐笔成交（当日 + 历史按日，2000 笔/次） | 大单/资金流、分笔明细页 |
| 10 | 分时（当日 240 点 + 历史按日） | 分时图（现在只能靠 1 分钟 K 拼） |
| 11 | 板块成分（行业 20856 / 概念 41420） | 板块/题材页，可替代现有多源拼装 |
| 12 | 板块指数 K 线（880xxx） | 板块强度、轮动 |
| 13 | 基金分类 + 基金 K 线/行情 | ETF 页（现在 ETF 走同一套股票接口，缺分类与折溢价基础） |
| 14 | F10 公司资料 | **需自行 `maturin build --features f10`**，pip 包不含 |
| 15 | 本地 `.day/.lc5` 解析 + 批量下载器 | 离线库/回补历史 |

---

## 5. 风险登记

| # | 风险 | 影响 | 处置 |
| --- | --- | --- | --- |
| 1 | **GIL 未释放** | 并发为零、长请求阻塞整个 Python 侧 | 见 P0-2；这是接入前必须决策的第一件事 |
| 2 | **category=8 坏路径**（文档未标注） | 静默拿到 24 根假 1 分钟 K | 固定 category=7 + 根数/末根自检 |
| 3 | 撤单/退市码回包 `600839` | 若不做校验会写出 0 价 | 回包 code 校验 + 隔离名单 |
| 4 | xdxr 字段是 f32 | 因子链长历史累积误差 | round + 偏差回归（<0.1%） |
| 5 | 批量上限 60（Go 是 80） | 请求数 +33%（94 批） | 接受；或与上游沟通把上限放到 80 |
| 6 | 服务器限流（分钟线固定 10 req/s） | 全量分钟更慢 | 多进程也要控速，失败退避 |
| 7 | 第三方服务器可用性（实测有 1 台连不上） | 单台失败 | tdxrs 自带服务器池 + `block_server` 黑名单 |
| 8 | `AsyncTdxHqClient` 比同步慢 10× | 误用会拖慢 | 不用 async 客户端，统一同步 + 多进程 |
| 9 | pip 版本 0.6.10 与仓库源码可能不同步 | 自行改源码需重新 maturin 构建 | 若要打 `allow_threads` 补丁，就 fork + 本地 wheel |

---

## 6. 落地建议

**做插件，不改造现有 tdxhttp。** 两者并存，按能力分流：

```
backend/app/plugins/tdxrs/
├── plugin.yaml          # runtime: python, datasets: [realtime, depth5, daily, adj_factor, minute]
├── __init__.py
├── client.py            # 单例 TdxHqClient + 服务器池 + 限流 + 回包校验 + 退市隔离
├── symbols.py           # 600519.SH ↔ (market, code)；先看后缀再判资产（指数/ETF/北交所）
└── provider.py          # 五个数据集；vol 股→手、amount 元、minute 用 category=7
backend/tests/test_tdxrs_provider.py
```

三条边界，与现存插件保持一致：

- `datasets` 声明 5 项；`adj_factor` **这次可以声明**（tdxhttp 缺的就是这块）
- `financial` / `get_instruments` 不实现 → 自动回退 TickFlow
- `full_minute` 等压测达标再声明，不要一上来就挂上

**先做的一个决策**：GIL 这条是"要不要 fork 上游"的问题，不是编码问题。若不想 fork，就必须接受"TDX 调用串行、放在专用线程"，实时（3.7s/轮）不受影响，但 1 分钟历史同步（2.3s/只×多只）与全量分钟会明显拖慢其他接口。

---

## 7. 复现方式

```bash
# 1) 装（纯 wheel，macOS arm64 / py3.11）
python3.11 -m venv /tmp/tdxrs-probe && /tmp/tdxrs-probe/bin/python -m pip install tdxrs

# 2) 本报告的探针脚本
#    /tmp/tdxrs_probe1.py  连接 + 实时 + 日K + 分钟
#    /tmp/tdxrs_probe2.py  分钟深度 / 分时 / 逐笔 / 除权 / 财务
#    /tmp/tdxrs_probe3.py  category 7 vs 8、多服务器对照、基金客户端
#    /tmp/tdxrs_probe4.py  全市场快照成本、1 分钟深度、指数
#    /tmp/tdxrs_probe5.py  复权因子、北交所、板块
#    /tmp/tdxrs_probe6.py  并发/GIL 测试
#    /tmp/tdxrs_probe7.py  批次稳定性
#    /tmp/tdxrs_probe8.py  当日 240 根自检、退市码回包
TDXRS_LOG=warn /tmp/tdxrs-probe/bin/python /tmp/tdxrs_probe8.py
```
