# 通达信本地 HTTP 服务接入 TSP：可行性分析与改造方案

> 2026-11-17 更新：`tdxhttp`（TDX-HTTP）插件已按需求删除，本地通达信能力改由 `app/plugins/tdxrs`（tdxrs 自建直连）承接；本文仅作历史记录。

> 状态：方案待评审（未实施）
> 实测时间：2026-11-16 09:30–10:00（北京时间，盘中）
> 实测对象：`/film/tdx-api`（Go，来自 injoyai/tdx 协议引擎）本地构建并运行在 `127.0.0.1:8080`
> 对照对象：`backend/app/plugins/tdxquant/`（现行"通达信官方 API"，按积分计费）
> 本文所有数字均为实际跑出来的，不是按文档推断；未验证项已显式标注「待实测」。

---

## 0. 结论摘要

**可以做，但只能"逐能力替换"，不能"整体替换"。**

| TSP 能力（数据集） | 能否用 tdx-api 替代 TdxQuant | 依据 / 条件 |
| --- | --- | --- |
| `realtime` 全市场实时快照 | **可以，且更快** | 实测 5580 只 / 70 批 / 6 并发 = **0.57 秒一轮**（TdxQuant 约 10 批 30–60 秒） |
| `depth5` 五档盘口 | **可以** | 快照自带买卖五档（`BuyLevel`/`SellLevel`），量已是「手」 |
| `daily` 日K（原始价） | **可以** | 单标的 800 根一次请求约 0.10s；深度可回溯到上市首日（茅台 6043 根） |
| `minute` 分钟K | **可以，且更深** | 1m 上限 24000 根 ≈ **99 个交易日**（TdxQuant 约 40 日；TickFlow 基准更深） |
| `full_minute` 全量分钟 | **可以，但仍是逐标的** | 无批量端点，全市场一轮 ≈ 5580 次请求；按实测单请求 0.10s + 6 并发估算 **~1.5 分钟/轮**（优于 TdxQuant 的 10 分钟量级） |
| `adj_factor` 除权因子 | **不能**（当前缺口） | 无「除权除息」端点；用 THS 前复权比值反推**实测失败**（茅台早期前复权价为负，比值失真）。需在 Go 侧补协议帧，或保留现有源 |
| `financial` 财务 | 不可以 | 未接入，继续走 TickFlow |
| 标的维表 | 部分可以 | `/api/codes` 有代码+名称，但**没有涨跌停价/股本**；实现 `get_instruments` 会把 `limit_up/limit_down` 覆盖成 null，破坏涨跌停/连板监控 → **建议暂不实现** |

推荐落地方案：**Go 服务做本地 sidecar（TSP 拉起或用户自启）+ 新增 Python 插件 `tdxhttp`**，
Go 侧补 3 个"干净口径"端点并修 3 个已知 bug，Python 侧只做符号映射与单位归一。
不建议在 Python 里重写通达信协议（见 §4 方案 B）。

---

## 1. 现状

### 1.1 现行 TdxQuant 插件（要替代的东西）

`backend/app/plugins/tdxquant/`，`runtime: python`，依赖 `tdxaidata`（ctypes 包装的私有 TCP 库）：

- 能力：`realtime / depth5 / daily / adj_factor / minute / full_minute`（未声明 `financial` → 自动回退 TickFlow）
- 计费：`billing: points`，按积分扣；`api_key_env: TDXQUANT_API_KEY`，需在通达信商城创建 Key
- 实时：600 只/批，全市场约 10 批
- 分钟：逐标的 count 模式，240 根/日，深度约 40 交易日
- 全量分钟：无批量端点，全市场一轮「10 分钟量级」
- 除权因子：`get_divid_factors` 事件（每 10 股口径）+ 本地日K配价，按交易所公式推导单事件比值
- 隐患：SDK 1.2.2 会把"服务端失败"和"正常无事件"都转成空 DataFrame（插件只能靠另一接口判成败）

**要替代它的动机**（从工程角度看是三点）：免 Key、免积分、少一层原生依赖（ctypes 库是黑盒，出问题只能看日志）。

### 1.2 `/film/tdx-api` 是什么

不是"另一个数据 API 供应商"，而是**通达信免费行情协议（TCP 二进制）的 Go 实现 + 一层 HTTP/JSON 封装**：

- `protocol/`：帧编解码（`0x052D` K线、`0x003E` 报价、`0x0F` 除权除息常量已定义但**未实现**、指数 K线、分时、成交明细、代码表）
- `client.go`：`GetQuote`（单帧最多 80 只）、`GetKline(Type, code, start, count)`（单帧 ≤800 根）、`GetIndex`、`GetKlineMinute` 等
- `manage.go / hosts.go`：多服务器地址池、连接数
- `web/`：完整 HTTP 服务 + JS 前端 + 定时任务（`tasks/pull-kline`）。**这就是我们要对接的那一层**
- 无 LICENSE 文件（默认版权保留），若要把二进制随 TSP 安装包分发，需先确认授权（§7）

