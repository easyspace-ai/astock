# 财富引擎交付说明书 · 文档源

客户交付说明书（访问方式 / 使用说明 / 注意事项 / 字段说明 / 验收）的 Markdown 源与站点工程。
内容以本目录为准，旧的单文件 `../delivery-manual.html` 是迁移前的生成物，不再手工维护。

## 目录

```text
manual/
├── index.md                 # 封面 + 交付清单 + 文档结构（含原始 HTML 版式块）
├── access.md                # 访问与登录
├── overview.md              # 系统总览与菜单
├── workflow.md              # 每日使用流程
├── modules/*.md             # 17 个功能模块（看板、自选、策略……）
├── notes.md  fields.md  faq.md  accept.md
├── public/
│   ├── logo-mark.svg / favicon-mark.svg   # 顶栏 logo 与标签页图标（取自 frontend/public/favicon.svg）
│   └── shots/s01.jpg…s27.jpg   # 界面截图（27 张）
├── .vitepress/              # 站点配置与主题
│   ├── config.ts            # 侧边栏 / 搜索 / 站点信息
│   └── theme/{index.ts,Figure.vue,custom.css}
└── scripts/
    ├── manifest.json        # 章节清单（导航分组、文件、标题、是否章节起页）
    ├── build-print.mjs      # MD → 单文件 HTML → PDF
    ├── legacy.css           # 打印/版式样式（从旧版手册完整继承）
    └── legacy_html2md.py    # 一次性迁移脚本，平时不用跑
```

## 常用命令

```bash
cd docs-site/manual
npm install          # 首次
npm run dev          # 本地写作预览 http://localhost:5199，改 MD 热更新
npm run build        # 出静态站，产物在 .vitepress/dist
npm run pdf          # 出 dist-print/ 下的单文件 HTML 与 PDF（需要本机 Chrome）
```

## 发布到线上

后端把构建产物挂载在 `/manual`（`backend/app/main.py` → `ManualStaticFiles`），首次挂载或改动挂载逻辑后需要重启后端：

```bash
bash scripts/deploy_remote.sh --manual    # 构建（DOCS_BASE=/manual/）并上传，不重启
bash scripts/deploy_remote.sh --backend   # 只在新增/修改挂载时才需要
```

访问 `http://<host>:3018/manual/`。命令行验证：

```bash
curl -s -o /dev/null -w '%{http_code}\n' http://47.119.36.147:3018/manual/access
```

线上是干净 URL（`/manual/access`），磁盘文件是 `access.html`，由 `ManualStaticFiles` 在查表阶段回退，
不需要 nginx rewrite。目录不存在时后端整段跳过挂载，所以本地/桌面版不受影响。

## 写作约定

- 一章一个 `.md`，第一个 `#` 标题即章节标题；侧边栏文字在 `scripts/manifest.json` 里配。
- 新章节：加 MD 文件 → 在 `manifest.json` 的 `sections` 与对应 `groups` 里各加一条，侧边栏自动生成。
- **截图**：图片放 `public/shots/`，正文写
  `<Figure src="/shots/s01.jpg" alt="看板下半屏" caption="图注文字" />`。
  图注必填（PDF 里的说明行来自 caption），点击图片在网页版可放大。
- **提示框**：`::: tip 标题` / `::: warning 标题` / `::: danger 标题`，以单独一行 `:::` 结束。
  三种分别对应建议、注意/限制、风险/禁忌。
- **表格**：用标准 Markdown 表格；需要合并单元格时直接写 HTML `<table>`（目前只有「菜单结构」一张）。
- 代码与命令用行内反引号或 ``` 代码块；端口、路径、能力键以 `docs/configuration.md` 与源码为准，默认端口 `3018`。
- 不写投资收益承诺；涉及策略表现统一标注「研究结果，不构成投资建议」。
- 封面里的「交付方 / 接收方 / 访问地址 / 支持联系人」是占位符，交付前替换。

## PDF 出线

`npm run pdf` 会把所有 MD 按 `manifest.json` 的顺序渲染成一个 `<section>`，套 `legacy.css`
的打印规则后交给 headless Chrome：

- 章节（`cover` / `access` / `overview` / `workflow` / `notes` / `fields` / `faq` / `accept`）强制另起一页；
  17 个模块连续排版，避免章末大片空白。
- 图片限高 `104mm`（登录页 `128mm`），防止整幅截图撑满一页。
- 截图在产物里内联为 base64，得到的 `delivery-manual.html` 仍是单文件，可直接邮件发送。
- 校验口径：与迁移前的 54 页 PDF 逐页比对，页数与分页一致、无空白页。

改样式时优先改 `scripts/legacy.css`（PDF）与 `.vitepress/theme/custom.css`（网页），
两者的颜色变量同源，但选择器不同（`.vp-doc` 前缀）。

## 迁移记录

2026-11-27 由 `../delivery-manual.html`（单文件、25 节、27 张内联截图）迁移而来：
`scripts/legacy_html2md.py` 负责拆章节与解出图片，`legacy.css` 原样继承旧样式，
PDF 与旧版逐页比对一致（54 页）。迁移后修过一处转换瑕疵：中文「**用途：**正文」因
CommonMark 右边界规则不生效，已把冒号移到加粗之外。
