"""业务接口的页面权限映射。

前端菜单隐藏只是体验, 这里把「页面级权限」落到每个业务路由: 一个路由只要
命中下表的任一页面权限就放行。共享数据（行情/K线/指数等）会列出全部合法
消费页面, 因为多个页面本来就读同一份数据; 页面专属数据（筛选/因子/回测/
自选/成长等）只列真正消费它的页面。

约定:
  - 只覆盖 `/api` 下的业务路由, 认证、账号、管理后台、个人设置除外;
  - 多页面共享的路由用 anyOf, 不要求同时具备多个权限;
  - 无账号会话(API Token 通道/内部调用)由 `has_permission` 视为全权限;
  - 新增业务路由时必须在此登记, 否则不会被页面权限保护。
"""
from __future__ import annotations

from app.services import rbac

# 所有行情图表页 + 数据管理页都可能读 K 线/分时。
MARKET_PAGES: tuple[str, ...] = (
    rbac.PAGE_DASHBOARD,
    rbac.PAGE_WATCHLIST,
    rbac.PAGE_SCREENER,
    rbac.PAGE_FACTORS,
    rbac.PAGE_GROWTH,
    rbac.PAGE_STOCK_ANALYSIS,
    rbac.PAGE_BACKTEST,
    rbac.PAGE_REGIME,
    rbac.PAGE_INDICES,
    rbac.PAGE_ANALYSIS_WILDCARD,
    rbac.PAGE_EXT_WILDCARD,
    rbac.PAGE_DEV,
    rbac.ADMIN_DATA_MANAGE,
)

PAGE_ACCESS: dict[str, tuple[str, ...]] = {
    "kline": MARKET_PAGES,
    "intraday": MARKET_PAGES,
    "overview": (rbac.PAGE_DASHBOARD,),
    "watchlist": (
        rbac.PAGE_WATCHLIST,
        rbac.PAGE_SCREENER,
        rbac.PAGE_BACKTEST,
        rbac.PAGE_STOCK_ANALYSIS,
        rbac.PAGE_DEV,
    ),
    "screener": (rbac.PAGE_SCREENER,),
    # 因子数据也被回测的「加因子信号」弹窗读取
    "factors": (rbac.PAGE_FACTORS, rbac.PAGE_BACKTEST),
    "backtest": (rbac.PAGE_BACKTEST,),
    "mining": (rbac.PAGE_BACKTEST,),
    "guardian": (rbac.PAGE_BACKTEST,),
    "lab": (rbac.PAGE_BACKTEST,),
    "position_policies": (rbac.PAGE_BACKTEST,),
    # 策略库被策略页、回测页与规则编辑器共同使用
    "strategy": (rbac.PAGE_SCREENER, rbac.PAGE_BACKTEST, rbac.PAGE_STOCK_ANALYSIS),
    "strategy_pool": (rbac.PAGE_SCREENER, rbac.PAGE_BACKTEST),
    "signals": (rbac.PAGE_SCREENER, rbac.PAGE_BACKTEST),
    "definitions": (rbac.PAGE_SCREENER, rbac.PAGE_FACTORS, rbac.PAGE_BACKTEST),
    # 规则编辑器在自选、个股分析、策略页都可打开
    "monitor_rules": (
        rbac.PAGE_SCREENER,
        rbac.PAGE_WATCHLIST,
        rbac.PAGE_STOCK_ANALYSIS,
        rbac.PAGE_DEV,
    ),
    "regime": (rbac.PAGE_REGIME, rbac.PAGE_BACKTEST, rbac.ADMIN_DATA_MANAGE),
    "indices": (
        rbac.PAGE_INDICES,
        rbac.PAGE_DASHBOARD,
        rbac.PAGE_ANALYSIS_WILDCARD,
        rbac.PAGE_EXT_WILDCARD,
        rbac.ADMIN_DATA_MANAGE,
    ),
    "stock_analysis": (rbac.PAGE_STOCK_ANALYSIS,),
    "stock_growth": (rbac.PAGE_GROWTH,),
    "financials": (
        rbac.PAGE_STOCK_ANALYSIS,
        rbac.PAGE_GROWTH,
        rbac.PAGE_EXT_FINANCIALS,
        rbac.PAGE_ANALYSIS_WILDCARD,
    ),
    "rps": (rbac.PAGE_FACTORS, rbac.PAGE_SCREENER, rbac.PAGE_ANALYSIS_WILDCARD),
    "sector_rotation": (rbac.PAGE_DASHBOARD, rbac.PAGE_ANALYSIS_WILDCARD, rbac.PAGE_REGIME),
    "abnormal": (
        rbac.PAGE_DASHBOARD,
        rbac.PAGE_ANALYSIS_WILDCARD,
        rbac.PAGE_EXT_ABNORMAL,
        rbac.PAGE_WATCHLIST,
        rbac.PAGE_SCREENER,
        rbac.PAGE_STOCK_ANALYSIS,
        rbac.PAGE_DEV,
    ),
    # 扩展数据列被自选/策略/分析页读取; 配置写入仍由 guard_ext_data_access 管
    "ext_data": (
        rbac.PAGE_ANALYSIS_WILDCARD,
        rbac.PAGE_EXT_WILDCARD,
        rbac.PAGE_WATCHLIST,
        rbac.PAGE_SCREENER,
        rbac.ADMIN_DATA_MANAGE,
    ),
    # 数据管道与本地数据状态: 看板/策略/回测会用状态做门控, 数据页做管理
    "data": (
        rbac.PAGE_DASHBOARD,
        rbac.PAGE_SCREENER,
        rbac.PAGE_BACKTEST,
        rbac.ADMIN_DATA_MANAGE,
    ),
    "pipeline": MARKET_PAGES,
    # 扩展页（/analysis 下）自带的数据/模拟盘/事件中心
    "paper": (rbac.PAGE_EXT_PAPER, rbac.PAGE_ANALYSIS_WILDCARD),
    "lots": (rbac.PAGE_EXT_LOTS, rbac.PAGE_ANALYSIS_WILDCARD),
    "market_recap": (rbac.PAGE_DASHBOARD, rbac.PAGE_EXT_REVIEW, rbac.PAGE_ANALYSIS_WILDCARD),
}