**关键事实**：它读的是通达信公开行情服务器的原始数据，没有复权、没有财务、没有除权事件。三个必须知道的实现细节：

1. `/api/kline?type=day` 走的是**同花顺前复权**（`getQfqKlineDay`，外部依赖），不是 TDX 原始价；
   只有 `/api/kline-all/tdx` / `/api/kline-raw` 这类"tdx"端点才是原始价
2. `type=week|month` 也是拿前复权日K后"**每 5 根/每月合并**"的简化实现（实测 week 1290 根 vs 原始 1273 根，
   节假日与停牌周会错位）→ 周/月线应由 TSP 侧自己聚合原始日K，**不要用这两个端点**
3. `/api/kline?type=minute1` 是不带 count 的全量口径：实测 1.38s / 23780 根（内部 30 次 800 分页），
   只为了取今天最新 3 根却要拉 99 天 → 必须换成带 `count` 的端点

### 1.3 TSP 的插件契约（决定改造边界）

来自 `docs/plugin-development.md`，与本方案直接相关的红线：

- **能力 = 数据集级**：声明 `realtime` 就等于接管"整个全市场轮询周期（含指数与 ETF）"，不能只覆盖 A 股股票
- **代码格式**：`600519.SH / 000001.SZ / 300750.SZ`，指数同格式
- **单位制**：`volume` 一律「手」；`amount` 元；`change_pct` 小数制；日K 必须**不复权原始价**
- **分钟 `datetime` 必须是北京墙钟 naive**；`amount` 拿不到就 `null`，不许伪造
- **软失败语义**：`get_realtime` 失败返 `[]` 不抛；`get_realtime_indices` 失败返 `None`
- **不许跨源回退**，不许"小于 1 就乘 100"这类启发式补数
- 插件声明 `datasets` 后，能力矩阵/设置页自动出现该源，**后端与前端都不用改注册表**

---

## 2. 实测基线

方法：`go build ./web`（Go 1.25，模块下载后编译通过）→ 本地启动 → 逐端点抓数并与"成交额 ÷ (成交量 × 100) ≈ 均价"、
"`/api/kline` 前复权 vs `/api/kline-all/tdx` 原始价"等交叉校验。

### 2.1 能力与延迟（盘中实测）

| 端点 | 实测 | 备注 |
| --- | --- | --- |
| `/api/quote?code=` （GET，逗号分隔） | **单帧硬上限 80 只**；80 只 ≈ 34ms；>80 报 `预期200个，实际80个` | 服务端的 `/api/batch-quote` 反而把上限压到 **50**（不必要） |
| 全市场快照（5580 只 / 70 批 / 6 并发） | **0.57s、0.59s（连跑两轮全成功）** | 完全打得进 6s 轮询节奏 |
| `/api/kline-all/tdx?code=`（全历史原始日K） | 0.37s（6043 根） | 内部按 800 根分页拼接，长历史标的会被放大多倍请求 |
| `/api/kline-history?type=minute1&limit=800` | 0.10s（800 根 = 3.3 交易日） | 原始价、含 OHLC |
| `/api/kline?type=minute1`（全量口径） | **1.38s / 23780 根** | 内部 30 次分页；只为 3 根最新数据太浪费 → 需 count 端点 |
| `/api/index?code=sh000001&type=day&limit=800` | 0.08s | 指数原始日K |
| `/api/index?code=sh000001&type=minute1` | 可用，800 根 | 指数分钟也能取 |
| `/api/codes?exchange=sh` | 2320 只（带名称） | 三市场股票合计 5580 |
| `/api/etf-codes` | 2276 只（仅代码） | 名称需另取 |
| `/api/market-count` | 52952（含债券/基金等全部证券） | 不是"股票数" |

### 2.2 单位口径（实测确认，这是最容易踩的坑）

| 数据 | 实测值 | 结论 |
| --- | --- | --- |
| 报价 `K.Open/High/Low/Close/Last`（股票） | 600519：`Close=1433560` | **厘**，÷1000 = 1433.56 元 ✓ |
| 报价 `K.*`（ETF） | 510300：`Close=405`（K线同日 `Close=4050`） | **÷100**！ETF 的 K 字段被 `/api/quote` 里 `adjustQuotePrice` 额外 `/10`，与 K线端点**口径不一致** |
| 报价买卖五档 `Price` | 510300：买一 `4049` | **厘**，÷1000 = 4.049 元 ✓（五档没被 /10，所以和 K 字段差 10 倍） |
| 报价 `TotalHand` | 000001：162860 | **手** ✓（`volume=162860手`、`amount=1.968亿` → 均价 12.08 ≈ 现价 12.07） |
| 报价 `Amount` | 000001：196809984 | **元**（`API_接口文档.md` 标注"厘"，与实测不符，以实测为准） |
| 日K `Volume` | 600519 11-13：41779 手；ETF 510300 11-13：8510502 手 | **手** ✓ |
| 日K `Amount` | 600519 11-13：5954833920000 | **厘**，÷1000 = 59.5 亿元 ✓（对得上 volume×均价） |
| 分钟 `Volume` | 600519 09:31：1692 手（amount 2.05 亿 → 均价 1211 ✓） | **手** ✓（源码里分钟已 `/100`） |
| 指数 `Volume` | 上证 11-16 盘中：97449500 | **手** ✓（974亿股 × 均价 15 元 ≈ 1482 亿成交额） |
| 时间戳 | 日K `2026-11-13T15:00:00+08:00`；分钟 `09:31` | 取决于**服务进程时区**：东八区机器直接是北京墙钟；UTC 机器会整体偏 8 小时 → 必须固定 `TZ=Asia/Shanghai` |
| 报价时间 | `ServerTime: "9382090"`（非 epoch，格式不确定） | **不可用于时间戳**，provider 只能退本地时间（`quote_freshness` 会因此依赖本地时钟，见 §7） |
| 同码不同所 | `000001.SH`（上证指数，`sh000001` 可用）与 `000001.SZ`（平安银行） | 映射**必须先看后缀**：按代码判"是否指数"会把平安银行误路由到 `/api/index/all`，实测直接超时 |

