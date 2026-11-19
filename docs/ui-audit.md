# UI 全面检查与待办清单

> 基准：2026-11-17，P1–P7 已落地后的实测审计。
> 数据来源：`pnpm build`（10.24s EXIT 0）、全量 `vitest run`（119 文件 / 817 用例，1 项疑似 flaky）、源码全仓扫描。

## 0. 一句话结论

白标主链路（外链 / 打赏 / 版权 / 更新检查 / 品牌字 / logo / favicon / 引导页许可）已经清干净，**剩下的是三块**：图表调色板与令牌脱节、几处旧的高度补偿与孤儿模块、以及一个「顶栏中部空（✅ 已补全局搜索，2026-11-17）着」的产品性缺口。

## 1. 本轮已补齐（P7 白标 · 第二步）

| 项 | 处理 |
| --- | --- |
| `public/favicon.svg` | 原为上游「方括号 + K 线 + 紫色渐变」，注释里直接写着 Tick Stock Panel。已换成与 `Logo.tsx` 同构的六边形 + 镂空长阳线，保留品牌紫渐变。 |
| `public/donate-wechat.jpg` | 上游作者的微信赞赏码，About 改写后已无引用，但仍会随 `public/` 打进 dist。已删除。 |
| `pages/Onboarding.tsx` | 首屏「TSP · 智能量化 / 工作台引擎」→「财富引擎 / 量化研究 · 交易工作台」；删掉「用开源引擎回测」措辞、指向上游仓库的「查看引擎源码」、MIT 许可整段（`PROJECT_REPO_URL` / `PROJECT_LICENSE_NAME` / `PROJECT_LICENSE_TEXT` 三个常量的引用），换成「数据源与使用范围」两条内部口径。 |
| `pages/settings/About.tsx` | 正文里的 TSP 自述改为「财富引擎」。 |
| `lib/api.ts` | 两处错误提示里的「重启 TSP / 转发到 TSP 后端」改为「重启终端 / 转发到终端后端」。 |
| 引导页欢迎语 | 「欢迎使用 TSP」→「欢迎使用财富引擎」。 |

构建 10.24s EXIT 0；`public/` 现在只剩 `favicon.svg` 与 `third-party/`。

## 2. 白标仍未处理（需你拍板）

1. **`pages/Branding.tsx`（管理台「品牌」分页）**：4 种风格语言的展示页，内部交易员用不到，但会出现在 `/admin/branding`。建议删除，同时移除 `AdminApp` 里的 `branding` 分节。
2. **孤儿模块**：`lib/updateCheck.ts`（含 `updateCheck.test.ts`，8 项测试）、`data/ossLicenses.ts`、`components/OssLicenseDialog.tsx`。三者运行时已无入口，只是源码与测试里还留着上游仓库地址。建议整体删除；若要保留 MIT 合规，仅保留仓库根 `LICENSE` 与 `public/third-party/*` 即可（后者是 third-party 库的许可证，属正常合规物，不可删）。
3. **`tickflow` 字样**：`lib/api.ts`、`provider-routing.test.ts`、若干注释里大量出现。这是**数据供应商 TickFlow 的产品名**（用户自己配置的数据源），不是开源项目名，属正常的第三方服务标识，建议保留。

## 3. 视觉一致性（P8 候选，按收益排序）

### 3.1 图表调色板仍是旧灰阶（✅ 已修，2026-11-17；并修 setOption 重复调用回归）

`lib/theme.ts` 的 `ChartTheme` 还是改造前的灰阶：

- 暗色：`text #A1A1AA`、`border #27272A`
- 亮色：`textStrong #27272A`、`tooltipText #27272A`

而壳层亮色已切到 `border-default #DCDEE1` / `text-muted` 一套中性色，暗色是 `#0D0E10` 画布 + `#151618` 纸面。结果：**图表坐标轴、刻度、tooltip 文字与卡片边框不是同一套灰**，暗色下尤其明显（轴比卡片边更亮）。

