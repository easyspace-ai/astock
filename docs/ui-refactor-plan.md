# TSP 外壳重构与全局样式计划（参考 Craft Agents）

> 目标：只重做 **应用外壳（TopBar / 导航 / 内容容器）** 与 **全局样式（颜色 / 字体 / 图标 / 面板质感）**，
> 参考 `/film/craft-agents-oss` 的壳层结构与设计令牌。
> **TSP 各页面内部的内容、信息架构与排版一律不动**：页面文件不改布局，只继承新令牌带来的颜色与字体变化。
>
> 本文是路线图与验收基准。P0–P4 各阶段独立可交付、可停。

---

## 1. 范围与边界

### 1.1 包含

| 项 | 说明 |
| --- | --- |
| TopBar | 新增全局顶栏：品牌、全局搜索、市场状态/指数、数据同步、AI、主题、账户 |
| 导航 | 保留现有导航项、顺序、路由、展开/图标条两态与移动抽屉；只重绘视觉 |
| 内容容器 | 页面外加一层「纸面 panel」：圆角、发丝边框、与导航的 6px 间隙；内部不加 padding |
| 全局颜色 | 语义令牌（navigator / paper / raised / sunken / floating + 文字四档 + 边框三档 + 强调色） |
| 全局字体 | 沿用 Inter + JetBrains Mono（与 Craft 同族），只定义壳层字号角色与字重/字距 |
| 图标 | 沿用 `lucide-react`，统一壳层图标的尺寸、描边、对齐；`BrandIcons` 按同一网格重绘 |
| 面板质感 | 高程、圆角、发丝线、滚动条、焦点环、动效时长 |
| 主题 | 保留暗色默认 / 亮色；新增品牌主题包与高对比预留 |
| 持久化 | 侧栏两态、主题、品牌主题；沿用现有 localStorage + 后端偏好机制 |

### 1.2 明确不动

- `pages/*`、`custom/*` 的页面内部布局、卡片排列、表格列、图表尺寸、弹窗结构。
- `PageHeader` 的高度、标题字号、右侧操作区布局（只允许颜色随令牌变化）。
- 路由、导航项、`nav_layout` v1、`nav_hidden`、8 个现有插槽的 context。
- 后端 API、查询键、SSE、数据口径、`pageZoom` 机制。
- 页面级密度（不做 compact/comfortable 切换，避免改表格行高）。

### 1.3 设计红线

1. **页面文件零布局改动**：本次只改 `components/Layout.tsx`、新增 `shell/*`、`styles/*`、`tailwind.config.ts`；页面目录的 diff 只允许出现在「颜色/字体令牌替换」这类纯样式行。
2. **壳层不吞内容宽度**：面板边框用 `box-shadow` 发丝线而非 `border`，避免 1px 挤压导致表格横向滚动；总宽度损失控制在 12px 内（6px 间隙 ×2）。
3. **A 股语义色不动**：`bull/bear` 保持现状，只用于价格；UI 状态不复用。
4. **不改页面滚动模型**：内容面板不是滚动容器，页面原有的 `min-h-full` / `sticky` 头部继续在页面内生效。
5. **弹窗不动**：`Modal` 仍是页面级组件，本次不统一改造，避免牵连 27 个页面。

---

## 2. 现状（与壳相关的证据）

| 指标 | 数值 | 结论 |
| --- | --- | --- |
| 外壳 | `components/Layout.tsx` 1,336 行 | 导航、行情、状态、Host、弹窗全在一处；需拆 |
| 布局 | `gridTemplateColumns: 14rem / 3.5rem / 0` | 224px 展开、56px 图标条，与 Craft 的 232/56 基本一致，**保留尺寸** |
| 页面 | 25 个 `pages/*` + 17 个 `custom/*` | 本次全部不改内部布局 |
| 颜色 | `bg-base 508` / `bg-surface 451` / `bg-elevated 905` / `border-border 1,662` | 三层表面不够用，弹窗/菜单/卡片同质感 |
| 文字 | `text-muted 2,380` / `text-foreground 1,332` / `text-secondary 1,281` | 层级可用，但缺少统一字号角色 |
| 字号 | `text-[10px] 1,155` / `text-[11px] 1,050` | 任意值集中在页面内，本次不动页面，仅在壳层禁用新任意值 |
| 硬编码色 | 45 个文件、419 处 hex | 主要在页面/图表；壳层先清零，页面随令牌继承 |
| 字体 | Inter 300–900 + JetBrains Mono 400–700（自托管） | 与 Craft 同族，**不换字体**，只统一壳层用法 |
| 图标 | `lucide-react` 165 个文件；尺寸 `h-3.5` 587、`h-3` 391、`h-4` 338；`strokeWidth` 覆盖 52 处 | 尺寸碎片化，壳层需规范 |
| 主题 | `html.dark` + `tf-theme` 预渲染脚本 | 机制可复用，令牌需扩展 |
| 扩展 | 8 个插槽，v1；壳层无插槽 | 顶栏/导航需要新插槽 |

---

## 3. 参考实现拆解（只取壳层）

| 机制 | Craft 数值/做法 | TSP 落地 |
| --- | --- | --- |
| 顶栏 | 44px，固定，全局动作 + 工作区切换 | 44px 全局顶栏；不承载页面级操作 |
| 面板几何 | gap 6、edge inset 6、内圆角 12、贴边 8 | 直接采用；边框改发丝阴影以避免挤压 |
| 表面分层 | navigator / paper / raised / sunken / floating | 采用五层；页面 `base→navigator`、`surface→paper`、`elevated→raised` |
| 导航 | 232 展开 / 56 图标条，分组、徽标、底部账户 | 保留 TSP 现有 224/56 与分组结构，只重绘视觉 |
| 面板头 | `PanelHeader` 48px，标题/徽标/操作 | 仅用于壳层（顶栏、导航头）；**不替换页面 PageHeader** |
| 动效 | 0/100/150/200/300ms，reduced-motion 归零 | 壳层使用 150/200ms；页面动画不变 |
| 高程 | elev-0 发丝环 → elev-3 弹窗 | 仅导航/内容面板用 elev-0/1；页面卡片暂不升级阴影 |
| 字体角色 | 11 caption / 12 label / 13 body，紧字距 | 定义壳层角色；页面字号不动 |

不做：Tailwind 4 `@theme`、Electron 标题栏、Radix/jotai、聊天式 20/32/48 分栏、30px 衬线标题。

---

## 4. 目标外壳