### 2.3 自检脚本与实测结果

`backend/scripts/probe_tdx_http.py` 把上面这张表落成可执行检查（价格/K线一致、`amount÷(volume×100)` 与价格中枢、
分钟北京墙钟、ETF 口径比）。盘中一次实测（2026-11-16 09:49）：

```
600519.SH  PASS  quote==kline close 1431.270     000001.SZ  PASS  quote==kline close 12.150
300750.SZ  PASS  quote==kline close 236.300      510300.SH  PASS  quote==kline close 4.050
510300.SH  WARN  ETF 报价口径比 kline=0.100 (现状 /10 bug)      000001.SH  PASS  close 3598.568
合计: 20 项, FAIL 0 项
```

只有那条 WARN 是已知 bug（§5.2 修复项 4），其余全部 PASS —— 也就是说**股票/ETF/指数的价、量、额、时区四项口径都对得上**，
唯一需要下游补偿的是 ETF 报价的 10 倍差。


### 2.4 数据深度

- 日K：`/api/kline-all/tdx` 从上市首日到**当天**（含当日盘中未收盘 bar，`Time` 仍是 15:00）
- 1m：上限 24000 根（~99 交易日）；本机实测 23780 根，从 2026-06-22 到当前
- 周/月：`/api/kline-all?type=week` 1273 根 ✓

---

## 3. 缺口清单（逐能力看"能不能真的替换"）

### 3.1 可以替代，但要补"干净端点"

现有 HTTP 层是给人看网页用的，口径不干净：ETF 报价被 `/10`、批量被砍到 50、没有 count 参数（取 800 根也得先把全历史拉完）。
所以**必须加一层面向程序的端点**，而不是让 Python 端去"猜"这些差异。

### 3.2 `adj_factor`：硬缺口（**这是"完整替代"的唯一阻塞项**）

- tdx-api **没有**除权除息接口（`protocol/const.go` 里 `TypeXdxr` 常量在，`client` 无实现）
- 尝试过的替代路径：用 `/api/kline-all/ths`（同花顺前复权）÷ `/api/kline-all/tdx`（原始）反推逐日比值
  → **实测失败**：茅台 6042 根里有 5515 个不同比值，且出现 `-15.13`、`-15.05` 这类负值
  （高分红股前复权早期价可为负，比值失去意义），无法产出可信 `ex_factor`
- 也验证过"日K 的 `Last` 是否等于除权参考价"：600519 / 000001 / 300750 全历史 **0 处不匹配**
  → `Last` 就是原始昨收，推不出因子
- 结论：要么 **Go 侧新增除权除息帧（`0x000f`）**，要么 **`adj_factor` 继续路由到现有源**（fuyao / TickFlow / TdxQuant）

> 影响面要说清楚：`daily` 换成 TDX 而 `adj_factor` 留在别家，是**允许的**（能力独立路由），
> 但必须回归一次"TDX 原始价 × 他家事件链 = 前复权价"的偏差（建议容忍 <0.1%），
> 因为两家的昨收基价不是同一根序列时，因子链会对不上。

### 3.3 `full_minute`：能跑，但没有批量端点

通达信协议是"一标的一请求"，没有"一次拿全市场"的接口。可行性依赖并发能力：

- 单请求 800 根 ≈ 0.10s；只要最新 3 根（`count=3`）应更快（待实测）
- 5580 只 ÷ 6 并发 × 0.10s ≈ **1.5 分钟/轮**（对比 TdxQuant 10 分钟量级）
- 代价：**每分钟 ~5580 次请求**发向通达信公共服务器，被限流/封 IP 的风险非零（§7）

### 3.4 标的维表：不要实现 `get_instruments`

`/api/codes` 只有 `code/name/exchange`，没有 `limit_up/limit_down/total_shares/float_shares`。
TSP 的 `instrument_sync._flatten_instruments` 只要 provider 返回行，就会**整表替换**，
缺列写 null → 涨跌停价、连板梯队、封单等依赖 `limit_up` 的功能静默失效。

