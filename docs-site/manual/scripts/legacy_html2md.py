#!/usr/bin/env python3
"""一次性迁移脚本：docs-site/delivery-manual.html → manual/ 下的 Markdown 与图片。

不参与日常构建，仅在需要从旧单文件 HTML 重新生成 MD 源时使用。转换规则：
- h1/h2/h3 → #/##/###
- p / ul / ol → Markdown 段落与列表
- table（无合并单元格）→ Markdown 表格；带 rowspan 的表格保留原始 HTML
- div.callout tip|warn|danger → VitePress 容器 ::: tip|warning|danger
- figure → <Figure src alt caption />
- div.cover / div.kpi / div.footer → 原始 HTML 块（结构性版式，内容很少改动）
- 内联 JPEG data URI → public/shots/sNN.jpg
"""
from html.parser import HTMLParser
import base64
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..'))
SRC = os.path.abspath(os.path.join(ROOT, '..', 'delivery-manual.html'))
OUT = ROOT
SHOTS = os.path.join(ROOT, 'public', 'shots')

VOID = {'img', 'br', 'hr', 'input', 'meta', 'link', 'source'}


class Node:
    def __init__(self, tag, attrs=None):
        self.tag = tag
        self.attrs = dict(attrs or [])
        self.children = []

    def text(self):
        out = []
        for c in self.children:
            out.append(c if isinstance(c, str) else c.text())
        return ''.join(out)

    def find(self, tag=None, cls=None):
        for c in self.children:
            if isinstance(c, str):
                continue
            if (tag is None or c.tag == tag) and (cls is None or cls in (c.attrs.get('class') or '').split()):
                return c
        return None

    def all(self, tag=None, cls=None):
        res = []
        for c in self.children:
            if isinstance(c, str):
                continue
            if (tag is None or c.tag == tag) and (cls is None or cls in (c.attrs.get('class') or '').split()):
                res.append(c)
            res.extend(c.all(tag, cls))
        return res


