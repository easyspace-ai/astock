# TSP 用户手册（HTML 文档站）

面向部署者与最终用户的静态 HTML 手册，单文件、无构建依赖，浏览器直接打开 `index.html` 即可使用，也可挂到任意静态服务器（Nginx / GitHub Pages / 对象存储）。

## 目录结构

```text
docs-site/
├── index.html            # 产品手册（面向部署者与最终用户）
├── delivery-manual.html  # 客户交付说明书：访问方式/使用说明/注意事项/字段说明（单文件，截图与 Logo 已内联，可直接发给客户）
├── 财富引擎-交付说明书.pdf  # 上述 HTML 的打印版（52 页），随 HTML 一并更新
└── README.md             # 本维护说明
```

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