TdxQuant 同样没实现 `get_instruments`（也靠 TickFlow 维表），所以**保持不实现即与现状等价**。
若未来要替代 TickFlow 维表，涨跌停价必须显式计算（主板 ±10% / 创业科创 ±20% / 北交所 ±30% / ST ±5%，
规则会漂移，属于另一议题）。

### 3.5 `financial`：不具备，保持回退

---

## 4. 三个方案

| | A. Go sidecar + Python 插件（推荐） | B. Python 内嵌 pytdx | C. 最小替换（只 realtime+depth5+daily） |
| --- | --- | --- | --- |
| 协议风险 | 由 Go 侧承担（已有 800 分页/多服务器/重连） | 自己承担：服务器地址池、协议版本、限流 | 同 A |
| 依赖 | 多一个本地进程（可随桌面版拉起） | 纯 Python，无外部进程 | 同 A |
| 打包 | 需把 Go 二进制塞进 `tsp.spec` / `tsp.iss` | 简单 | 同 A |
| 分钟深度/全量分钟 | 支持 | 支持（要自己实现分页与并发） | 不支持 |
| 除权因子 | Go 侧补帧可解 | pytdx 有 `get_xdxr_info`，可解 | 不解，继续用现有源 |
| 维护 | 上游 Go 项目 + 我们的小补丁 | pytdx 多年未更新 | 同 A |
| 工作量 | 中（Go 3 端点 + Python 插件 + 打包） | 高（等于重写协议层） | 低 |

**选 A**。理由：Go 侧已解决"协议 + 服务器池 + 分页"这些真正脏的活；我们只补"给程序用"的干净端点。
B 的问题不是技术不可行，而是把协议维护成本搬回 TSP 仓库，且要自己维护服务器清单。

---

## 5. 推荐方案详解

### 5.1 拓扑

```
TSP backend (Python, httpx)
   │  http://127.0.0.1:18080  (仅本机回环; 服务默认 :8080, 建议改用 18080 避开本地开发端口)
   ▼
tdx-api Go 服务（新增"程序端点"，去掉给网页看的口径修正）
   │  TCP 二进制协议，连接池 / 多服务器
   ▼
通达信公共行情服务器（免费、无 Key）
```

约束：

- 只监听 `127.0.0.1`，不要 `0.0.0.0`（该服务无鉴权）
- 服务时区固定 `TZ=Asia/Shanghai`
- 端口可配（`TDX_HTTP_PORT`），TSP 侧用 `TDX_HTTP_URL` 指向它
- 单实例：启动前探活，已存活则复用，避免用户重复双击起两个

### 5.2 Go 侧改造清单

**新增（面向程序，口径干净）**

1. `GET /api/quotes?codes=600519,sz399001,sh510300`（≤80）
   - 全部价格字段返回**原始厘**（不做 `/10`、不对 ETF 特殊处理），量返回**手**，额返回**元**
   - 返回 `symbol`（带 sh/sz/bj 前缀）+ 五档 + `received_at`
   - **校验回包**：`resp.Code != 请求code` 或全零 → 视为无数据并报错（见 §7 静默错误案例）
2. `GET /api/kline-raw?code=&type=day|minute1&start=0&count=800&kind=stock|index`
   - 直接走 `client.GetKline(Type, code, start, count)` / `client.GetIndex(...)`，**不做全历史拼接**
   - 原始价；返回 `meta.end_offset` 供 Python 侧翻页（达 800 根上限时）
   - 不提供 week/month（真周月线由调用方按交易日聚合，避免"每 5 根合并"的错位）
3. `GET /api/xdxr?code=`（P3 可选，除权除息事件）
   - 帧 `Type=0x000f`，`market(1B)+code(6B)+?`，响应为「日期 + 类别 + 分红/配股/送转/配股价」记录
   - 协议细节需以 pytdx `get_xdxr_info` 为参照逐字节验证（**未在本仓库实现，属待办**）

**修复（现有 bug，不改下游就会踩）**

> 落地取舍：以下 4 项**全部在新端点内实现**，不改动 `/api/quote`、`/api/kline` 的既有行为——
> 那两个端点仍被该项目自带网页前端（`web/static/app.js`）使用，直接改口径会砸掉它的图表。
> 目标端点为 TSP 提供干净数据即可，旧端点的历史行为保留。

4. `adjustQuotePrice`（`web/server.go:670`）：ETF/`Decimal==3` 的 K 价格 `/10` 导致**报价口径（0.01 元）与 K线口径（厘）差 10 倍**。
   → `/api/quotes` 不做该修正，统一按厘输出；旧 `/api/quote` 保持不变。
5. 批量上限不一致：`/api/batch-quote` 卡在 50，协议实际支持 80。→ `/api/quotes` 统一 80，超过直接报错不截断。
6. 无效代码静默返回垃圾：`/api/quote?code=bj430047`（旧北交所代码）返回 `Code:"600839"`、全零 K 而不报错。
   → `/api/quotes` 校验回包 code 与全零，命中则进 `skipped`（不返回假数据）；TSP 侧另有防御。