```
┌─ TopBar 44 ───────────────────────────────────────────────────────────────┐
│ Logo TSP │ ⌘K 搜索 │ 上证 +0.19% · 深成 -1.19% · 创业板 … │ 同步 │ AI │ 主题 │ 头像 │
├──────────┬──────────────────────────────────────────────────────────────────┤
│ Nav 224  │ ContentPanel（paper，圆角 12，发丝环）                             │
│ 展开/56  │  ┌ 页面原样：PageHeader / 工具条 / 表格 / 图表 / 卡片 ┐            │
│ 分组     │  │  页面自己的滚动、粘性头、列宽、间距全部保持不变      │            │
│ 扩展入口 │  └───────────────────────────────────────────────────┘            │
│ 实时状态 │                                                                  │
│ 用户     │                                                                  │
└──────────┴──────────────────────────────────────────────────────────────────┘
   6px 间隙 + 6px 外边距；无右侧面板，不改变页面可用宽度语义
```

### 4.1 几何

| 项 | 值 | 备注 |
| --- | --- | --- |
| TopBar 高 | 44px | 页面粘性头从 44px 之下开始 |
| 导航宽 | 224 / 56 | 保留现值，避免页面宽度变化 |
| 面板间隙 | 6px | 仅导航与内容之间 |
| 窗口内边距 | 6px（上/右/下） | 左侧贴边，保留桌面客户端观感 |
| 面板圆角 | 12px | 内容面板四角 |
| 发丝环 | `inset 0 0 0 1px` | 不用 border，避免 1px 宽度损失 |
| 高程 | elev-0（1px 环 + 1px 阴影） | 不开大阴影，保持终端冷静 |

### 4.2 断点

| 宽度 | 行为 |
| --- | --- |
| ≥ 1200 | 完整：TopBar（3 个指数 + 行情状态）+ 导航展开 + 内容面板 |
| 1024–1199 | TopBar 隐藏第三个指数与行情状态胶囊；导航仍可展开 |
| 980–1023 | 导航强制图标条；顶栏动作文字收窄 |
| < 980 | 导航变 56px 图标条；< 768 时导航改为抽屉（现有行为，视觉重绘）；TopBar 只留品牌、搜索、动作、头像 |

---

## 5. 全局样式系统

### 5.1 颜色令牌（已按 Craft 落地）

| 令牌 | 角色 | 暗色（Craft） | 亮色（Craft） | 页面别名 |
| --- | --- | --- | --- | --- |
| `--surface-navigator` | 画布：顶栏、导航、窗口底 | `#0D0E10` / `220 10% 6%` | `#F2F3F4` / `210 8% 95%` | `base` |
| `--surface-paper` | 内容纸面 | `#151618` / `220 7% 9%` | `#FCFDFE` / `210 50% 99%` | `surface` |
| `--surface-raised` | 纸面上的卡片/悬浮项 | `#1A1B1D` / `220 5% 11%` | `#FCFDFE` | `elevated` |
| `--surface-sunken` | 凹槽：壳层搜索框、状态块 | `#111214` / `220 8% 7%` | `#F4F5F6` / `210 10% 96%` | 新增（仅壳层） |
| `--surface-floating` | 浮层：壳层菜单/命令面板 | `#1F2022` / `220 5% 13%` | `#FCFDFE` | 新增（仅壳层） |
| `--text-primary / secondary / tertiary` | 文字三档 | `#ECEDEF / #C3C4C6 / #9E9FA1` | `#1A1C1E / #424446 / #5D5F60` | `fg-primary / secondary / muted` |
| `--text-disabled` | 禁用态 | `#545557` | `#A3A4A6` | 新增 |
| `--border-default` | 常规边框 | `#2A2B2D` / `220 3% 17%` | `#E7E8E9` / `210 4% 91%` | `border` |
| `--border-strong` | 强调分隔 | `#383A3B` | `#DBDCDD` | 新增 |
| `--accent-solid` | 品牌强调（按钮/选中） | `#8B78D8` / `252 55% 66%` | `#6F49CA` / `258 55% 54%` | `accent` |
| `--accent-text` | 链接/彩色文字 | `#B29FFF` | `#6942C3` | 新增 |
| `--bull / --bear` | 涨 / 跌（仅价格） | `#F04438 / #12B76A` | 不变 | 同名 |

**强调色的取舍**：Craft 用两个令牌分工（`accent-solid` 给按钮底、`accent-text` 给彩色文字），
而 TSP 只有一个 `--accent` 同时承担两者。直接取 Craft solid `#7E66CA`：白字对比度 4.53 合格，但彩色文字只有 4.0；
取 text `#B29FFF`：文字 8.0，但白字按钮掉到 2.26。因此暗色取两者中点 `#8B78D8`（白字 3.6 / 文字 5.0），
与改造前电光蓝（白字 3.7 / 文字 4.9）的对比度曲线一致，视觉上是 Craft 紫。亮色 `#6F49CA` 两项均 ≥ 5.9，直接用 Craft solid。

**其他取舍**：`bg-elevated` 有 905 处，既做卡片又做 hover。本次不改页面，因此：
- `elevated → raised`（卡片稍亮，hover 仍可用）；
- 不拆分 hover 语义，避免动 905 个页面调用点；
- 新增的 `sunken/floating` 只给壳层用，页面弹窗维持现状（`bg-surface`）。
- 边框按 Craft 的 10% 前景混合取实色（暗色 `#2A2B2D`），比原来的 `#353539` 更收敛，表格仍可辨认。

> **P1a 已落地（2026-11-17）**：令牌写入 `frontend/src/index.css`，`tailwind.config.ts` 暴露
> `navigator/paper/raised/sunken/floating` 与 `accent-solid/accent-text`、壳层字号四档；
> 页面文件零改动，`pnpm build` 通过（8.18s）。

### 5.2 字体

| 角色 | 规格 | 使用范围 |
| --- | --- | --- |
| 品牌字 | Inter 600，12px，字距 .12em，大写 | TopBar 品牌 |
| 顶栏标签 | Inter 500，11px/16 | 指数、动作 |
| 导航项 | Inter 500，12px/16；激活 500 + 主色 | 导航 |
| 导航分组 | Inter 500，10px，字距 .08em，大写 | 分组标题 |
| 数字 | JetBrains Mono 400/500，`tabular-nums` | 指数、徽标数字 |
| 字体栈 | `Inter, "HarmonyOS Sans SC", "PingFang SC", system-ui` | 不变 |
| 渲染 | 保留现有 `subpixel-antialiased` 与 `font-feature-settings` | 不变 |

不新增字体、不改页面字号；壳层禁止 `text-[10px]` 这类任意值，统一走 `text-caption` / `text-label`。

### 5.3 图标