class Builder(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node('root')
        self.stack = [self.root]

    def handle_starttag(self, tag, attrs):
        node = Node(tag, attrs)
        self.stack[-1].children.append(node)
        if tag not in VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.stack[-1].children.append(Node(tag, attrs))

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                return

    def handle_data(self, data):
        self.stack[-1].children.append(data)


def norm_ws(s):
    return re.sub(r'[ \t\r\n]+', ' ', s)


def inline(node):
    """行内元素 → Markdown。"""
    out = []
    for c in node.children:
        if isinstance(c, str):
            out.append(norm_ws(c))
            continue
        t = c.tag
        if t in ('b', 'strong'):
            txt = inline(c).strip()
            # 中文标签常写成「**用途：**内容」，CommonMark 会因右边界是标点+汉字而不生效，
            # 把冒号移到加粗之外，既符合排版习惯也能被解析。
            m = re.match(r'^(.*?)([：:])$', txt)
            if m:
                out.append('**%s**%s' % (m.group(1), m.group(2)))
            else:
                out.append('**%s**' % txt)
        elif t in ('i', 'em'):
            out.append('*%s*' % inline(c).strip())
        elif t == 'code':
            out.append('`%s`' % c.text().strip())
        elif t == 'a':
            out.append('[%s](%s)' % (inline(c).strip(), c.attrs.get('href', '')))
        elif t == 'br':
            out.append('  \n')
        elif t == 'span':
            out.append(inline(c))
        else:
            out.append(inline(c))
    return ''.join(out)


def clean(s):
    return re.sub(r' {2,}', ' ', s).strip()


def esc_cell(s):
    return s.replace('|', '\\|').replace('\n', ' ')


def table_md(node):
    rows = node.all('tr')
    if not rows or any(r.find('td') and r.find('td').attrs.get('rowspan') for r in rows):
        return html_raw(node)
    lines = []
    head = rows[0]
    cells = [esc_cell(clean(inline(c))) for c in head.children if not isinstance(c, str) and c.tag in ('th', 'td')]
    if not any(c.tag == 'th' for c in head.children if not isinstance(c, str)):
        cells = ['列%d' % (i + 1) for i in range(len(cells))]
        body = rows
    else:
        body = rows[1:]
    lines.append('| ' + ' | '.join(cells) + ' |')
    lines.append('| ' + ' | '.join(['---'] * len(cells)) + ' |')
    for r in body:
        vals = [esc_cell(clean(inline(c))) for c in r.children if not isinstance(c, str) and c.tag in ('td', 'th')]
        vals += [''] * (len(cells) - len(vals))
        lines.append('| ' + ' | '.join(vals[:len(cells)]) + ' |')
    return '\n'.join(lines)


def html_raw(node):
    """原样输出 HTML（结构性版式用），并压缩空白。"""
    def ser(n):
        if isinstance(n, str):
            return n
        attrs = ''.join(' %s="%s"' % (k, v) for k, v in n.attrs.items())
        if n.tag in VOID:
            return '<%s%s>' % (n.tag, attrs)
        return '<%s%s>%s</%s>' % (n.tag, attrs, ''.join(ser(c) for c in n.children), n.tag)
    return ser(node)


FIGS = []


def figure_md(node):
    img = node.find('img')
    cap = node.find('figcaption')
    alt = img.attrs.get('alt', '') if img else ''
    src = img.attrs.get('src', '') if img else ''
    FIGS.append(src)
    name = 's%02d.jpg' % len(FIGS)
    save_image(src, name)
    caption = clean(cap.text()) if cap else ''
    return '<Figure src="/shots/%s" alt="%s" caption="%s" />' % (name, alt, caption)


def save_image(src, name):
    m = re.match(r'data:image/jpeg;base64,(.*)$', src, re.S)
    if not m:
        raise SystemExit('unexpected image src: %s' % src[:60])
    os.makedirs(SHOTS, exist_ok=True)
    with open(os.path.join(SHOTS, name), 'wb') as f:
        f.write(base64.b64decode(m.group(1)))


def callout_md(node):
    cls = (node.attrs.get('class') or '').split()
    kind = 'tip'
    for k in ('tip', 'warn', 'danger'):
        if k in cls:
            kind = {'tip': 'tip', 'warn': 'warning', 'danger': 'danger'}[k]
    title_node = node.find('div', 'title')
    title = clean(title_node.text()) if title_node else ''
    rest = [c for c in node.children if not (not isinstance(c, str) and c.tag == 'div' and 'title' in (c.attrs.get('class') or ''))]
    body = blocks(Node('box', []), rest)
    return '::: %s%s\n%s\n:::' % (kind, (' ' + title) if title else '', body)


BLOCK_TAGS = {'ul', 'ol', 'p', 'div', 'table', 'figure', 'pre', 'h1', 'h2', 'h3', 'h4'}


def has_block(node):
    return any(not isinstance(c, str) and c.tag in BLOCK_TAGS for c in node.children)


def blocks(parent, children=None):
    out = []
    for c in (parent.children if children is None else children):
        if isinstance(c, str):
            if c.strip():
                out.append(clean(c))
            continue
        t = c.tag
        cls = (c.attrs.get('class') or '').split()
        if t in ('h1', 'h2', 'h3', 'h4'):
            out.append('#' * int(t[1]) + ' ' + clean(inline(c)))
        elif t == 'p':
            out.append(clean(inline(c)))
        elif t in ('ul', 'ol'):
            items = []
            for i, li in enumerate([x for x in c.children if not isinstance(x, str) and x.tag == 'li']):
                marker = '%d.' % (i + 1) if t == 'ol' else '-'
                body = blocks(li) if has_block(li) else clean(inline(li))
                body = body.replace('\n', '\n  ')
                items.append('%s %s' % (marker, body))
            out.append('\n'.join(items))
        elif t == 'figure':
            out.append(figure_md(c))
        elif t == 'table':
            out.append(table_md(c))
        elif t == 'div' and 'callout' in cls:
            out.append(callout_md(c))
        elif t == 'div' and ('cover' in cls or 'kpi' in cls or 'footer' in cls or 'meta' in cls):
            out.append(html_raw(c))
        elif t == 'div':
            out.append(blocks(c))
        elif t in ('pre',):
            out.append('```\n%s\n```' % c.text())
        else:
            out.append(clean(inline(c)))
    return '\n\n'.join(x for x in out if x and x.strip())


def main():
    raw = open(SRC, encoding='utf-8').read()
    css = re.search(r'<style>(.*?)</style>', raw, re.S).group(1)
    body = raw.split('<body', 1)[1]
    nav_html = body.split('<nav id="nav">', 1)[1].split('</nav>', 1)[0]

    groups = []
    for m in re.finditer(r'<div class="nav-group">\s*<div class="nav-group-title">([^<]+)</div>(.*?)\n    </div>', nav_html, re.S):
        title = m.group(1).strip()
        items = re.findall(r'data-target="([^"]+)">([^<]+)</button>', m.group(2))
        groups.append({'title': title, 'items': [{'id': a, 'label': b} for a, b in items]})

    sec_re = re.compile(r'<section class="section( chapter)?" id="([\w-]+)">(.*?)</section>', re.S)
    sections = []
    for m in sec_re.finditer(body):
        sections.append({'id': m.group(2), 'chapter': bool(m.group(1)), 'html': m.group(3)})

    builder = Builder()
    builder.feed(''.join('<section id="%s">%s</section>' % (s['id'], s['html']) for s in sections))
    doc = {n.attrs['id']: n for n in builder.root.children if isinstance(n, Node)}

    # 输出目录与命名
    layout = {
        'cover': ('index.md', '交付概览'),
        'access': ('access.md', '访问与登录'),
        'overview': ('overview.md', '系统总览与菜单'),
        'workflow': ('workflow.md', '每日使用流程'),
        'notes': ('notes.md', '注意事项'),
        'fields': ('fields.md', '字段说明'),
        'faq': ('faq.md', '常见问题'),
        'accept': ('accept.md', '交付验收与支持'),
    }
    module_dir = 'modules'
    os.makedirs(os.path.join(OUT, module_dir), exist_ok=True)

    manifest = []
    for s in sections:
        node = doc[s['id']]
        title = clean(node.find('h1').text()) if node.find('h1') else s['id']
        md = blocks(node)
        if s['id'] in layout:
            path, _ = layout[s['id']]
        else:
            path = '%s/%s.md' % (module_dir, s['id'][2:] if s['id'].startswith('m-') else s['id'])
        with open(os.path.join(OUT, path), 'w', encoding='utf-8') as f:
            f.write(md.rstrip() + '\n')
        manifest.append({'id': s['id'], 'file': path, 'title': title, 'chapter': s['chapter']})

    with open(os.path.join(OUT, 'scripts', 'manifest.json'), 'w', encoding='utf-8') as f:
        json.dump({'groups': groups, 'sections': manifest}, f, ensure_ascii=False, indent=2)
    with open(os.path.join(OUT, 'scripts', 'legacy.css'), 'w', encoding='utf-8') as f:
        f.write(css.strip() + '\n')

    print('sections:', len(manifest), 'images:', len(FIGS), 'groups:', [(g['title'], len(g['items'])) for g in groups])


if __name__ == '__main__':
    main()