7. 时区：`protocol.GetTime` 用进程本地时区 → `/api/kline-raw` 在 handler 内显式转 `+08:00`
   （不改 `protocol` 全局，避免影响其它调用方）。

### 5.3 Python 侧插件

```
backend/app/plugins/tdxhttp/
├── plugin.yaml          # name: tdxhttp, runtime: none, datasets: [...]
├── __init__.py
├── client.py            # httpx 客户端: base_url、超时、信封解包(code:-1 → 异常)、重试、分块并发
├── symbols.py           # 600519.SH <-> sh600519；指数 000001.SH -> sh000001；ETF；BJ
└── provider.py          # TdxHttpProvider: 各 get_* 与单位归一、软失败、test_dataset
backend/tests/test_tdxhttp_provider.py
```

要点：

- `runtime: none`（纯 HTTP，无新依赖），`check/availability` = 探活 `/api/server-status`
- **不声明 `api_key_env`**：这个输入框的文案是"申请 API Key"，把 URL 塞进去语义错。
  一期用环境变量 `TDX_HTTP_URL`（默认 `http://127.0.0.1:18080`）；
  二期若要在设置页填地址，加一个通用 `base_url_env` 清单字段 + 前端一个输入框（小改，见 §6 P3）
- 代码映射走白名单规则，不靠猜：**先看后缀再判资产类型**（`000001.SH`=上证指数、`000001.SZ`=平安银行）；
  `6xxxxx→sh`、`0/3→sz`、`92/8/43→bj`、`SH+000001/000300/000905/000852/000016/000688→sh`、
  `399xxx→sz`、`510/511/512/513/515→sh`、`159/16x→sz`；映射不到的标的**记 warning 并跳过**，不静默拉错
- ETF（`Decimal==3`）报价按实测把 K 字段 `/100`、五档 `/1000` 分开处理；
  更稳的做法是等 Go 侧 bug 修完（统一厘）后一律 `/1000`，并在 provider 里保留"口径自检"
- 每个数据集都带**口径自检**：`amount ÷ (volume×100)` 与 `(high+low)/2` 偏差 > 20% 就告警（不自动修正）

### 5.4 字段映射表（实现时逐行对）

**realtime（`/api/quotes`）**

| TSP 字段 | 来源 | 换算 |
| --- | --- | --- |
| `symbol` | `sh600519` | `600519.SH` |
| `last_price` / `prev_close` / `open` / `high` / `low` | `K.Last/…` | ÷1000（ETF 修 bug 后同） |
| `volume` | `TotalHand` | 原值（手） |
| `amount` | `Amount` | 原值（元） |
| `change_amount` | `last - prev_close` | 元 |
| `change_pct` | `change_amount / prev_close` | **小数制**，不乘 100 |
| `timestamp` | 无可靠服务端时间 | 本地时间 ms；`ServerTime` 格式未确认（`"9382090"`）不可用 |
| `name` | `None` | 由 TSP 维表关联（与 TdxQuant 一致） |
| `turnover_rate` / `amplitude` | `None` | 不伪造 |

**depth5（同一快照）**

| 字段 | 来源 | 换算 |
| --- | --- | --- |
| `bid_prices` / `ask_prices` | `BuyLevel[].Price` / `SellLevel[].Price` | ÷1000 |
| `bid_volumes` / `ask_volumes` | `BuyLevel[].Number` / `SellLevel[].Number` | 原值（手，实测 1 手 = 100 股） |
| `timestamp` | 同 realtime | ms |

**daily（`/api/kline-raw?type=day`）**

| TSP | 来源 | 换算 |
| --- | --- | --- |
| `date` | `Time` | 取**日期部分**（`15:00+08:00`），不做时区再换算 |
| `open/high/low/close` | 同名字段 | ÷1000，**原始价**（禁止用 `/api/kline` 的 qfq） |
| `volume` | `Volume` | 原值（手） |
| `amount` | `Amount` | ÷1000（厘→元） |

> 当日盘中 bar：`kline-all` 会返回**今天未收盘**的一根（`Time` 仍是 `15:00`）。
> 与现状一致（其他源也这样），盘中同步由实时覆写；若要更严，可在 `market_time` 判定交易中时丢弃当日行（列为选项，不默认改）。

**minute（`/api/kline-raw?type=minute1`）**

| TSP | 来源 | 换算 |
| --- | --- | --- |
| `datetime` | `Time` | **北京墙钟 naive**（`09:31:00`），1m 首根 09:31、末根 15:00，240 根/日 |
| OHLC | 同名字段 | ÷1000 |
| `volume` | `Volume` | 原值（手） |
| `amount` | `Amount` | ÷1000（厘→元） |

**指数**：`000001.SH→sh000001`、`399001.SZ→sz399001`、`399006.SZ→sz399006`（实测均可用；
TdxQuant 用的 `999999.SH` 别名在 tdx-api 上**不可用**，会报"代码长度错误"）。