| 项 | 规范 |
| --- | --- |
| 库 | 沿用 `lucide-react`，不引入新图标库 |
| 尺寸 | `icon-xs 12`（徽标）、`icon-sm 14`（导航次级）、`icon 16`（顶栏/导航主）、`icon-lg 20`（空态） |
| 描边 | 统一 `strokeWidth 1.75`（现状 52 处覆盖，壳层先收敛） |
| 颜色 | `currentColor`，激活态用 `--accent`，非激活 `--fg-muted` |
| 品牌图标 | `BrandIcons.tsx` 按 20×20 网格、1.75 描边重绘 7 个导航图标，保持单色 |
| 对齐 | 图标与文字 `gap 10`（展开）/ 居中（rail），视觉重心对齐基线 |

### 5.4 面板与质感

- 内容面板：`border-radius 12`、`box-shadow: inset 0 0 0 1px var(--border), 0 1px 2px rgba(0,0,0,.28)`。
- 导航栏：无边框，靠 `navigator` 与 `paper` 的明度差分区；分组间用 `--border-subtle` 发丝线。
- 滚动条：沿用现有 8px、`border` 色，hover 主色；导航滚动条继续悬停显形。
- 焦点环：`outline: 2px solid color-mix(accent 60%, transparent)`，偏移 2px；键盘可见、鼠标不显示（`:focus-visible`）。
- 动效：导航开合 200ms `cubic-bezier(.16,1,.3,1)`；hover 120ms；`prefers-reduced-motion` 归零。

---

## 6. 代码改造

### 6.1 新增结构（P2 实际落地）

```text
frontend/src/components/shell/
├── TopBar.tsx          # 44px 顶栏：导航开关 + 品牌 + 指数条 + 全局动作
├── IndexStrip.tsx      # 顶栏指数条（自侧栏上移，路由/数据契约不变）
└── ContentPanel.tsx    # 内容纸面：12px 圆角 + 1px 发丝环 + 内部滚动

frontend/src/components/Layout.tsx   # 仍是唯一装配点（1,088 行）
```

未做：`NavPanel.tsx` 拆文件、`useShellPrefs.ts` 抽偏好、`AppShell.tsx` 门面化。
导航分组/图标条/自选二级菜单的状态与 `Layout` 深度耦合，硬拆需要大量 props 透传，
本轮先保证视觉与行为正确，文件拆分留到 P3。

### 6.2 迁移步骤

1. 把 `Layout.tsx` 中「导航渲染」「用户块」「实时开关」原样搬到 `NavPanel`，**逻辑不改**，仅重绘样式；侧栏原有的指数条上移到 `TopBar`（不再在 224px 导航内重复展示）。
2. `TopBar` 复用现有组件：`RealtimeToggle`、`ThemeToggle`、同步按钮、AI 入口、`UserBlock`（改为 `variant="topbar"`）。
3. `Layout.tsx` 保留：`OnboardingGuard` 之后的渲染、`StockPreviewHost`、`StockAnalysisHost`、`AiAnalysisHost`、挖掘队列弹窗；这些挂载点不变。
4. 页面出口改为 `<ContentPanel><Outlet/></ContentPanel>`；**不包 padding、不改高度链**，页面 `min-h-full` 继续生效。
5. 保留 `tf-nav-collapsed`、`tf-nav-width`（如有）、`nav_layout` 读写键，避免用户偏好丢失。

### 6.3 契约保持

| 契约 | 处理 |
| --- | --- |
| 路由与导航项 | 不动；`CORE_NAVIGATION` / 扩展导航原样使用 |
| `nav_layout` v1 | 读写逻辑原样搬入 `useShellPrefs` |
| 8 个 v1 插槽 | context 原样透传，不改 key |
| `pageZoom` | 仍作用于 `#root`；壳层不得引入 `100vh` 硬高 |
| 主题事件 `tf-theme-change` | 保留；新增品牌主题时沿用同一事件 |

---

## 7. 分阶段计划

| 阶段 | 内容 | 关键文件 | 验收 | 工期 |
| --- | --- | --- | --- | --- |
| **P0 基线** | 分支、8 页 × 暗/亮 × 3 宽度截图、壳层截图基线、`/dev/design` 令牌页、ESLint 禁令 | `pages/Dev.tsx`、eslint 配置 | `pnpm build` 绿；基线入库 | 1–2d |
| **P1 令牌与全局样式** | tokens/base 拆分；Tailwind 映射 + 页面别名；字体角色；壳层图标规范；焦点环/滚动条 | `styles/*`、`tailwind.config.ts`、`BrandIcons.tsx` | 页面**仅颜色/字体变化**，截图 diff 可逐页审；无布局位移 | 3–4d |
| **P2 外壳重构** | `AppShell/TopBar/NavPanel/ContentPanel`；`Layout` 门面化；断点与抽屉；偏好迁移 | `shell/*`、`Layout.tsx` | 导航项/路由/两态/抽屉行为不变；页面宽度损失 ≤12px | 5–6d |
| **P3 定制能力** | 品牌主题包、顶栏动作插槽、导航徽标插槽、主题切换入口 | `themes/*`、`extensions/*` | 不改 `Layout` 即可加顶栏动作；品牌色一键切换 | 3–4d |
| **P4 回归发布** | 全量截图 diff、窄屏、缩放、性能、升级预检 | `scripts/upgrade_check.py` | 见 §9 | 2d |

合计：单人 2.5–3 周；后端零改动，页面文件近乎零改动。

> **P2 已落地（2026-11-17）**：顶栏 44px、6px 面板间隙、12px 圆角内容纸面、导航去边框并上移品牌、
> 指数条上移顶栏、`layout.header.quick` 插槽随之上移（key 不变）。`pnpm build` 通过（9.55s）。
> 实测看板/回测两页、明暗主题、224px 与 56px 两种导航态均正常。

