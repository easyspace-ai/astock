# tdxrs 数据源插件说明

> TSP 内置数据源插件 `tdxrs`：进程内直连通达信免费行情协议，**无 Key、无积分、无 sidecar**。
> 目录：`backend/app/plugins/tdxrs/`　｜　清单名：`tdxrs`　｜　显示名：**通达信 (tdxrs 自建直连)**
> 上游：`tdxrs` PyPI 包（Rust + PyO3，MIT），源码 `github.com/injoyai/tdxrs`

---

## 1. 它是什么

```
TSP backend ──PyO3(同进程)──▶ tdxrs(Rust) ──TCP──▶ 通达信行情服务器
```

- 没有独立进程、没有本地端口、不需要 `TZ`、没有 40–60 秒预热窗口，"安装依赖 → 重启后端"即生效。
- 与两个已存在/已退役的方案的差别：

| 方案 | 依赖 | 成本 | 状态 |
| --- | --- | --- | --- |
| `tdxquant` | `tdxaidata` ctypes 包装 DLL | API Key + 积分计费 | 保留（付费/六项能力，含全量分钟） |
| `tdxhttp`（TDX-HTTP） | 本机 Go sidecar `:8080` | 免费 | **已删除**，能力由本插件承接 |
| **`tdxrs`** | `pip install tdxrs` | **免费** | 当前方案 |

---

## 2. 安装与启动

### 2.1 正常启动

```bash
cd /path/to/tsp
./dev.sh          # 唯一需要的命令
```

后端启动时 `loader` 扫 `app/plugins/*/plugin.yaml` → 调本插件 `availability()`（导入 `tdxrs` + 试连一次服务器）→ 通过即注册。设置页自动出现卡片，无需任何配置。

### 2.2 依赖

`requirements.txt` 只有一行：`tdxrs>=0.6.10`（macOS arm64 wheel ≈1 MB，零 Python 依赖，requires-python ≥3.11）。

已装入 `backend/.venv`。若重建过 venv：

```bash
uv pip install --python backend/.venv/bin/python "tdxrs>=0.6.10"
# 或直接点设置页卡片的「安装依赖」按钮（runtime: python 会读 requirements.txt）
```

### 2.3 可选环境变量

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `TDXRS_BLOCK_SERVER` | `180.153.18.170:7709` | 板块/ETF 专属客户端的目标服务器。协议不返回当前连接目标，只能显式指定 |

---

## 3. 能力矩阵

### 3.1 数据集（参与 `provider_has_dataset` 路由）

`datasets: [realtime, depth5, daily, adj_factor, minute]`

| 数据集 | 内容 | 粒度 / 深度 | 未声明时的行为 |
| --- | --- | --- | --- |
| `realtime` | 全 A 快照（含名称） | 60 只/批，全市场约 3.7s | — |
| `depth5` | 五档盘口 | 复用同一快照，零额外请求 | — |
| `daily` | 原始日K（不复权） | 单标的可回全历史（600519 = 6044 根） | — |
| `adj_factor` | 除权因子（含 ETF） | 按窗口增量 | — |
| `minute` | 1 分钟K | 约 **90 交易日**（`minute_history_days = 90`） | — |
| `full_minute` | 全市场分钟 | **不声明** | 回退 TickFlow |
| `financial` | 财务三表时序 | **不声明**（只有单期快照） | 回退 TickFlow |

> `full_minute` 不做的原因：上游无批量端点，逐标的增量一轮 3–4 分钟，打不住 6s 盘中节奏。宁可回退，也不做"声明了却永远落后"的假覆盖。

### 3.2 接口总览

| 类别 | 数量 | 说明 |
| --- | --- | --- |
| 契约方法 | 10 | `get_realtime` / `get_realtime_symbols` / `get_realtime_indices` / `get_depth_batch` / `get_daily` / `iter_daily` / `get_adj_factors` / `get_minute` / `iter_minute` / `test_dataset` |
| 扩展接口 | 13 | `tx_*`，覆盖指数/ETF/板块/逐笔/分时/财务快照/代码表/连接状态 |
| 类属性 | — | `name="tdxrs"`、`builtin=True`、`minute_history_days=90` |