### 5.5 `plugin.yaml` 草案

```yaml
name: tdxhttp
display_name: "通达信本地服务 (TDX-HTTP)"
runtime: none
entry: app.plugins.tdxhttp.provider:TdxHttpProvider
check: app.plugins.tdxhttp.provider:availability
datasets: [realtime, depth5, daily, minute, full_minute]
# 说明:
#  - adj_factor 暂不声明(需 Go 侧补 0x000f 除权帧), financial 不具备 → 自动回退 TickFlow
#  - 不声明 api_key_env: 本方案无需 Key, 服务地址走 TDX_HTTP_URL
description: |
  ## 接入说明
  连接本机运行的 `tdx-api` HTTP 服务(通达信免费行情协议, 无需 Key / 积分)。
  需先启动服务并保证 TZ=Asia/Shanghai:
  `TDX_HTTP_PORT=18080 ./tdx-api`，TSP 侧配置 `TDX_HTTP_URL=http://127.0.0.1:18080`。
usage_notes:
  - "实时快照: 80 只/批、6 并发，全市场一轮实测约 0.6 秒"
  - "五档盘口: 随快照返回，不产生额外请求"
  - "日K: 原始价，单标的一次请求最多 800 根，按 start 翻页"
  - "分钟K: 1m 深度约 99 交易日(24000 根上限)"
  - "全量分钟: 无批量端点，全市场一轮约 1.5 分钟(逐标的)"