建议：把 `ChartTheme` 的 8 个颜色改为读 CSS 变量（`hsl(var(--fg-primary))` 之类），只在运行期计算一次，图表就跟品牌包、明暗、五表面阶梯自动同步。

### 3.2 六处旧高度补偿（✅ 已修壳层相关部分）

```
custom/stock-analysis/Page.tsx:283   max-h-[calc(100vh-220px)]
custom/review/Page.tsx:910           max-h-[calc(100vh-22rem)]
custom/review/Page.tsx:966           max-h-[calc(100vh-26rem)]
pages/backtest/MiningWorkbench.tsx:678/679/786   calc(100vh-9rem)
```

新壳实际 chrome ≈ 44（顶栏）+ 28（状态栏）+ 12（间隙）≈ **90px ≈ 5.6rem**，而这些页面按 9rem / 22rem / 26rem 扣，等于凭空少给 40–300px，表现为「面板没铺满、下面留一截空白」。建议统一成 `h-full min-h-0` + 父容器滚动（壳里 `ContentPanel` 已经是 `flex h-full`），彻底不用 `100vh` 运算。

### 3.3 其余视觉小项

- `pages/AdminApp.tsx` 仍是独立全屏壳（`min-h-screen bg-base` + 自带 56px sticky 顶栏 + `max-w-[1500px]`），与新终端的 44px 顶栏 / 12px 纸面 / 28px 状态栏不是一套语言。它是管理台，可接受，但至少可把顶栏高度、圆角、文字档位对齐。
- 空态与骨架：看板/回测有实现，`custom/*` 扩展页多为裸文字空态，可统一成 `shell/EmptyState`。
- 移动端：997px 桌面态已验，**375px 抽屉态未实测**（BU 视口改不动，需人工拉窄一次）。

## 4. 产品性缺口

1. **顶栏中部空（✅ 已补全局搜索，2026-11-17）着**：品牌在左、动作在右，中间 400–900px 是空的。最小可用方案是全局标的搜索（输入代码/名称 → 下拉直达个股分析），进阶是 ⌘K 命令面板（页面跳转 + 标的 + 常用动作）。这是「不像开源 demo、像自研终端」最直观的一步。
2. **侧栏无搜索**：导航项已到 20+，展开态靠滚动。图标条态有 tooltip，但没有过滤。
3. **图表大包**：`echarts` 单独 1.04MB（gzip 344KB），首屏进入含图的页面要等它。可改按需引入（只 import 用到的 series/component）。
4. 页面级密度切换、右侧新增面板 —— 已否决，不再列入。

## 5. 代码结构

- `components/Layout.tsx` 已从 1074 行降到 716 行：导航树 / 实时开关 / 用户块 / 主题切换 / 监控徽标分别拆到 `shell/{NavPanel,SidebarRealtime,UserBlock,ThemeToggle,MonitorBadge}.tsx`。`Layout` 只留状态、查询与外壳装配（`<aside>` 抽屉、`ContentPanel`、`StatusBar`、各 Host 组件）。
- 原先的 flaky `custom/signals/Signals.numeric-right.test.tsx` 已修（固定 `setTimeout` 改 `vi.waitFor` 等列表数据）。375px 窄屏已实测并修掉页头挤压、KPI 截断与抽屉不滑入三个问题，见 plan §14。

## 6. 建议的下一步顺序

| 顺序 | 内容 | 收益 | 风险 |
| --- | --- | --- | --- |
| 1 | 删 `Branding` 分页 + 三个孤儿模块（含测试） | 白标彻底闭环 | 低（无运行时引用） |
| 2 | `ChartTheme` 令牌化 | 图表与壳层同一套灰，暗色提升明显 | 低（只改颜色来源） |
| 3 | 六处高度补偿改 `h-full` | 消除底部空白 | 低 |
| 4 | ECharts 按需引入 | 首屏/图表页加载 | 中（需核对每个图表的 series） |
| 5 | 顶栏全局标的搜索 / ⌘K | 产品感 | 中（新增交互与接口调用） |
| 6 | 拆 `Layout` ✅ 已完成（1074 → 716 行，见 plan §12） | 可维护性 | 低但耗时 |
