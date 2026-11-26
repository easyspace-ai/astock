# TSP 用户手册（HTML 文档站）

面向部署者与最终用户的静态 HTML 手册，单文件、无构建依赖，浏览器直接打开 `index.html` 即可使用，也可挂到任意静态服务器（Nginx / GitHub Pages / 对象存储）。

## 目录结构

```text
docs-site/
├── index.html      # 手册本体：内联 CSS / JS，hash 路由 + 左侧导航 + 搜索过滤
└── README.md       # 本维护说明
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