> **P4 已落地（2026-11-17）· 亮色层级与信息架构**：
> 1. 亮色重建五表面阶梯 —— `paper #FCFDFE` / `raised #FFFFFF` / `sunken #F1F2F4` / `floating #FFFFFF`（此前 paper=raised=floating 同值，只能靠 127 级发丝线分层，故发灰）；发丝线 `border-default` 由 `#E7E8E9` 提到 `#DCDEE1`，`border-strong #C9CCD0`。
> 2. `ContentPanel` 亮色补极浅投影（暗色不加），把画布/纸面的图底关系立起来。
> 3. 顶栏指数条下沉为底部 **28px 状态栏**（`shell/StatusBar.tsx`：四指数 + 实时行情状态 + 数据源 + 同步态），顶栏只剩品牌与全局动作，消除“顶栏/看板/指数页”三处重复。
> 4. 侧栏去掉 `EXT` 小标签（开源扩展架构的内部痕迹），导航默认仍 `expanded`。
> 构建 9.07s EXIT 0，997px 实拍明暗两态通过。
>
> **P6 已落地（2026-11-17）· 统一页头**
> 仓库本就有 `components/PageHeader.tsx`（26 个页面已在用），所以这步不是铺新组件，而是**升级这个共享组件的视觉契约**，一次影响全部引用方：
> 1. 标题从 20px 紫色渐变裁剪字改为 **15px 中性色**（渐变字是开源版本最显眼的设计痕迹，也和多页面标题字号不一致）；
> 2. 新增 `icon` 插槽（16px accent 盒），说明文案从 10px `muted` 改为 11px，行高与顶栏对齐；
> 3. 容器统一 `min-h-11`（44px）+ 底部发丝线 + `px-4`，页面可用 `className` 覆盖 sticky / 背景 / 内衬（`cn` 走 twMerge，后传优先）；
> 4. `right` 收到一个 `flex min-w-0` 容器里，页面操作按钮不再各自撑高页头。
> 迁移：看板的渐变 hero 头（Gauge + 情绪徽标 + 日期/重载/自定义布局）整体换成 PageHeader（徽标走 `titleExtra`，控件走 `right`）；指数页的内联 `<h1>指数</h1>+<p>` 同样迁移。
> 尚未迁移：`Regime`（自有 `regime-heading` CSS + Activity 图标，需连 CSS 一起改）、`AnalysisDetail`、`AdminApp`；`Auth` / `Onboarding` 是入口页，不按终端页头处理。
> 构建 12.05s EXIT 0；Dashboard / Indices / extensions 41 项测试通过。
>
> **导航 / 页面底色分工（2026-11-17）**：亮色下此前页面根容器普遍写 `bg-base`（画布灰 #F2F3F4），而导航也是画布灰，左右连成一片；少数页面用纸面色，于是「有的页面好看、有的糊在一起」。
> 1. 新增 `--surface-page` 令牌（亮色 = 纸面 #FCFDFE，暗色 = 画布 #0D0E10），Tailwind 暴露为 `bg-page`；页面根容器统一改用 `bg-page`。
> 2. 顶栏 `TopBar` 改铺纸面色；画布灰只留给导航栏，形成「导航灰 / 内容白」的左右分工（对齐参考图）。
> 3. 迁移页面：`Dashboard`（含 3 处加载/空态）、`Backtest`、`Factors`、`Indices`，以及这些页面的吸顶页头 `bg-base/95 → bg-page/95`（含 `backtest/MiningWorkbench` 底栏）。
> 4. 暗色不变：`--surface-page` 与画布同色，仍靠卡片层级区分，不引入新的亮暗差异。
> 构建 10.52s EXIT 0；Dashboard / Indices / Dashboard.status 13 项测试通过。

