# TSP 用户手册（HTML 文档站）

面向部署者与最终用户的静态 HTML 手册，单文件、无构建依赖，浏览器直接打开 `index.html` 即可使用，也可挂到任意静态服务器（Nginx / GitHub Pages / 对象存储）。

## 目录结构

```text
docs-site/
├── index.html            # 产品手册（面向部署者与最终用户）
├── delivery-manual.html  # [迁移前的旧版] 客户交付说明书单文件，已由 manual/ 取代，勿再手改
├── 财富引擎-交付说明书.pdf  # [迁移前的旧版] 上述 HTML 的打印版（54 页）
├── manual/               # 交付说明书的 Markdown 源 + VitePress 站点（现行维护入口，见 manual/README.md）
└── README.md             # 本维护说明
```

交付说明书自 2026-11-27 起改为「一章一个 MD」，网页版由 VitePress 构建，PDF 由 `npm run pdf` 生成；
日常修改只动 `manual/` 下的 Markdown，别再改 `delivery-manual.html`。

线上访问：`http://<部署实例>:3018/manual/`（后端挂载 `manual/.vitepress/dist`，发布用 `scripts/deploy_remote.sh --manual`）。

品牌 Logo 引用 `../brand/` 下的 SVG；移动 `docs-site/` 时请保持与 `brand/` 的相对位置，或把图片改为本地内联/同目录引用。

## 内容来源（维护时以这些权威文档为准）

| 手册章节 | 主要来源 |
| --- | --- |
| 快速开始 / 部署 / 更新 | `docs/deployment.md`、`README.md`「快速开始」 |
| 配置（数据源 / AI / 实时 / 通知） | `docs/configuration.md`、`docs/features.md` |
| 各功能页面操作 | `操作说明书.md`（26 章用户操作口径） |
| 开放接口 / Token / SSE / MCP | `docs/features.md`「开放接口」一节 |
| 数据维护 / 备份迁移 / 排错 | `操作说明书.md` 第 20、24、25、26 章 |
| 安全建议 | `操作说明书.md` 第 27 章、`docs/security-stability-review.md` |

> 手册是提炼与重组，不是逐字复制；功能口径与界面以运行版本「设置 → 系统设置」中的实际界面为准。文档与源码不一致时，先核对 `操作说明书.md` 与 `docs/` 对应文件，再回填本手册。

## 如何新增一个章节

1. **左侧导航**：在 `<nav id="nav">` 对应分组里加一条按钮：
   ```html
   <button class="nav-item" data-target="your-id">章节标题</button>
   ```
2. **内容区块**：在 `<main>` 内加一个 section，`id` 与上面的 `data-target` 一致：
   ```html
   <!-- ===== 章节：your-id ===== -->
   <section class="section" id="your-id">
     <h1>章节标题</h1>
     ...
   </section>
   ```
3. 导航顺序与 section 顺序保持一致；路由默认指向 `intro`。

## 写作约定

- 提示框三类：`<div class="callout tip">`（建议）、`warn`（注意/限制）、`danger`（风险/禁忌），标题用 `<div class="title">`。
- 代码用 `<code>`，多行命令用 `<pre><code>...</code></pre>`。
- 表格沿用现有 `<table>` 样式，无需额外类名。
- 端口、路径、能力键等以 `docs/configuration.md` 与源码为准；默认端口 `3018`。
- 不写投资收益承诺；涉及策略表现统一标注“研究结果，不构成投资建议”。

## 交付说明书（delivery-manual.html）

- 面向客户的验收文档，结构固定为「交付概览 → 访问与登录 → 系统总览 → 使用说明（17 节）→ 注意事项 → 字段说明 → 常见问题 → 交付验收」。
- 单文件自包含：截图以 JPEG data URI 内联、Logo 以 SVG data URI 内联，不依赖 `brand/` 与 `screenshots/`，可直接邮件发送或用浏览器「打印 → 另存为 PDF」。
- 字段说明的行情/指标表由 `backend/app/indicators/pipeline.py` 的 `ENRICHED_COLUMNS` 生成，新增字段时同步更新该表。
- 交付前需替换：封面「交付方 / 接收方」、访问地址、支持联系人、验收清单结果。
- PDF 版由同一份 HTML 打印生成，改完 HTML 必须重出 PDF（`--no-pdf-header-footer` 去掉页眉页脚）：