`ManagedProvider.__getattr__` 全属性透传，所以 `tx_*` 可以直接用鸭子类型探测调用（先例：`kline_sync` 用 `getattr(provider, "get_intraday_batch", None)`）。

---

## 4. 契约接口

### 4.1 realtime 行字段

```python
{
  "symbol": "600519.SH", "name": "贵州茅台",
  "last_price": 1457.7, "prev_close": 1453.9,
  "open": 1450.0, "high": 1461.6, "low": 1437.71,
  "volume": 35116,            # 手
  "amount": 5094594560.0,     # 元
  "change_pct": 0.002613,     # 小数制，不乘 100
  "change_amount": 3.8,
  "amplitude": None, "turnover_rate": None,   # 快照不提供，不启发式计算
  "timestamp": 1763...,       # 毫秒；由 servertime 解析，失败回退本地时间
  "session": None,
}
```

失败语义：`get_realtime` 软失败返回 `[]`（不阻断轮询线程）；`get_realtime_indices` 失败返回 `None`（保留上轮缓存）；`get_realtime_symbols` 失败返回 `None`。

### 4.2 历史接口

| 方法 | 语义 |
| --- | --- |
| `get_daily(symbols, start_time, end_time, asset_type, on_chunk_done)` | 逐标的原始日K，一次性 concat |
| `iter_daily(...)` | 有界分批产出；单票失败记 warning 继续，**连续 20 只失败**才抛错（视为服务端异常） |
| `get_adj_factors(symbols, start_time, end_time, asset_type, on_chunk_done)` | 除权因子宽表（见 §5.3） |
| `get_minute(symbols, ..., freq="1m", on_chunk_done)` | 1m，只有 `freq="1m"`；`asset_type="index"` 空手而还；请求失败抛 `ProviderRequestError` |

### 4.3 adj_factor 表结构

| 列 | 类型 | 口径 |
| --- | --- | --- |
| `symbol` / `trade_date` | str / Date | 除权除息日 |
| `ex_factor` | f64 | **单事件比值 = 前收盘 / 参考价（>1 方向）**，非累积 |
| `dividend` | f64 | 每股分红（协议给的是每 10 股，已 ÷10） |
| `bonus` | f64 | 每股送股（÷10） |
| `allot` | f64 | 每股配股（÷10） |
| `allot_price` | f64 | 配股价（元/股，不除 10） |
| `prev_close` | f64 | 除权日前收盘 |

---

## 5. 扩展接口（`tx_*`）

| 签名 | 返回列 / 结构 | 说明 |
| --- | --- | --- |
| `tx_kline(symbol, period="day", count=800, fq=0)` | 同 `daily` / `minute` schema | 周期：`1m` `5m` `15m` `30m` `1h` `day` `week` `month`；`fq`：0 不复权 / 1 前复权 / 2 后复权 |
| `tx_index_kline(symbol, period="day", count=800)` | 同上 | 指数专用（内部 `get_index_bars`）。1m 深度只有 4–10 个交易日 |
| `tx_minute_time(symbol, trade_date=None)` | `symbol, time, price, avg_price, vol` | 分时 240 点/日；当日或历史某日，vol=手 |
| `tx_transactions(symbol, count=2000, offset=0, trade_date=None, drop_empty=True)` | `symbol, time, price, vol, num, buyorsell` | 逐笔；`buyorsell` 0=买/1=卖/2=中性；收盘后填充行（vol=0）默认剔除 |
| `tx_xdxr(symbol)` | `symbol, date, category, category_name, fenhong_per10, peigujia, songzhuangu_per10, peigu_per10, suogu_per10` | 14 类事件明细，金额/比例**保留每 10 股原值** |
| `tx_finance_snapshot(symbol)` | dict（36 字段） | 单期快照，股本单位为万股；**无 `period_end`/`announce_date`** |
| `tx_security_list(market="SH"/"SZ"/"BJ")` | `symbol, code, name, pre_close, volunit, decimal_point` | 代码表（沪 27881 行） |
| `tx_block_components(kind="industry"/"concept"/"index")` | `block_name, block_type, code_index, constituent` | 板块→成分股（行业 20856 行） |
| `tx_block_quotes(codes)` | `code, price, prev_close, open, high, low, vol, amount` | 板块指数行情（880xxx/881xxx） |
| `tx_block_kline(code, period="day", count=800)` | `code, datetime, ohlcv, amount` | 板块指数 K 线 |
| `tx_fund_list(market="SH"/"SZ")` | `symbol, code, name, fund_type, pre_close, vol_unit` | ETF/基金列表（沪 827 只） |
| `tx_fund_bars(symbol, period="day", count=800)` | 同 `daily` / `minute` | ETF K 线（走基金客户端，口径与股票交叉验证一致） |
| `tx_server_status()` | dict | `connected` / `pool` / `universe` / `quote_batch` / `minute_history_days` / `datasets` |