> **Regime 页头迁移（2026-11-17）**：`<header className="regime-heading">` 换成共享 `PageHeader`（`Activity` 走 `icon`、副标题按 `view` 切换、`className="mb-3.5 px-0"` 贴合页面内衬、原 `.regime-toolbar`（1年/2年/全部/自定义/重算）整体进 `right`）；`Regime.css` 删除 `.regime-heading`、`.regime-heading-copy`、`.regime-heading-icon`、`.regime-heading h1/p` 及 3 处移动端覆盖。构建 9.32s EXIT 0，`Regime.test.tsx` 10 项通过，明暗实拍见 `regime-heading-dark.png` / `regime-heading-light.png`。
> `AnalysisDetail` 核对结论：该页是薄包装（`template==='url'` 走 `ExtUrlEmbed`，其余走 `ExtDimensionAnalysis`），后者第 255 行已用 `PageHeader`，故无需再改。`AdminApp` 为独立全屏管理台（自有 sticky 顶栏、`max-w-[1500px]`），不属交易终端页头体系，暂不动。
>
> **P7 白标 · 剥离上游痕迹（第一步，2026-11-17）**：
> 1. `components/screener/StrategyBuilderDialog.tsx` 两处「策略开发指南」外链（指向上游 `backend/app/strategy/prompts/strategy-guide.md`）整段移除，步骤说明文案保留。
> 2. `custom/financials/Page.tsx` 的 Issues 外链与「欢迎在 Issues 中多多推荐」改为「当前财务数据源需付费,后续会接入更多免费数据源;接入后在数据源设置里切换即可。」
> 3. `pages/settings/DataSourceEditor.tsx` 指向上游 `docs/custom-data-source.md` 的「AI 帮你整理映射 →」锚点移除。
> 4. `pages/settings/About.tsx` 整体改写为内部版「关于」：只留产品信息（定位一句话 + 当前版本）与「数据与口径」三条（盘后数据来源、实时行情权限、账号/偏好本地存储）；版权署名、官网、打赏（含 `/donate-wechat.jpg`、放大 Modal）、OSS 许可弹窗、检查更新（含 `?autoupdate=1` 深链）全部删除。`OssLicenseDialog` / `data/ossLicenses.ts` 因此无引用方（文件保留、不再进包）。
> 5. `components/Layout.tsx` 侧栏版本号旁的「发现新版本」下载徽标与图标条红点一并删除（它读上游 Releases，点击会跳 `/admin?tab=about&autoupdate=1`），`useUpdateCheck` 从壳层摘除。**`lib/updateCheck.ts` 本身未改**：目前仅剩它自己的单测在调用，运行时已无入口，是否彻底删除待定。
> 6. 测试修正：`Layout.realtime.test.tsx` 在 `beforeEach` 显式置 `tf-nav-state=expanded`（终端默认图标条后，多数用例仍断言展开态菜单，此前 3 项因此红）。
> 构建 10.21s EXIT 0；`Layout.realtime.test.tsx` 12 项测试通过。
> 7. 品牌字样（2026-11-17）：`Layout` 顶栏、`Logo` aria-label、`Auth`、`Onboarding`、`index.html` 标题统一为「TSP 量化终端」（保留 TSP 缩写、去掉上游英文全称）；`Branding.tsx` 展示页同名字替换（页面去留未定）。构建 10.30s EXIT 0，实拍 `brand-tsp-light.png`。
> 8. 品牌定名（2026-11-17）：产品名定为**「财富引擎」**，替换顶栏、`Logo` aria-label、`Auth`、`Onboarding`、`Branding` 展示页与 `index.html` 标题中的旧字样（上游英文全称 `Tick Stock Panel` 已不再出现）。构建 9.90s EXIT 0，实拍 `brand-caifu-light.png`。
> 9. Logo 换新（2026-11-17）：原图标是上游的方括号 + K 线，与「财富引擎」不匹配。先出 4 个候选（六边核心+上箭头 / 三辐引擎 / 阶梯上升 / 环+箭头）做 14/18/24/40px 明暗同屏预览（`logo-candidates.png`），选定**六边形引擎核心 + 向上推力箭头**：六边形读作设备/核心舱而非代码边界，箭头表达推力与增长。`components/Logo.tsx` 重写为 3 条路径，仍是 `currentColor`，随品牌包自动变色。构建 10.35s EXIT 0，顶栏明暗实拍 `logo-hex-arrow-light.png` / `logo-hex-arrow-dark.png`。
> 10. Logo 二次调整（2026-11-17）：按用户要求改为「六边形里一根突破的 K 线」。先做 4 组对比（开口穿出 / 闭合内嵌 / 大开口粗阳线 / 实心镂空），小尺寸下开口方案会糊成断环，最终选**实心六边形 + 负空间镂空的长阳线**（`mask` 镂空 body 与上下影线，阳线实体加宽到 4.4/32、影线 1px）。`Logo.tsx` 用 `useId` 生成唯一 mask id，避免同页多实例冲突。构建 11.42s EXIT 0，实拍 `logo-kline-light.png` / `logo-kline-dark.png`；候选对比 `logo-kline-candidates.png`。
> **P8 图表令牌化 + 高度补偿（2026-11-17）**：
> 1. `lib/theme.ts` 的 `ChartTheme` 不再写死旧灰阶（`#A1A1AA/#27272A`），改为运行期读壳层令牌：`--text-secondary / --text-primary / --border-default / --surface-raised / --surface-floating / --surface-sunken`，透明度走 `hsl(... / a)`。保留一份无 DOM 环境的兜底值（单测/SSR）。这样坐标轴、网格、tooltip 与卡片边、正文是同一套灰，明暗切换自动跟随。已在真实浏览器里验证 `getComputedStyle` 能解析别名变量、canvas 接受 `hsl(210 6% 87% / 0.5)` 语法。
> 2. 新增 `--shell-chrome-y: 78px`（顶栏 44 + 间隙 6 + 状态栏 28），`MiningWorkbench` 三处 `calc(100vh-9rem)` 改为 `calc(100dvh - var(--shell-chrome-y))`，不再凭空少给 66px。其余 `review`（22/26rem）与 `stock-analysis`（220px）是页内分区自己的高度上限，来源不是壳层 chrome，保持不动。
> 构建 9.72s EXIT 0；图表相关 4 个测试文件 55 项通过；实拍 `chart-theme-dark.png` / `chart-theme-light.png`。
> **P9 删除上游孤儿模块（2026-11-17）**：
> 删除 `pages/Branding.tsx`（4 种风格语言展示页）、`lib/updateCheck.ts` + 8 项测试、「检查更新」链路、`data/ossLicenses.ts` + `components/OssLicenseDialog.tsx`（138 行 OSS 许可弹窗）。同步摘除：`AdminApp` 的 branding 分节（import / `AdminSection` 联合类型 / `SECTIONS` 条目 / 渲染分支 / `Palette` 图标）、`router` 的 `/admin/branding` 路由与 `/branding` 重定向及 `CORE_ROUTE_PATHS` 条目、`Layout` 的 `ADMIN_ONLY_NAV` 品牌项、`AssistantLauncher` 的「品牌设置」路由标题、两处测试里的 mock 与断言。`noUnusedLocals` 下构建 8.43s EXIT 0；全量 118 文件 / 807 用例全通过。
> 顺带修掉一处因 P8 引入的回归：`useChartTheme()` 每次渲染返回新对象，图表 effect 依赖它导致重复 `setOption`（`Regime` 键盘切换用例从 3 次变 5 次）。已改为 `useMemo(() => chartTheme(theme), [theme])`，调色板随主题稳定。
> **P10 顶栏全局标的搜索（2026-11-17）**：
> 顶栏中部原来空着，现补 `components/shell/GlobalSearch.tsx`：440px 以内的常驻入口（放大镜 + 「搜索标的」+ `⌘K` 提示），`⌘K / Ctrl+K` 全局唤起，弹层走 `bg-floating` + `border` + 24 遮罩 + 2px 模糊，与壳层同一套令牌。
> 数据复用现成的 `api.instrumentSearch(q, 12, 'stock,index,etf')`，60s 缓存；键盘 ↑↓ 选择、Enter 打开、Esc 关闭。选中后跳 `/stock-analysis?symbol=xxx`，个股页新增 URL 深链读取（`useSearchParams` + `instrumentNames` 补名称），不再需要在页内重搜。宽度小于 md 时入口隐藏，避免挤占指数条与动作区。
> 实测：输入「茅台」返回贵州茅台 600519.SH，回车落到个股分析并自动渲染 K 线；构建 10.45s EXIT 0，全量 118 文件 / 807 用例通过。实拍 `topbar-search-idle.png` / `topbar-search-open.png` / `topbar-search-land.png`。
> **P11 ECharts 按需引入（2026-11-17）**：
> 新增 `src/lib/echarts.ts` 作为唯一注册入口：`echarts/core` + `use([...])` 只注册实际用到的 6 种图表（line / bar / candlestick / pie / heatmap / custom）、9 个组件（grid / tooltip / axisPointer / legend / dataZoom / markLine / markArea / visualMap / graphic）和 canvas 渲染器；7 个原本 `import * as echarts from 'echarts'` 的文件改为 `import { echarts } from '@/lib/echarts'`。适配层对外仍暴露完整命名空间的类型面（`core as unknown as typeof import('echarts')`），调用方无需改类型写法，避免 `EChartsType` / `EChartsOption` 的类型漂移。
> 产物对比：ECharts 分块 **1013KB → 653KB**（−35.5%），gzip **344KB → 222.8KB**（−35.2%），构建 7.96s。风险点在于「漏注册的系列会静默不画」，因此新增 `src/lib/echarts.test.ts` 守门测试：用 SSR + SVG 渲染器把六种系列和关键组件各跑一遍，断言 console 无 "series is not exists"，当前 3 项通过。
> 同步把 5 个测试文件的 `vi.mock('echarts')` 改为 `vi.mock('@/lib/echarts', () => ({ echarts: {...} }))`（Candlestick / Intraday / SectorRotation / Regime / echarts smoke）。全量 119 文件 / 810 用例通过。
> 实拍验收（按需引入后）：个股分析 K 线（candlestick + markLine 压力/支撑 + markArea 成交密集区）正常；市场环境页 line + bar + dataZoom 滑块 + markLine 阶段线 + 饼图状态分布正常，console 无 series 缺失。自定义页（概念板块等）在免费账号下会被守卫重定向回看板，属权限问题而非图表问题；heatmap / custom 系列由 `echarts.test.ts` 的 SSR 渲染覆盖。
> 待定：`pages/Branding.tsx`「4 种风格语言」展示页去留；`Tick Stock Panel` / `TSP` 字样仍在 `Layout.tsx`（品牌字）、`Logo.tsx`（aria-label）、`Auth.tsx`、`Onboarding.tsx`，需先定新名称。

