#!/usr/bin/env python3
"""tdx-api HTTP 服务口径自检 (只读, 无第三方依赖).

目的: 把 docs/tdx-http-data-source-plan.md §2.2 的单位口径表固化成可执行的检查,
在接入 TSP 之前确认 价格(厘/0.01元) / 成交量(手) / 成交额(元/厘) / 时区 四项没有错位。

检查项:
  1) 报价收盘价 与 原始日K收盘价 一致 (同一标的同一交易日)
  2) 日K 内部自洽: amount ÷ (volume × 100) ≈ (high+low)/2
  3) 分钟K: 时分为北京墙钟 (09:30–15:00), 且 amount ÷ (volume × 100) ≈ close
  4) ETF(Decimal=3) 报价口径是否与 K线一致 (已知 /api/quote 存在 /10 差异, 会被标为 WARN)

用法:
    uv run python scripts/probe_tdx_http.py [--base http://127.0.0.1:8080]
                                            [--symbols 600519.SH,000001.SZ,510300.SH,sh000001]

退出码: 0 = 全部 PASS/WARN; 1 = 存在 FAIL。
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

TIMEOUT = 30
# 均价容差: 通达信 amount 与 (volume×均价) 来自不同字段, 允许 20% 偏差
AVG_TOL = 0.20

SH_INDEX = {"000001", "000300", "000905", "000852", "000016", "000688"}
SZ_INDEX_PREFIX = ("399",)


@dataclass
class Row:
    symbol: str
    status: str
    detail: str


def http_get(base: str, path: str, params: dict[str, str | int]) -> dict:
    url = f"{base}{path}?{urllib.parse.urlencode(params)}"
    with urllib.request.urlopen(url, timeout=TIMEOUT) as resp:  # noqa: S310 - 本机回环
        payload = json.loads(resp.read().decode("utf-8"))
    if payload.get("code") != 0:
        raise RuntimeError(f"{path} -> code={payload.get('code')} {payload.get('message')}")
    return payload["data"]


def to_tdx_code(symbol: str) -> str:
    """600519.SH -> sh600519; 000001.SH(指数) -> sh000001; 已带前缀的原样返回."""
    text = symbol.strip().lower()
    if text.startswith(("sh", "sz", "bj")):
        return text
    body, _, suffix = text.partition(".")
    if suffix:
        return f"{suffix}{body}"
    # 无后缀时按规则推断: 指数优先
    if body in SH_INDEX or body.startswith(SZ_INDEX_PREFIX):
        return ("sh" if body in SH_INDEX else "sz") + body
    if body.startswith("6") or body[:3] in {"510", "511", "512", "513", "515"}:
        return "sh" + body
    if body[:2] in {"92", "43"} or body.startswith("8"):
        return "bj" + body
    return "sz" + body


def is_index(symbol: str) -> bool:
    """注意 000001.SH(上证指数) 与 000001.SZ(平安银行) 同码不同所, 必须看后缀。"""
    body, _, suffix = symbol.partition(".")
    if body.startswith(SZ_INDEX_PREFIX):
        return True
    if suffix:
        return suffix.upper() == "SH" and body in SH_INDEX
    return body in SH_INDEX


def is_etf(symbol: str) -> bool:
    return symbol.split(".")[0][:3] in {"510", "511", "512", "513", "515", "159", "588"}


def day_rows(base: str, symbol: str, limit: int = 2) -> list[dict]:
    if is_index(symbol):
        data = http_get(base, "/api/index/all", {"code": to_tdx_code(symbol), "type": "day", "limit": limit})
    else:
        data = http_get(base, "/api/kline-all/tdx", {"code": to_tdx_code(symbol), "type": "day", "limit": limit})
    return data["list"]


def minute_rows(base: str, symbol: str, limit: int = 3) -> list[dict]:
    if is_index(symbol):
        data = http_get(base, "/api/index", {"code": to_tdx_code(symbol), "type": "minute1", "limit": limit})
        return data["List"]
    data = http_get(base, "/api/kline-history", {"code": to_tdx_code(symbol), "type": "minute1", "limit": limit})
    return data["List"]


def quote(base: str, symbol: str) -> dict:
    data = http_get(base, "/api/quote", {"code": to_tdx_code(symbol)})
    return data[0]


def check_symbol(base: str, symbol: str, rows: list[Row]) -> None:
    code = to_tdx_code(symbol)

    # ---- 报价 vs 日K (同一交易日取最后一根) ----
    try:
        q = quote(base, symbol)
    except (urllib.error.URLError, RuntimeError) as exc:
        rows.append(Row(symbol, "FAIL", f"报价请求失败: {exc}"))
        return

    if q.get("Code") != code[2:]:
        rows.append(Row(symbol, "FAIL", f"回包代码不符: 请求 {code[2:]} 实得 {q.get('Code')} (旧码/无效码静默错误)"))
        return
    if not any(v for k, v in q["K"].items()):
        rows.append(Row(symbol, "FAIL", "回包行情全零, 视为无数据"))
        return

    q_close = q["K"]["Close"] / 1000  # 股票/指数: 厘 -> 元
    if is_etf(symbol):
        # 已知 /api/quote 对 Decimal==3 额外 /10, 因此报价是 0.01 元口径
        q_close = q["K"]["Close"] / 100

    try:
        ks = day_rows(base, symbol, 2)
    except (urllib.error.URLError, RuntimeError) as exc:
        rows.append(Row(symbol, "FAIL", f"日K请求失败: {exc}"))
        return
    if not ks:
        rows.append(Row(symbol, "FAIL", "日K返回空"))
        return

    k = ks[-1]
    k_close = k["Close"] / 1000
    same_day = k["Time"][:10] == _today_shanghai()
    if same_day:
        # 盘中: 日K最后一根是未收盘 bar, 收盘价与快照可能有秒级差异, 用 5% 容差
        tol = 0.05
    else:
        tol = 0.01
    if abs(q_close - k_close) > max(tol * k_close, 0.02):
        rows.append(Row(symbol, "FAIL", f"报价与日K收盘不一致: quote={q_close:.3f} kline={k_close:.3f} ({k['Time']})"))
    else:
        rows.append(Row(symbol, "PASS", f"quote==kline close {k_close:.3f} ({k['Time'][:10]})"))

    if is_etf(symbol):
        ratio = q["K"]["Close"] / k["Close"] if k["Close"] else 0
        rows.append(
            Row(
                symbol,
                "WARN" if abs(ratio - 0.1) < 0.001 else "PASS",
                f"ETF 报价口径比 kline={ratio:.3f} (期望修复后 1.000, 现状 0.1 = /10 bug)",
            )
        )

    # ---- 日K 内部自洽: amount ÷ (volume×100) ≈ (high+low)/2 ----
    # 指数不适用: 指数成交额/成交量对应成分股均价, 与指数点位无关。
    if is_index(symbol):
        rows.append(Row(symbol, "SKIP", "指数不校验量额比(指数点位非均价)"))
    else:
        for row in (ks if len(ks) > 1 else []):
            vol = row["Volume"]
            amt = row["Amount"]
            if not vol or not amt:
                continue
            avg = amt / 1000 / (vol * 100)  # 厘->元, 手->股
            mid = (row["High"] + row["Low"]) / 2000
            gap = abs(avg - mid) / mid if mid else 9.99
            if gap > AVG_TOL:
                rows.append(Row(symbol, "FAIL", f"日K量额不自洽 {row['Time'][:10]}: 均价 {avg:.3f} vs 中枢 {mid:.3f} (偏差 {gap:.0%})"))
                break
        else:
            rows.append(Row(symbol, "PASS", "日K amount/(volume*100) 与价格中枢一致"))

    # ---- 分钟K: 北京墙钟 + 量额自洽 ----
    try:
        ms = minute_rows(base, symbol, 3)
    except (urllib.error.URLError, RuntimeError) as exc:
        rows.append(Row(symbol, "WARN", f"分钟请求失败: {exc}"))
        return
    if not ms:
        rows.append(Row(symbol, "WARN", "分钟返回空(非交易时段可能正常)"))
        return
    hhmm = ms[-1]["Time"][11:16]
    if not ("09:30" <= hhmm <= "15:00"):
        rows.append(Row(symbol, "FAIL", f"分钟时间不在北京交易时段: {ms[-1]['Time']} (检查服务进程 TZ)"))
    else:
        rows.append(Row(symbol, "PASS", f"分钟墙钟 {hhmm} 在交易时段"))

    for row in ms:
        vol, amt, close = row["Volume"], row["Amount"], row["Close"] / 1000
        if not vol or not amt or not close:
            continue
        if is_index(symbol):
            break  # 指数同上: 量额比无量纲意义
        avg = amt / 1000 / (vol * 100)
        gap = abs(avg - close) / close
        if gap > AVG_TOL:
            rows.append(Row(symbol, "FAIL", f"分钟量额不自洽 {row['Time']}: 均价 {avg:.3f} vs 收 {close:.3f} (偏差 {gap:.0%})"))
            break
    else:
        rows.append(Row(symbol, "SKIP" if is_index(symbol) else "PASS", "指数跳过量额比" if is_index(symbol) else "分钟 amount/(volume*100) 与收盘价一致"))


def _today_shanghai() -> str:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    return datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()


def main() -> int:
    parser = argparse.ArgumentParser(description="tdx-api 单位口径自检")
    parser.add_argument("--base", default="http://127.0.0.1:8080", help="tdx-api 服务地址")
    parser.add_argument("--symbols", default="600519.SH,000001.SZ,300750.SZ,510300.SH,000001.SH",
                        help="逗号分隔的 TSP symbol")
    args = parser.parse_args()

    try:
        status = http_get(args.base, "/api/server-status", {})
        print(f"服务状态: {status.get('status')} connected={status.get('connected')} base={args.base}\n")
    except (urllib.error.URLError, RuntimeError) as exc:
        print(f"无法连接 {args.base}: {exc}", file=sys.stderr)
        return 1

    rows: list[Row] = []
    for symbol in [s.strip() for s in args.symbols.split(",") if s.strip()]:
        try:
            check_symbol(args.base, symbol, rows)
        except Exception as exc:  # noqa: BLE001 - 自检脚本, 任何异常都要落到报告里
            rows.append(Row(symbol, "FAIL", f"检查异常: {type(exc).__name__}: {exc}"))

    width = max(len(r.symbol) for r in rows)
    fails = 0
    for r in rows:
        if r.status == "FAIL":
            fails += 1
        print(f"{r.symbol:<{width}}  {r.status:<4}  {r.detail}")
    print(f"\n合计: {len(rows)} 项, FAIL {fails} 项")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
