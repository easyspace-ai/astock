# 系统总览与菜单

系统由三条业务线组成：**数据线**（同步行情、财务、分钟与扩展数据）、**研究线**（因子检验、组合、挖掘）、**交易线**（策略、信号、回测、监控）。三条线共用同一份增强数据表（Enriched），因此字段口径一致。

<div class="kpi">
    <div><b>77</b><span>内置因子（按动量、波动率、量价、形态等分组）</span></div>
    <div><b>25</b><span>因子 DSL 算子（时序 / 截面 / 工具）</span></div>
    <div><b>20</b><span>内置信号定义（预计算，只读可隐藏）</span></div>
    <div><b>57</b><span>Enriched 可用字段（行情 + 指标 + JOIN）</span></div>
  </div>

## 菜单结构与页面路径

<table>
    <thead><tr><th>分组</th><th>菜单</th><th>路径</th><th>用途</th></tr></thead>
    <tbody>
      <tr><td rowspan="3">核心</td><td>看板</td><td><code>/</code></td><td>市场摘要、监控通知、常用入口</td></tr>
      <tr><td>自选</td><td><code>/watchlist</code></td><td>关注标的的行情、K 线、分时与自定义列</td></tr>
      <tr><td>策略</td><td><code>/screener</code></td><td>运行策略、查看候选与评分、调参、创建策略</td></tr>
      <tr><td rowspan="3">研究</td><td>因子</td><td><code>/factors</code></td><td>检验 / 因子库 / 编辑器 / 组合 / 挖掘</td></tr>
      <tr><td>回测</td><td><code>/backtest</code></td><td>策略回测、参数优化、步进优化</td></tr>
      <tr><td>信号库</td><td><code>/signals</code></td><td>内置信号与自定义条件信号管理</td></tr>
      <tr><td rowspan="5">分析</td><td>个股分析</td><td><code>/stock-analysis</code></td><td>个股 K 线、关键点位、AI 四维报告</td></tr>
      <tr><td>市场环境</td><td><code>/regime</code></td><td>大盘环境与风格判断</td></tr>
      <tr><td>指数</td><td><code>/indices</code></td><td>指数列表、日 K 与分时</td></tr>
      <tr><td>概念板块 / 行业板块</td><td><code>/concept-analysis</code>、<code>/industry-analysis</code></td><td>板块轮动、强弱与龙头候选</td></tr>
      <tr><td>三年五倍</td><td><code>/growth</code></td><td>扫描历史窗口内「谷底→峰值」涨幅 ≥5 倍的标的（≤756 交易日，前复权口径）</td></tr>
      <tr><td rowspan="7">监控与运营</td><td>监控中心</td><td><code>/monitor</code></td><td>规则管理、触发记录与通知</td></tr>
      <tr><td>持仓提醒</td><td><code>/lots</code></td><td>按买入批次生成止盈止损监控</td></tr>
      <tr><td>模拟盘</td><td><code>/paper</code></td><td>模拟账户、下单与净值跟踪</td></tr>
      <tr><td>异动监控</td><td><code>/abnormal</code></td><td>竞价、盘中、板块异动聚合</td></tr>
      <tr><td>连板梯队</td><td><code>/limit-ladder</code></td><td>涨跌停、连板/连跌梯队</td></tr>
      <tr><td>复盘</td><td><code>/review</code></td><td>AI 盘后复盘与定时推送</td></tr>
      <tr><td>AI 助手</td><td><code>/assistant</code></td><td>多轮对话式数据与策略问答</td></tr>
      <tr><td rowspan="2">信息</td><td>公告中心 / 新闻中心 / 快讯</td><td><code>/info/*</code></td><td>公开信息聚合与检索</td></tr>
      <tr><td>财务概况</td><td><code>/financials</code></td><td>三大报表与财务指标</td></tr>
      <tr><td rowspan="2">系统</td><td>数据</td><td>菜单「数据」</td><td>数据画像、同步、重算、清理</td></tr>
      <tr><td>设置</td><td>菜单「设置」</td><td>数据源、AI、实时监控、通知、账号、备份</td></tr>
    </tbody>
  </table>

::: tip 菜单可配置
菜单分组、显示顺序与可见性可在「设置 → 菜单设置」中调整；未分配的页面不会出现在导航中，但直接访问路径仍受账号权限校验。
:::

<Figure src="/shots/s02.jpg" alt="看板" caption="顶栏与分组菜单：行情看板 / 策略研究 / 个股研究 / 市场分析 / 资讯与监控 / 交易与复盘" />