install_hint: "无需额外依赖；请先启动本机 tdx-api 服务(TDX_HTTP_URL)"
homepage: "https://github.com/injoyai/tdx"
```

### 5.6 启动、配置与打包

- **手动模式**：用户自行跑 `tdx-api`，TSP 里 `TDX_HTTP_URL` 指过去；`availability()` 探活失败时卡片灰显并提示原因
- **桌面版（推荐）**：`desktop.py` 在起 uvicorn 之前 spawn 子进程（参考现有 TdxQuant 探测 worker 的"重执行自身 + 哨兵"写法，但这里是 Go 二进制）：
  - 先探 `/api/server-status`，已存活则复用
  - 未存活则 `subprocess.Popen`，环境注入 `TZ=Asia/Shanghai`、`TDX_HTTP_PORT`
  - 退出时 terminate；崩溃 3 次熔断并提示（不要让轮询线程反复踩死服务）
  - `packaging/tsp.spec` 加 `binaries`（macOS arm64 / Windows x64 各一份）；`packaging/tsp.iss` 打进安装目录
- **Docker/服务端模式**：sidecar 容器或用宿主进程；注意容器里 `network_mode` 与回环地址

### 5.7 失败语义

| 场景 | 行为 |
| --- | --- |
| 服务未启动/端口不通 | `availability` = 不可用；实时轮询软失败（返回 `[]` + warning），不阻断线程 |
| 单批报价失败 | 只丢该批，其他批照常（与 TdxQuant 的批间隔离一致） |
| 回包 code 与请求不符 / 全零 | 视为该标的无数据，**记 warning 并计入本轮缺失（日K任务计入 `zero_row_out`）**，不写入 0 价 |
| 日K/分钟请求失败 | 抛 `ProviderRequestError`，任务标记失败，不跨源重试 |
| 时区不合约 | 交给 `kline_sync._enforce_minute_beijing_wallclock` 兜底，但 provider 侧先写对 |

---

## 6. 实施路线

**P0 口径验证（0.5 天，已部分完成）**
把本文 §2.2 的单位表固化成可执行校验脚本（见 `backend/scripts/probe_tdx_http.py`，已可运行），
对 5 个代表标的（600519 / 000001.SZ / 300750 / 510300 / 000001.SH）逐字段自检。
验收：全部字段"amount÷(volume×100)≈均价"通过；ETF 价格与 K线端点一致（修 bug 后）。

**P1 Go 侧干净端点（1–2 天）**
新增 `/api/quotes`、`/api/kline-raw`；修 `adjustQuotePrice`、批量上限、回包校验、时区。
验收：同一标的 `/api/quotes` 与 `/api/kline-raw?type=day` 的收盘价一致（修完 ETF 后含 510300）；
单批 80 只全成功；无效代码返回错误码而不是垃圾数据。

**P2 Python 插件（2–3 天）**
`realtime + depth5 + daily + minute` 四个能力 + `test_dataset` + 契约测试（假 client 注入，不联网）。
验收：`pytest tests/test_tdxhttp_provider.py`、`ruff check` 通过；
设置页试拉四能力有数据；切到 tdxhttp 后 Dashboard 自选行情、五档、日K、分时都正常。

**P3 全量分钟与因子（2–3 天，可分开）**
- `full_minute`：`get_intraday_batch`（当日窗口）+ `get_intraday_latest`（`count=3`）。
  验收：盘中一轮覆盖率 > 99%，连续 3 轮不出现空轮自愈循环；观察 30 分钟无线程堆积
- `adj_factor`：Go 侧 `0x000f` 帧 + provider 事件解析（每 10 股口径 ÷10 → 每股，交易所公式，
  涨跌停自检）；拿不到就维持路由到现有源，**不要用"前复权比值反推"那条路**
- 打包与守护（sidecar、双平台二进制、崩溃熔断）

---

## 7. 风险登记

| # | 风险 | 影响 | 处置 |
| --- | --- | --- | --- |
| 1 | **无 LICENSE**（`/film/tdx-api` 仓库里没有授权文件） | 随安装包分发二进制可能侵权 | 自用不受影响；要分发必须先拿到授权或改为"用户自备/自行构建" |
| 2 | 通达信公共服务器限流 / 封 IP / 改协议 | 实时与全量分钟中断 | 全市场一轮 5580 次请求的节奏要留余量（建议轮询 15–30s）；失败退避 + 多服务器池；把"单轮失败率"暴露到状态面板 |
| 3 | 静默错误（本次实测踩到 2 个：ETF 价格 /10、旧北交所代码返回他人代码空数据） | 看起来正常、数值全错 | 端点层做"回包 code 校验 + 全零判定"；provider 层做 `amount/volume` 交叉自检并告警 |
| 4 | 报价无可靠时间戳（`ServerTime` 格式未确认） | `quote_freshness` 依赖本地时钟；非交易时段可能被标 `live` | 一期接受（时区/时段判断已有：休盘→historical）；二期若能解析 `ServerTime` 再补 |
| 5 | 当日未收盘 bar 出现在日K | 盘中手动同步会把半根收盘价固化成收盘 | 与现状一致；需要时按 `market_time` 丢弃当日行 |
| 6 | ETF 与股票价格口径差 10 倍 | 前复权/涨跌幅全错 | 见 §5.2 修复项 4；provider 保留 `Decimal` 分支兜底 |
| 7 | 北交所代码段迁移（旧 430/83x → 92xxxx） | 旧码静默返回垃圾 | 端点校验 + 维表对齐检查（`get_instruments` 不做，维表仍来自 TickFlow，需核对一次 BJ 覆盖） |
| 8 | `adj_factor` 跨源 | 前复权价与 fuyao 口径偏差 | 回归测试：同一批标的前复权收盘偏差 < 0.1%，否则强制同源 |
| 9 | 分钟时间戳依赖进程时区 | 分时图整体偏移 8 小时 | 固定 `TZ`；不建议依赖入口守卫 |
| 10 | 误用 `/api/kline`（同花顺前复权）当日K | 混入已复权价格，与本地 raw 基准冲突，前复权翻车 | 只用 `tdx` 原始端点；provider 里显式断言响应 `meta.source == "tdx"` |
| 11 | 误用 `type=week/month`（每 5 根合并） | 周/月线与真实周线错位（实测 1290 vs 1273） | 不用该端点，周/月由 TSP 聚合原始日K |
| 12 | 瞬时高频请求触发限流 | 单轮失败、覆盖率下降 | 分批并发上限（实测 6 并发稳定）、失败退避、单轮失败率进状态面板 |

---

## 8. 测试矩阵（对齐 CONTRIBUTING §9）

| 类别 | 用例 |
| --- | --- |
| 单位映射 | 厘→元、厘→元(amount)、手直传；ETF `Decimal==3` 分支；指数 Volume 不额外缩放 |
| 代码映射 | 六类前缀规则 + 指数别名；未知代码记 warning 不抛；`bj430047` 这类旧码被判无数据 |
| 分块/并发 | 80 只/批、5580 只 70 批；单批失败隔离；并发上限可配 |
| 翻页 | `count=800` 打满时按 `end_offset` 继续；页数上限；空页终止 |
| 软失败 | `/api/quotes` 500/超时 → `[]`；`get_realtime_indices` 失败 → `None` |
| 响应变体 | 信封 `code:-1` + message；字段缺失（`Count:0`、`List:null`）；整页 schema 变化要告警 |
| 时区 | 带 `+08:00` 的 `Time` → naive 北京墙钟；UTC 机器输出必须仍为 09:31 |
| 能力声明 | 未声明 `adj_factor`/`financial` 时 `provider_has_dataset` 为 False |
| loader | `plugin.yaml` 解析后注册；`availability` 不可用时灰显且不注册 |
| 真实网络 smoke（不进 CI） | 单跑一次 §6 P0 校验脚本 |

---

## 9. 附：复现方式

```bash
# 1) 构建（Go 1.25 已可用；模块会自动下载）
cd /film/tdx-api/web && go build -o /tmp/tdxapi-server .

# 2) 启动（时区必须固定）
TZ=Asia/Shanghai /tmp/tdxapi-server   # 默认 :8080

# 3) 口径自检（脚本只用标准库，`python3` 或 `uv run python` 均可）
cd /path/to/tsp/backend
python3 scripts/probe_tdx_http.py --base http://127.0.0.1:8080 \
  --symbols 600519.SH,000001.SZ,300750.SZ,510300.SH,000001.SH