调用示例：

```python
from app.data_providers.custom import loader

p = loader.get_provider("tdxrs")           # ManagedProvider，属性全透传
df = p.tx_kline("600519.SH", period="day", count=60)
print(p.tx_server_status())
```

---

## 6. 口径与单位（**动这几处之前先读这份表**）

| 数据 | 协议给的 | 落库口径 | 处理 |
| --- | --- | --- | --- |
| 快照价格 | 元(float) | 元 | 透传 |
| 快照 `vol` | **手** | 手 | 透传，**不得再 ÷100** |
| 快照 `amount` | 元 | 元 | 透传 |
| 快照 `servertime` | `"15:15:4431"`（HH:MM:SS + 亚秒尾数） | 毫秒 | 只取到秒，尾数丢弃；ETF 会返回垃圾（`152:99:6950`）→ 回退本地时间 |
| 日K `vol` | **股** | 手 | `floor(vol/100)` |
| 分钟K `vol` | **股** | 手 | `floor(vol/100)` |
| K线 `amount` | 元 | 元 | 透传 |
| K线 `datetime` | 日K `"YYYY-MM-DD"` / 分钟 `"YYYY-MM-DD HH:MM"` | naive 北京墙钟 | 直接 `strptime`，不做时区换算 |
| XDXR 金额/比例 | 每 10 股 | 每股 | ÷10（`peigujia` 除外，本身就是每股价格） |
| `calc_fq_factors.qfq_factor` | 参考价/前收盘（**<1 方向**） | — | **只作交叉校验**；TSP 要的 `ex_factor` 是它的倒数 |
| 涨跌幅 | 小数 | 小数 | 不乘 100 |

### 6.1 三个必须记住的坑

1. **单位不能按字段名推**：快照 `vol` 是手，日K/分钟 `vol` 是股。搞反就是 100 倍误差。
2. **`calc_fq_factors` 不能按 `start` 分页**。实测它只对"事件落在本次返回的 bars 窗口内"正确：600519 `start=800` 时，2025 年事件的 `close_before` 变成窗口边界价 1838.03，拼出来全是错因子。所以除权因子改为交易所公式自算：

   ```
   ref  = (P - D + AR·AP) / (1 + S + AR)     # 四舍五入(half-up) 2 位
   factor = P / ref                           # >1 方向
   ```

   外加**涨跌停自检**（错误因子会让复权后除权日涨跌幅越过板限，10%/20%/30% + 2% 容差）。
3. **指数必须走 index 接口**。`get_security_bars` 取指数只回 1 根且日期陈旧；category 8 是 23 根同 `09:30` 的假数据。

---

## 7. 性能实测

环境：macOS arm64、tdxrs 0.6.10、收盘后。

| 场景 | 数值 |
| --- | --- |
| 股票池 | 5225 只（沪+深），首次枚举 11.8s，缓存 12h |
| `get_realtime` 全市场 | **5224 条 / 3.7s**（87 批 × 60 只） |
| 日K 增量（100 只样本外推） | 全市场约 6 分钟 |
| 除权因子（100 只含事件筛选外推） | 全市场约 8 分钟 |
| 1 分钟K 单标的 21600 根 | 约 2.3s |
| 日K 单标的全历史 6044 根 | 约 0.6s |

> 线程池没有收益：PyO3 绑定全程持 GIL，实测 8 线程跑 40 批加速比 **1.00×**，`AsyncTdxHqClient` 反而更慢。所以内部单线程串行 + 20ms/标的节流。

首次实时轮询会慢（约 15 秒，要枚举代码表），之后每轮 3.7 秒。

---

## 8. 已知限制