```bash
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
  --headless=new --disable-gpu --no-sandbox --user-data-dir=/tmp/chrome-pdf \
  --no-pdf-header-footer \
  --print-to-pdf="docs-site/财富引擎-交付说明书.pdf" \
  "file://$PWD/docs-site/delivery-manual.html"
```

- 打印样式里 `.section` 必须带 `animation:none!important;opacity:1!important`：页面切换有 `fadeIn` 动画，打印在动画首帧取图会导致首个可见小节整页空白（曾出现封面页空白）。
- 分页策略：只有「章」（`section.section.chapter`：封面/访问/总览/流程/注意事项/字段说明/常见问题/交付验收）强制另起一页，17 个模块小节连续排版；否则每个小节另起一页会在章末留下大量空白页。
- 打印时图片限高 `.figure img{max-height:104mm}`（登录页单独 128mm），避免整幅截图撑满一页把正文挤散；`h2/h3` 加 `page-break-after:avoid` 防止标题落在页脚。

## 截图维护（交付说明书内联图）

文档内 27 张界面图全部取自交付实例的真实界面（新品牌「财富引擎」浅色主题），内联为 JPEG data URI，单文件约 2.6MB。

重截流程（在有 Chrome 的 macOS 上）：

1. **取登录态**：生产无 ADMIN 变量，直接在 `/opt/tsp/backend` 的 venv 里铸造会话
   `TICKFLOW_ENV_FILE=/opt/tsp/.env PYTHONPATH=/opt/tsp/backend .venv/bin/python -c "from app.services import user_store; print(user_store.create_session(1))"`，
   写入本机临时文件（勿提交），Cookie 名 `tf_user_session`。
2. **驱动浏览器**：`puppeteer-core` + 本机 Chrome（`--headless=new --no-sandbox --disable-gpu --hide-scrollbars --no-proxy-server`），视口 1600×1000、`deviceScaleFactor 2`。
   必须加 `--no-proxy-server`：macOS 系统代理会让页面返回 `ERR_CONTENT_LENGTH_MISMATCH`。
3. **需要交互的页面**：`/stock-analysis`、`/financials` 不读 URL 参数，靠 `localStorage.last_stock` 预置标的；监控「新建规则」按钮无文字，用 `button[title="新建规则"]` 点击。
4. **裁切与压缩**：裁掉左侧菜单（源图 x≥478）与顶栏、底栏，只留内容区；登录页只裁中间卡片。统一缩放到宽 1280、JPEG q76；内容稀疏的页面（如自选仅两行）再按内容底部裁掉空白，避免图里大块留白。
5. **回填**：图片按 `alt` 精确定位替换 `<img>` 的 data URI（`alt` 与临时文件名一一对应），改完跑下方结构校验并重出 PDF。

隐私：截图中不得出现账号邮箱（设置页顶栏已裁掉）、密码、Token；`/tmp` 下的会话凭据用完后可在服务端删除对应 session。

## 校验

改完后在 `docs-site/` 目录跑一次结构校验（Python 标准库即可）：

```bash
python3 - <<'EOF'
import re
from html.parser import HTMLParser
src = open('index.html', encoding='utf-8').read()
nav = re.findall(r'data-target="([^"]+)"', src)
sec = re.findall(r'<section class="section" id="([^"]+)"', src)
assert nav == sec, (nav, sec)
VOID = {'meta','img','br','hr','input','source','link'}
class P(HTMLParser):
    def __init__(self): super().__init__(); self.stack=[]; self.errs=[]
    def handle_starttag(self,t,a):
        if t not in VOID: self.stack.append(t)
    def handle_endtag(self,t):
        if t in VOID: return
        if not self.stack: self.errs.append(t); return
        if self.stack.pop() != t: self.errs.append(t)
p=P(); p.feed(src)
assert not p.stack and not p.errs, (p.stack, p.errs)
print('OK:', len(sec), 'sections')
EOF
```

## 发布

- 本地查看：直接双击 `index.html`，或 `python3 -m http.server` 后访问。
- 静态托管：把 `docs-site/` 整个目录（连同 `brand/` 的相对路径）部署到任意静态站点；若只发布单文件，需把 Logo 改为可访问的 URL 或内联 SVG。
- 本文件不含外部 JS/CSS 依赖，无需联网即可使用。