> **看板宽度 bug 已修（2026-11-17）**：内容宽到 941px（图标条态）时，`indices` / `kpi` 卡片只画出第一格、其余大片空白。根因不是 RGL 网格（格子宽度一直是 899px），而是组件卡片上的 `backdrop-blur-sm` —— 同屏多张毛玻璃卡在更宽容器下触发合成层失效；去掉这些卡片的 `backdrop-blur-sm` 后全部正常（顺带减少 GPU 开销，卡片本来就铺在不透明表面上）。另一处是真溢出：`kpi` 大盘指标是两行 6 格，2 行高（70px）放不下，已把 `minH/defH` 提到 3 行，`DEFAULT_LAYOUT` 从上往下整体下移 1 行（分布/雷达/趋势/梯队 y=5，热度/监控 y=13，榜单 y=22）。图标条（56px）现在是默认导航态；展开态（224px）同样回归通过。

### 7.1 分阶段可停点

- P1 结束后即使不做壳，页面也会获得统一的新配色与新焦点环，可独立发布。
- P2 结束后壳已可用，P3 纯增强。

---

## 8. 页面不受影响的保障措施

| 风险 | 保障 |
| --- | --- |
| 内容宽度变窄导致表格横滚 | 间隙 6px×2 + 发丝环不用 border；在 1024 宽逐页检查 `overflow-x` |
| 页面粘性头错位 | 内容面板不设 `overflow`，不创建新的滚动上下文；页面 `sticky top-0` 仍在自身滚动容器内 |
| 双滚动条 | `ContentPanel` 仅 `overflow: hidden` 用于圆角裁切，滚动仍由页面内部容器负责 |
| 页面高度链断裂 | 保持 `html/body/#root` 100% 与 `min-h-full` 链；壳层用 `h-full` 而非 `100vh` |
| `pageZoom` 缩放 | 壳层边框用 1px 实线发丝环；0.8×/1.5× 下回归检查圆角与描边 |
| 抽屉/浮层 portal | 抽屉仍 `fixed`；顶栏下拉用 portal + `--z-dropdown`，不被面板圆角裁切 |
| 图表宽度 | 壳层不改变页面容器宽度语义；仅总宽减 12px，ECharts `ResizeObserver` 自动跟随 |

---

## 9. 验收矩阵

| 类型 | 验证 |
| --- | --- |
| 构建 | `cd frontend && pnpm build`（每阶段） |
| 单测 | `pnpm exec vitest run`；重点 `Layout.realtime.test.tsx`、`navLayout.test.ts`、`navOrder.test.ts` |
| 页面零改动 | `git diff --stat frontend/src/pages frontend/src/custom` 只允许样式令牌行；布局相关行数为 0 |
| 视觉 | 8 页 × 暗/亮 × 1440/1280/1024；壳层额外 768 |
| 壳层交互 | 导航展开/收起、分组弹层、抽屉、主题切换、⌘K、滚动与粘性头 |
| 缩放 | `pageZoom` 0.8 / 1.25 / 1.5 下无描边丢失、无圆角锯齿 |
| 性能 | 首屏 bundle 增量 ≤ 3%；导航开合 60fps；无新增重复请求 |
| 兼容 | `nav_layout` v1、`tf-nav-collapsed`、8 个插槽、旧深链、旧主题值 |
| 升级 | `python3 scripts/upgrade_check.py <上游Tag>`；`git diff --check` |

完成标准：

- 页面目录无布局 diff；壳层文件全部走令牌，0 处 hex、0 处 `text-[10px]`。
- 三档宽度无横向滚动、无粘性头错位；暗/亮下正文对比度 ≥ 4.5:1，控件边框 ≥ 3:1。
- 导航项、顺序、徽标、扩展入口与改造前逐项一致。

---

## 10. 深度定制能力（壳层）

| 能力 | 接入 | 分级 |
| --- | --- | --- |
| 品牌主题 | `index.css` 的 `html[data-brand]` 规则覆盖 `--accent-solid/text/raw`（表面/文字/边框/涨跌色不动）；顶栏调色盘切换，偏好走 `tf-brand-theme` + 后端 `brand_theme` | L1 |
| 顶栏动作 | 插槽 `topbar.actions`（无 context，扩展渲染 28px 图标按钮） | L2 |
| 导航徽标 | 现有导航扩展 `badge` + 新增 `badgeTone`（`accent/neutral/warning/danger`，缺省 warning 保持琥珀） | L2 |
| 壳层布局 | 展开/图标条两态已足够；不提供自由拖拽分栏 | L1 |
| 密度 | 本期不做；如后续需要，仅作用于壳层（导航行高、顶栏高度） | 未来 |

品牌包：`craft`（默认雾紫）、`ocean`、`jade`、`amber`、`rose`；各包均按「白字 ≥3.4、纸面 ≥4.5」折中取值，`index.html` 首屏脚本先挂 `data-brand` 避免闪烁。`meta[name=theme-color]` 随品牌包与明暗改写（暗/亮各一色），首屏脚本与 `syncThemeColor()` 两处同步。

**P3 已落地（2026-11-17）**：新增 `lib/brandTheme.ts`、`components/shell/BrandThemePicker.tsx`；`types.ts` 加 `topbar.actions` 与 `badgeTone`，`registry.ts` 校验色阶取值，`lib/navigation.ts` 透传；`uiPrefs.ts` 加 `tf-brand-theme → brand_theme`（并入 `REMOTE_FIRST`），后端 `preferences.py` 的 `_UI_PREFS_KEYS` 加一行 `brand_theme`；`Layout.tsx` 的 `BRAND` 改为 `hsl(var(--accent-solid))`，logo 跟随品牌包。

偏差说明：原计划要求新插槽配 `apiVersion: 2`。实施时按**附加式、保持 v1** 落地 —— 全仓只有 v1 扩展、`registry`/`runtime` 严格比对 1，新增槽位属于向后兼容的加法，不构成破坏性契约变更；拆 v2 会在无扩展场景下留下一层空的版本分支。后续若出现 v1 兼容性破坏（如 context 形状变更），再引入版本层。

---

## 11. 决策记录