# 4) 全市场一轮计时（可选）
curl -s "http://127.0.0.1:8080/api/stock-codes?prefix=false&limit=99999" >/tmp/codes.json
```

实测记录：全市场 5580 只、70 批、6 并发，两轮分别 0.59s / 0.57s，全部 `code:0`。

---

## 10. 实施记录（P1 + P2 已落地）

### 10.1 Go 侧（`/film/tdx-api`）

| 文件 | 变更 |
| --- | --- |
| `web/server_tsp.go` | 新增。`handleTSPQuotes`（`/api/quotes`）、`handleTSPKlineRaw`（`/api/kline-raw`）、回包校验、厘/手/元口径、`+08:00` 固定时区 |
| `web/server.go` | 仅在 `main()` 注册两条路由（+2 行），未改动任何既有 handler |
| `web/static/tsp-preview.html` | 新增。TSP 接入预览页（自检 + 报价/五档 + 日K/分钟图），无外部依赖 |

端点契约：

- `/api/quotes?codes=600519,000001,sh510300,sh000001`（≤80，前缀 `sh/sz/bj` 可选）
  返回 `list[]`（`last/prev_close/open/high/low` 为**厘**、`volume` 手、`amount` 元、`bid/ask_prices` 厘、`decimal` 2/3）
  + `skipped[]`（回包代码不符 / 行情全零）+ `data_date` / `received_at` / `elapsed_ms`
- `/api/kline-raw?code=&type=day|minute1&start=0&count=800&kind=stock|index`
  返回 `list[]`（同口径 + `amount` 已转元 + `time` 为 RFC3339 `+08:00`）+ `end_offset` / `has_more` / `source=tdx`

稳定性说明：`go build` 通过；`gofmt` 无差异。服务端口仍是硬编码 `:8080`（本次未改），
因此插件默认 `TDX_HTTP_URL=http://127.0.0.1:8080`。

### 10.2 Python 侧（TSP）

| 文件 | 说明 |
| --- | --- |
| `backend/app/plugins/tdxhttp/plugin.yaml` | `runtime: none`；`datasets: [realtime, depth5, daily, minute]`；**不声明 `api_key_env`** |
| `backend/app/plugins/tdxhttp/client.py` | 信封解包、80 只分批、`/api/kline-raw` 窗口、股票池缓存（12h）、探活 |
| `backend/app/plugins/tdxhttp/symbols.py` | `600519.SH ↔ sh600519`；先看后缀再判资产类型（`000001.SH` 指数 / `000001.SZ` 平安银行） |
| `backend/app/plugins/tdxhttp/provider.py` | 四个数据集 + `test_dataset` + `availability`；失败抛 `ProviderRequestError`，实时软失败返 `[]` |
| `backend/tests/test_tdxhttp_provider.py` | 27 项，`httpx.MockTransport` 假服务，不联网 |

口径落地：厘→元 ÷1000；`volume` 手透传；`amount` 元；`change_pct` 小数制；
日K 取 `time` 的日期部分（自带 `+08:00`，不做二次时区换算）；分钟 `datetime` 剥离为北京墙钟 naive；
指数不进 `depth5`（服务端五档位置是无效字节）。

### 10.3 验收实测（2026-11-16 盘中，服务 `:8080`）

| 项 | 结果 |
| --- | --- |
| `availability()` | `(True, 'ok')` |
| 指定标的报价（3 只） | 0.04s |
| 日K（2 只 / 20 日） | 0.22s，30 行 |
| 分钟K（1 只 / 2 日） | 0.10s，225 行，末根 `2026-11-16 14:45` naive |
| 五档 | 仅 `600519.SH`，指数被跳过 |
| **ETF 口径回归** | `510300.SH` 报价 `4.04` = 日K 收盘 `4.04`（10 倍 bug 已消除） |
| loader 注册 | `tdxhttp 通达信本地服务 (TDX-HTTP)` available=True，`api_key_env=''` |
| 能力分流 | `daily=True`、`adj_factor=False`、`financial=False`（按预期回退） |
| `test_dataset` | realtime 3 行 / depth5 3 行 / daily 21 行 / minute 240 行；`adj_factor` 返回"未接入"提示 |
| 单元测试 | `pytest tests/test_tdxhttp_provider.py` **27 passed**；连带 custom 相关 103 passed |

### 10.4 预览页用法

```bash
cd /film/tdx-api/web && TZ=Asia/Shanghai /tmp/tdxapi-server   # 必须在 web/ 目录启动，否则 ./static 404
open http://127.0.0.1:8080/tsp-preview.html
```

页面自带口径自检（价格单位、买卖盘有序、量额自洽、报价与日K一致、`+08:00`、分钟根数与量额自洽）。

### 10.5 仍未做（与 §6 P3 一致，未变）

`adj_factor`（Go 侧需补 `0x000f` 除权帧）、`full_minute`（无批量端点，逐标的约 1.5 分钟/轮）、
`financial`、`get_instruments`（明确不实现，避免把 `limit_up/limit_down` 覆盖成 null）、
sidecar 随安装包分发（**`/film/tdx-api` 无 LICENSE，需先确认授权**）。