| 限制 | 现象 | 处理 |
| --- | --- | --- |
| 北交所 | 当前服务器 `get_security_count(2)` 返回 `E2005` | 股票池缺 BJ（显式代码仍可取数），`universe()` 对 market=2 失败只跳过不抛错 |
| 退市/无效码顶包 | `600355` 回包变成 `600839` 全零 | client 按 `(market, code)` 校验剔除，全市场因此少约 1 条 |
| 指数五档 | 该位置是无效字节（实测 `bid1=376173.22`） | `get_depth_batch` 跳过指数；`_depth_entry` 丢弃 >1e5 的哨兵值 |
| 指数分钟 | `get_index_bars(7)` 深度仅 4–10 交易日、翻页不稳 | `minute` 数据集不含指数；需要时用 `tx_index_kline` 显式取 |
| 逐笔收盘后 | 协议回填 `vol=0 num=0 bs=8` 填充行 | `tx_transactions(drop_empty=True)` 默认剔除 |
| 交易日历 | 上游无日历接口 | 社区/交易日判断仍走项目既有逻辑 |
| 桌面包 | `packaging/tsp.spec` 目前只声明 fuyao/tdxquant | 打包进桌面版需另把 `tdxrs` 原生 wheel 收进 `binaries`（独立任务） |

---

## 9. 验证与测试

```bash
cd backend

# 插件单测（假 client 注入，不依赖网络）
.venv/bin/python -m pytest tests/test_tdxrs_provider.py -q      # 49 passed
.venv/bin/python -m ruff check app/plugins/tdxrs/ tests/test_tdxrs_provider.py

# 真机探活
.venv/bin/python -c "from app.plugins.tdxrs.provider import availability; print(availability())"

# 设置页「试拉」同款：逐个数据集自检
.venv/bin/python -c "
from app.plugins.tdxrs.provider import TdxRsProvider
p = TdxRsProvider()
for ds in ('realtime','depth5','daily','adj_factor','minute','financial'):
    out = p.test_dataset(ds); print(ds, out.get('rows'), out.get('error'))
p.close()"

# 插件是否被 loader 注册、显示名是否正确
.venv/bin/python -c "
from app.data_providers.custom import loader
loader._load_builtin_plugins()
print([(d['name'], d['display_name'], d['available']) for d in loader.list_plugins()])"
```

测试覆盖点：符号映射（含 `000001.SH` 与 `000001.SZ` 同码区分）、单位换算、快照行映射、回包顶包剔除、五档零盘口/哨兵值、日K 窗口过滤与排序、分钟 category=7、除权公式与自检、软/硬失败语义、能力声明、清单一致性、loader 注册。

---

## 10. 故障排查

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| 设置页卡片显示"未就绪" | `tdxrs` 没装进 `backend/.venv`（如重建过 venv） | 点「安装依赖」或 `uv pip install --python backend/.venv/bin/python "tdxrs>=0.6.10"` |
| 日志 `所有候选服务器均连接失败` | 网络/防火墙拦截 7709 | 检查出口；可用 `availability()` 单独验证 |
| 实时本轮 0 条 | 服务器抽风或连接断开 | 下一次轮询会重连（client 内建重连一次）；持续则看 `tx_server_status()` |
| 北交所缺数据 | `E2005`（代码表接口不可用） | 已知限制，等上游或换服务器 |
| 日K 只回到某一年 | 请求 count 不够（窗口决定根数） | 加大 `count`（上限 8000，≈32 年） |
| 除权因子少了几条远古事件 | 收盘序列有缺口 → 涨跌停自检剔除 | 与 `tdxquant` 同行为；滚动增量窗口会补近期事件 |

---

## 11. 后续可做

1. 北交所代码表恢复后接入（`universe()` 已留分支）。
2. `full_minute`：需要上游批量端点或多进程方案，单连接 3–4 分钟一轮不达标。
3. `financial`：需要带 `period_end/announce_date` 的时序接口，单期快照不能替代三表。
4. 桌面包：把 `tdxrs` 原生 wheel 加进 `packaging/tsp.spec` 的 `binaries` 与 `hiddenimports` 双声明。
5. 集合竞价：`services/auction_benchmark.py` 能否用快照满足，需盘中实测。