| 编号 | 决策 | 结论 | 影响 |
| --- | --- | --- | --- |
| D1 | 品牌强调色 | **已定：整体按 Craft 配色**。亮色 `#6F49CA`，暗色 `#8B78D8`（Craft solid/text 中点） | 全站选中态、链接、按钮底；页面颜色整体偏紫，图表主线保持不变 |
| D2 | 页面卡片是否跟随新表面色 | **已定：跟随**（`elevated → raised`） | 壳与页面同一套表面色，避免割裂 |
| D3 | 导航宽度 | 保留 224 / 56 | 改 232 会挤压页面 8px |
| D4 | 顶栏内容 | 品牌、搜索、指数、同步、AI、主题、账户；不含页面级按钮 | 页面级操作留在 PageHeader |
| D5 | 亮色主题 | 保留并同步新令牌 | 已随 P1 一并落地，仍需一轮亮色截图验收 |

---

## 12. P12 拆 `Layout.tsx`（2026-11-17）

审计清单最后一项。`Layout.tsx` 原本 1074 行，导航渲染、实时开关、用户块、主题切换、监控徽标全部内联，壳层逻辑与 20+ 个导航项的 JSX 混在同一个文件里。

拆法（**纯重构，零视觉变更**：迁移的 JSX 与原实现逐字一致，只把闭包变量换成 props）：

| 新文件 | 职责 | 行数 |
| --- | --- | --- |
| `shell/NavPanel.tsx` | 导航树：页面项 / `NavFolder` 分组 / 图标条弹层 / 自选二级分组（`全部` + 分组涨跌）/ 数据同步转圈与完成勾 / 监控未读徽标 / `layout.navigation.extra` 插槽。`BADGE_TONE_CLASS` 一并迁入 | 224 |
| `shell/SidebarRealtime.tsx` | 侧栏底部全局行情开关：图标条态状态灯、展开态数据源/状态 + 设置入口 + `role="switch"`，含「自选实时前 5 只」「同步期间暂停」两条提示 | 154 |
| `shell/UserBlock.tsx` | 用户块 + `ROLE_LABEL/ROLE_CLASS`；登出同时注销账号会话与面板会话、清 query cache、回登录页 | 78 |
| `shell/ThemeToggle.tsx` | 明暗切换按钮 | 17 |
| `shell/MonitorBadge.tsx` | 未读徽标（`monitor_badge_enabled` 开关、`99+` 截断） | 16 |

`Layout.tsx` 保留：状态与查询（`navState/drawerOpen/overlayPreview/railMode`、分组展开与图标条弹层、自选分组、`realtime*` 派生、`handleToggle/doEnableRealtime`、挖掘排队确认）、`<aside>` 外壳（抽屉 / overlay 预览 / 移动端关闭）、底部「管理后台 / 个人中心」入口、`ContentPanel + motion.main + Outlet`、`Toast/Alert/Ai/Stock` 各 Host、`StatusBar`。**1074 → 716 行（−33%）**。

顺带清掉 `noUnusedLocals` 下的 12 个残留 import（`NavigationEntry`、`NavFolder`、`CheckCircle2`、`ChevronRight/Down`、`Sun/Moon`、`LogOut`、`resolveWatchlistGroupColor`、`groupPctColor/groupPctTitle`、`fmtPct`，以及迁走后的 `useTheme/toggleTheme`、`useUnreadAlerts`）；`lib/echarts.test.ts` 的 `SVGRenderer` 注册加了一处显式类型转换，避免 `use()` 的重载与 `typeof import('echarts')` 类型面冲突。

验收：构建 9.03s EXIT 0；全量 **119 文件 / 810 用例全通过**（36.85s）。实拍 `layout-split-light.png`（看板，内容区、状态栏、`PageHeader` 均正常）、`layout-split-drawer.png`（窄屏抽屉态导航完整渲染）。

---

## 13. 修复扩展页 / 核心页整页深链失效（2026-11-17）

**现象**：直接整页打开 `/stock-analysis?symbol=600519.SH`、`/regime`、`/indices`、`/concept-analysis` 一律落到看板 `/`；点侧栏（客户端跳转）正常。

**真实根因**（此前怀疑的「扩展路由注册晚于首次路由解析」并不成立 —— `/indices`、`/regime` 是核心路由，同样复现）：

1. 冷启动时 `OnboardingGuard` 先跑了一遍 `useSession`，`account-status` / `auth-status` 还没落定（`status=pending`、`fetchStatus=idle`），此时 `isLoading` 为 false、`authenticated` 为 false，守卫据此判定「未登录」，把页面重定向到 `/login?redirect=<原路径>`。
2. 登录页 `Auth` 挂载后发现其实已登录（`api.accountStatus()` 直连返回 `authenticated:true`），但自动跳转写死 `navigate('/')`，**把 `?redirect=` 丢了**，于是落到看板。
3. 而且 `redirectTarget()` 若在读 URL 时才解析，第一次跳转已把 query 清空，会二次弹回 `/`。

**改动**：

- `pages/Auth.tsx`：新增 `redirectTarget()`（只接受 `/` 开头的站内绝对路径，拒绝 `//host` 与绝对 URL，防开放重定向），挂载时用 `useState(redirectTarget)` 一次性锁定落地地址 `redirectTo`，四处「已登录/验证完成」跳转全部改用它（原先写死 `navigate('/')`）。
- `lib/useAccount.ts`：`loading` 判定从 `isLoading` 改为 `isPending`，覆盖「已挂载但首次请求尚未发出」（pending + idle）的窗口，避免守卫拿空数据误判未登录。
- 新增 `pages/Auth.redirect.test.ts`（jsdom，4 项）：无参数回看板、深链含查询串、核心页深链、拒绝 `//evil` 与绝对 URL。

**验收**：四条深链整页打开均正确落地并渲染（`deeplink-concept.png` 为 `/concept-analysis` 实拍）；`/login?redirect=%2Fregime` → `/regime`，`/login?redirect=%2F%2Fevil.example.com` → `/`。构建 8.25s EXIT 0；全量 **120 文件 / 814 用例**通过（39.75s）。

---

## 14. 收尾两项：flaky 用例 + 375px 窄屏（2026-11-17）

### 14.1 `Signals.numeric-right.test.tsx` 偶发失败

根因不是业务逻辑，而是**固定延时等异步渲染**：用例用 `setTimeout(120)` 等首屏，整套并发跑时回调时机被挤压，断言落在「页头已渲染、列表数据还没到」的窗口里 → `expect(...).toContain('0.6')` 偶发失败。

