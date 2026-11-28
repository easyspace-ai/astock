#!/usr/bin/env node
/**
 * Markdown → 单文件 HTML → PDF（交付给客户的版本）。
 *
 * 复用旧版单文件手册的打印样式（scripts/legacy.css），这样网页版（VitePress）与
 * PDF 版的字体、表格、提示框、分页规则保持同源：
 *   - 每个 .md 渲染成一个 <section>，chapter 章节强制另起一页
 *   - 截图内联为 base64，产物仍是一个可直接邮件发送的 HTML
 *
 * 用法：npm run pdf  →  dist-print/delivery-manual.html + dist-print/财富引擎-交付说明书.pdf
 */
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { execFileSync } from 'node:child_process'
import MarkdownIt from 'markdown-it'
import container from 'markdown-it-container'

const here = path.dirname(fileURLToPath(import.meta.url))
const root = path.resolve(here, '..')
const outDir = path.join(root, 'dist-print')
const manifest = JSON.parse(fs.readFileSync(path.join(here, 'manifest.json'), 'utf8'))
const legacyCss = fs.readFileSync(path.join(here, 'legacy.css'), 'utf8')

const md = new MarkdownIt({ html: true, linkify: false, typographer: false })
for (const kind of ['tip', 'warning', 'danger']) {
  md.use(container, kind, {
    render(tokens, idx) {
      if (tokens[idx].nesting === 1) {
        const title = tokens[idx].info.trim().slice(kind.length).trim()
        return `<div class="callout ${kind === 'warning' ? 'warn' : kind}">${
          title ? `<div class="title">${md.utils.escapeHtml(title)}</div>` : ''
        }\n`
      }
      return '</div>\n'
    },
  })
}

/** <Figure src alt caption /> → 可打印的 figure。 */
function figures(text) {
  return text.replace(/<Figure\s+([^>]*?)\/>/g, (_, attrs) => {
    const get = name => (attrs.match(new RegExp(`${name}="([^"]*)"`)) || [, ''])[1]
    const caption = get('caption')
    return `<figure class="figure"><img src="${get('src')}" alt="${get('alt')}">${
      caption ? `<figcaption>${caption}</figcaption>` : ''
    }</figure>`
  })
}

function dataUri(src) {
  if (!src.startsWith('/shots/')) return src
  const file = path.join(root, 'public', src)
  return `data:image/jpeg;base64,${fs.readFileSync(file).toString('base64')}`
}

const sections = manifest.sections.map(s => {
  const raw = fs.readFileSync(path.join(root, s.file), 'utf8')
  const body = md.render(figures(raw)).replace(/src="(\/shots\/[^"]+)"/g, (_, src) => `src="${dataUri(src)}"`)
  return `<section class="section${s.chapter ? ' chapter' : ''}" id="${s.id}">\n${body}\n</section>`
})

const printCss = `
/* 本文件由 build-print.mjs 生成：屏幕预览时也要显示全部小节 */
@media screen { .section { display: block !important; } }
.custom-block { border: 1px solid; border-radius: 10px; padding: 12px 16px; margin: 16px 0; }
.custom-block.tip { background: var(--tip-bg); border-color: var(--tip-border); }
.custom-block.warning { background: var(--warn-bg); border-color: var(--warn-border); }
.custom-block.danger { background: var(--danger-bg); border-color: var(--danger-border); }
.custom-block .custom-block-title { font-weight: 700; margin-bottom: 4px; }
.custom-block p { margin: 6px 0; }
@media print {
  .custom-block, .figure, .callout { page-break-inside: avoid; }
}
`

const html = `<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>财富引擎交付说明书</title>
<style>${legacyCss}</style>
<style>${printCss}</style>
</head>
<body>
<div class="layout">
<main class="main" id="main">
${sections.join('\n')}
</main>
</div>
</body>
</html>
`

fs.mkdirSync(outDir, { recursive: true })
const htmlPath = path.join(outDir, 'delivery-manual.html')
fs.writeFileSync(htmlPath, html)

const pdfPath = path.join(outDir, '财富引擎-交付说明书.pdf')
const chrome = process.env.CHROME_BIN
  || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
execFileSync(chrome, [
  '--headless=new',
  '--disable-gpu',
  '--no-sandbox',
  '--no-pdf-header-footer',
  '--user-data-dir=/tmp/chrome-tsp-manual-pdf',
  `--print-to-pdf=${pdfPath}`,
  `file://${htmlPath}`,
], { stdio: 'inherit' })

const mb = n => (n / 1024 / 1024).toFixed(1) + 'MB'
console.log(`html: ${htmlPath} (${mb(fs.statSync(htmlPath).size)})`)
console.log(`pdf : ${pdfPath} (${mb(fs.statSync(pdfPath).size)})`)
