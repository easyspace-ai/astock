"""TSP 内部代码 (600519.SH) ↔ 通达信协议代码 (market:u8, code:str) 映射。

与项目既有符号映射同源纪律:
  - **必须先看后缀再判资产类型**: `000001.SH` 是上证指数, `000001.SZ` 是平安银行,
    只按 6 位数字前缀判会路由错。
  - 映射不出来的标的显式抛 SymbolMapError, 由调用方跳过, 不猜。

差异: tdxrs 不用 "sh600519" 字符串, 而是 (market, code) 元组:
  0 = 深圳, 1 = 上海, 2 = 北京(tdxrs.constants.MARKET_SZ/SH/BJ)。
"""

from __future__ import annotations

MARKET_SZ = 0
MARKET_SH = 1
MARKET_BJ = 2

# 上交所指数代码统一以 000 开头(000001 上证指数与深市股票同码, 只能靠后缀区分)。
# 不能用固定白名单: 000002(上证A股指数)、000680(科创综指)等未列入白名单时会被
# 误判为股票, 走错取数接口并返回空/错行。SH_INDEX 仅保留给显式别名和历史引用。
SH_INDEX = frozenset({"000001", "000300", "000905", "000852", "000016", "000688", "000985"})
SH_INDEX_ALIAS = {"999999.SH": "000001.SH"}  # 兼容 TdxQuant 时代的上证指数别名

_MARKET_TO_SUFFIX = {MARKET_SH: "SH", MARKET_SZ: "SZ", MARKET_BJ: "BJ"}

# 各市场 A 股股票前缀白名单(维表 5537 只的来源; ETF/债/B股/指数都不在内)
_STOCK_PREFIXES = {
    MARKET_SH: ("600", "601", "603", "605", "688", "689"),
    MARKET_SZ: ("000", "001", "002", "003", "300", "301", "302"),
    MARKET_BJ: ("920", "430", "830", "831", "832", "833", "834", "835", "836",
                "837", "838", "839", "870", "871", "872", "873", "874", "875",
                "876", "877", "878", "879"),
}


class SymbolMapError(ValueError):
    """代码无法映射(未知市场后缀 / 前缀与市场不符)。"""


def normalize(symbol: str) -> str:
    sym = (symbol or "").strip().upper()
    return SH_INDEX_ALIAS.get(sym, sym)


def is_index(symbol: str) -> bool:
    """判断是否为指数代码(按后缀 + 指数代码前缀)。"""
    sym = normalize(symbol)
    if sym.endswith(".SH"):
        return sym[:6].startswith("000") or sym[:6] in SH_INDEX
    if sym.endswith(".SZ"):
        return sym[:6].startswith("399")
    if sym.endswith(".BJ"):
        return sym[:6].startswith("899")
    return False


def is_etf(symbol: str) -> bool:
    """ETF/LOF 代码(沪 51x/56x/58x, 深 15x/16x)。指数与股票都不命中。"""
    sym = normalize(symbol)
    code = sym[:6]
    if sym.endswith(".SH"):
        return code.startswith(("51", "52", "56", "58"))
    if sym.endswith(".SZ"):
        return code.startswith(("15", "16", "17"))
    return False


def is_stock(market: int, code: str) -> bool:
    """协议侧代码是否为 A 股股票(用于全市场股票池过滤)。"""
    prefixes = _STOCK_PREFIXES.get(market)
    return bool(prefixes) and str(code).startswith(prefixes)


def to_tdx(symbol: str) -> tuple[int, str]:
    """600519.SH → (1, "600519")。无法映射抛 SymbolMapError。"""
    sym = normalize(symbol)
    if len(sym) != 9 or sym[6] != ".":
        raise SymbolMapError(f"代码格式应为 6位代码.市场: {symbol!r}")
    code, mkt = sym[:6], sym[7:]
    if not code.isdigit():
        raise SymbolMapError(f"代码应为 6 位数字: {symbol!r}")
    if mkt == "SH":
        # 指数/ETF/股票/B股 全部在同一市场号; 前缀合法性由白名单兜住
        if code in SH_INDEX or code.startswith(("6", "5", "9", "0")):
            return MARKET_SH, code
        raise SymbolMapError(f"沪市代码前缀不明: {symbol!r}")
    if mkt == "SZ":
        if code.startswith(("0", "3", "1", "2")):
            return MARKET_SZ, code
        raise SymbolMapError(f"深市代码前缀不明: {symbol!r}")
    if mkt == "BJ":
        if code.startswith(("4", "8", "9", "0")):
            return MARKET_BJ, code
        raise SymbolMapError(f"北交所代码前缀不明: {symbol!r}")
    raise SymbolMapError(f"未知市场后缀: {symbol!r}")


def to_tsp(market: int, code: str) -> str:
    """(1, "600519") → 600519.SH。"""
    suffix = _MARKET_TO_SUFFIX.get(int(market))
    text = str(code or "").strip()
    if suffix is None or len(text) != 6 or not text.isdigit():
        raise SymbolMapError(f"服务端代码无法解析: market={market!r} code={code!r}")
    return f"{text}.{suffix}"


__all__ = [
    "MARKET_BJ",
    "MARKET_SH",
    "MARKET_SZ",
    "SH_INDEX",
    "SH_INDEX_ALIAS",
    "SymbolMapError",
    "is_etf",
    "is_index",
    "is_stock",
    "normalize",
    "to_tdx",
    "to_tsp",
]