改成 `vi.waitFor` 轮询，并且**等到列表行（`残月入场`）真的出现**再断言（只等页头 `信号库` 会提前通过，这正是之前偶发的来源）。条件编辑器那处也一样换成 `waitFor` 等 `input[type=number]` 挂载。

验证：单文件连跑 4 次、整套连跑 3 次全绿（814 用例）。

### 14.2 375px 窄屏实测与修复

用 375×812 的 iframe 承载真实应用（同源、带登录态）实测，暴露三处问题：

| 问题 | 修复 |
| --- | --- |
| 页头在 375px 被压成一行，标题/状态徽标被挤成竖排、控件换行错位 | `PageHeader` 容器加 `flex-wrap` + 左块 `flex-1`、标题行允许换行、`right` 在 `<sm` 占满整行并横向滚动 |
| KPI 六宫格 3 列时数值被截断（`3541/239/…`、`1.8% / 0.…`） | KPI 容器 `grid-cols-2 sm:grid-cols-3 xl:grid-cols-6`；`KpiCell` 数值 `text-[15px] sm:text-lg`，标签行允许换行 |
| **移动端抽屉只出遮罩、面板不滑入**（真 bug，非窄屏专属） | `aside` 上 `translate-x-0` 与实际计算值冲突（计算 transform 仍停在 `-100%`），改为内联 `style.transform` 显式控制；层级 `z-50 → z-[60]` 压过顶栏动作 |

抽屉 bug 影响所有移动端/窄窗口（此前只做桌面截图所以没暴露），`Layout.realtime.test.tsx` 相应把「断言 class 含 `-translate-x-full`」改成断言内联 `translateX(0)` / `translateX(-100%)`，顺带守住打开态。

实拍：`mobile-375-dashboard.png`（看板，页头两行、KPI 两列完整数值）、`mobile-375-drawer.png`（抽屉滑入，导航与实时行情/用户块完整）、`mobile-375-backtest.png`（回测页窄屏）。

---

## 15. AdminApp 视觉语言与新壳对齐（2026-11-17）

管理后台此前是**另一套壳**：`min-h-screen` 全屏、56px `sticky` 自绘顶栏、`max-w-[1500px]` 居中，和前台的 44px 纸面顶栏 + 画布灰边 + 12px 圆角纸面**完全不同源**，切换过去像换了个产品。

对齐方式（**只换壳语言，不动后台内容与信息架构**）：

| 项 | 改前 | 改后 |
| --- | --- | --- |
| 根容器 | `min-h-screen bg-base` 全屏 | `flex h-full flex-col overflow-hidden bg-base`（画布只露四周） |
| 顶栏 | 56px 自绘、`border-b`、`backdrop-blur`、`max-w-1500` 居中 | **44px `bg-page` 纸面色**，与 `TopBar` 同一几何：Logo 18px accent、`财富引擎` 11px、`管理后台` 胶囊、分节 tab（`rounded-btn`、选中 `bg-elevated` + 图标 accent）、右侧邮箱/角色胶囊/`ThemeToggle`/返回前台/退出 |
| 内容 | 裸 `main` 平铺 | `p-1.5 pt-0` + `ContentPanel`（12px 圆角 + 发丝环 + 浅投影）+ `main` 内部滚动 `scrollbar-gutter-stable` |
| 页面标题 | `embedded` 模式直接把 `PageHeader` 隐藏 → 纸面内**没有任何标题** | `Settings` / `Data` 的 `PageHeader` 取消 `!embedded` 包裹，始终渲染；内嵌时标题分别为 `系统配置` / `数据管理`，并补 `icon`（`Settings2` / `Database`） |
| Settings 竖向 tab | `sticky top-20`（为旧 56px 顶栏预留的偏移） | 内嵌态 `sticky top-4`（顶栏已不在滚动容器内） |

顺带修掉两个存量问题：

- `Growth.tsx` 里 `SORT_LABEL` 从未被引用（`tsc --force` 全量检查才暴露），删除；
- 后台顶栏原先没有主题切换，补上 `ThemeToggle`，与前台保持一致。

提交前临时把本地账号提权到 `admin` 实拍，拍完已还原为 `free`。

实拍：`admin-settings-light.png`、`admin-settings-dark.png`、`admin-data-light.png`。

---

## 16. 面板上沿丢线 + 顶栏与导航同色（2026-11-17）

### 16.1 内容面板上沿的发丝线时有时无

现象：看板等页面，内容面板顶部那条 1px 界线看不到（只看到一片白贴着顶栏），个股分析等页面却正常。

根因：面板的界线是用**容器自身的 `inset box-shadow`** 画的，而纸面里不少页面自己又铺了一层 `bg-surface`。子元素背景是画在父元素 `box-shadow` **之上**的，于是页面内容一铺白底，就把上沿/左沿那 1px 盖掉了 —— 所以看起来「有的页面有线、有的没有」。

修复：把发丝环改成面板里的**独立覆盖层**（`absolute inset-0 z-30 rounded-panel` + `inset` 盒阴影 + `pointer-events-none`），叠在内容之上，不再受子元素背景影响；容器只保留外投影。层级抬到 `z-30`，避免页面内 `sticky` 头部的层级再盖回去。

### 16.2 顶栏底色改成与左侧导航同色

参考给到的 Craft 界面：header 与左侧 rail 是同一块灰底，只有内容纸面是亮块。原实现顶栏铺纸面色（`bg-page`）、画布灰只留导航，左右确实分开了，但 header 与 rail 成了两种色。

改：`TopBar` 由 `bg-page` → `bg-base`（画布色），header 与导航列连成一块 L 形灰底，白纸面成为画面里唯一的亮块；内容与外围仍靠发丝环 + 浅投影分开。`AdminApp` 顶栏同步改色，保持同源。

实拍：`panel-ring-dashboard-light.png`、`panel-ring-stock-light.png`、`panel-ring-stock-dark.png`。

---

## 17. 顶栏胶囊从「实心白」改为「描边/半透明」（2026-11-17）

顶栏底色转成画布灰后，里面那几个白胶囊（全局搜索、AI 助手）显得比内容纸面还亮，抢视线。

全局搜索入口：`border-border/70 bg-sunken/70`（在亮色下 `sunken` 与画布几乎同色，实际上是一块「死灰」）→ 改成**描边为主**：

```
border border-border bg-transparent
hover:border-border-strong hover:bg-elevated/30
```

即常态只有 1px 发丝描边界定形状，悬停时才浮出一点点底。AI 助手入口本来就是 `bg-elevated/25 + border`，在这套规则下正好同族，未改。

这样顶栏只剩文字和一个「框」，视觉重心回到内容纸面。暗色同理：边框走 `--border`，与其它组件一致。

实拍：`topbar-outline-pill-light.png`
