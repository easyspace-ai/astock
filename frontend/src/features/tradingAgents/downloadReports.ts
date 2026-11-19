import { AGENT_META } from './agentMeta'
import { sanitizeReportMarkdown } from './reportText'

function triggerDownload(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  URL.revokeObjectURL(url)
}

function escapeHtml(s: string): string {
  return s
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
}

/** Very small Markdown → printable HTML (headings, lists, code, paragraphs). */
function markdownToPrintableHtml(md: string): string {
  const lines = md.replace(/\r\n/g, '\n').split('\n')
  const out: string[] = []
  let inUl = false
  let inOl = false
  let inCode = false
  let codeBuf: string[] = []

  const closeLists = () => {
    if (inUl) { out.push('</ul>'); inUl = false }
    if (inOl) { out.push('</ol>'); inOl = false }
  }

  const inline = (text: string) =>
    escapeHtml(text)
      .replace(/`([^`]+)`/g, '<code>$1</code>')
      .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
      .replace(/\*([^*]+)\*/g, '<em>$1</em>')

  for (const raw of lines) {
    const line = raw
    if (line.startsWith('```')) {
      if (inCode) {
        out.push(`<pre><code>${escapeHtml(codeBuf.join('\n'))}</code></pre>`)
        codeBuf = []
        inCode = false
      } else {
        closeLists()
        inCode = true
      }
      continue
    }
    if (inCode) {
      codeBuf.push(line)
      continue
    }
    if (!line.trim()) {
      closeLists()
      continue
    }
    const h = line.match(/^(#{1,4})\s+(.+)$/)
    if (h) {
      closeLists()
      const level = h[1].length
      out.push(`<h${level}>${inline(h[2])}</h${level}>`)
      continue
    }
    const ul = line.match(/^[-*•]\s+(.+)$/)
    if (ul) {
      if (inOl) { out.push('</ol>'); inOl = false }
      if (!inUl) { out.push('<ul>'); inUl = true }
      out.push(`<li>${inline(ul[1])}</li>`)
      continue
    }
    const ol = line.match(/^\d+\.\s+(.+)$/)
    if (ol) {
      if (inUl) { out.push('</ul>'); inUl = false }
      if (!inOl) { out.push('<ol>'); inOl = true }
      out.push(`<li>${inline(ol[1])}</li>`)
      continue
    }
    closeLists()
    out.push(`<p>${inline(line)}</p>`)
  }
  closeLists()
  if (inCode) out.push(`<pre><code>${escapeHtml(codeBuf.join('\n'))}</code></pre>`)
  return out.join('\n')
}

export function downloadMarkdownFile(markdown: string, filename: string) {
  const blob = new Blob([markdown], { type: 'text/markdown;charset=utf-8' })
  triggerDownload(blob, filename.endsWith('.md') ? filename : `${filename}.md`)
}

/** Open a print dialog so the user can save as PDF (works offline, no heavy deps). */
export function downloadPdfViaPrint(opts: {
  title: string
  subtitle?: string
  markdown: string
}) {
  const body = markdownToPrintableHtml(sanitizeReportMarkdown(opts.markdown))
  const html = `<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8"/>
<title>${escapeHtml(opts.title)}</title>
<style>
  @page { margin: 18mm; }
  body { font-family: "PingFang SC","Hiragino Sans GB","Microsoft YaHei",system-ui,sans-serif;
    line-height: 1.65; color: #0f172a; max-width: 800px; margin: 0 auto; padding: 24px; }
  h1 { font-size: 22px; margin: 0 0 4px; }
  .sub { color: #64748b; font-size: 12px; margin-bottom: 20px; }
  h2 { font-size: 18px; margin: 1.4em 0 0.5em; border-bottom: 1px solid #e2e8f0; padding-bottom: 4px; }
  h3 { font-size: 15px; margin: 1.2em 0 0.4em; }
  h4 { font-size: 14px; margin: 1em 0 0.3em; }
  p { margin: 0.55em 0; }
  ul, ol { margin: 0.4em 0 0.4em 1.2em; }
  li { margin: 0.2em 0; }
  code { background: #f1f5f9; padding: 1px 4px; border-radius: 3px; font-size: 0.9em; }
  pre { background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 8px; padding: 12px; overflow-x: auto; }
  pre code { background: none; padding: 0; }
</style>
</head>
<body>
  <h1>${escapeHtml(opts.title)}</h1>
  ${opts.subtitle ? `<p class="sub">${escapeHtml(opts.subtitle)}</p>` : ''}
  ${body}
  <script>window.onload=function(){setTimeout(function(){window.print()},180)}</script>
</body>
</html>`
  const w = window.open('', '_blank')
  if (!w) return false
  w.document.open()
  w.document.write(html)
  w.document.close()
  return true
}

export type AgentReportFile = {
  agentName: string
  label: string
  fileSlug: string
  markdown: string
}

export function collectAgentReportFiles(
  getContent: (agentName: string) => string,
): AgentReportFile[] {
  return AGENT_META.map((m) => {
    const markdown = sanitizeReportMarkdown(getContent(m.name)).trim()
    return { agentName: m.name, label: m.label, fileSlug: m.fileSlug, markdown }
  }).filter((f) => f.markdown.length > 0)
}

export function buildCombinedMarkdown(
  files: AgentReportFile[],
  meta: { symbol: string; stockName?: string },
): string {
  const title = `# ${meta.stockName || meta.symbol} · AI 多 Agent 分析报告`
  const parts = [
    title,
    '',
    `> 标的：${meta.symbol}${meta.stockName ? `（${meta.stockName}）` : ''}`,
    `> 生成时间：${new Date().toLocaleString('zh-CN')}`,
    '',
    '---',
    '',
  ]
  for (const f of files) {
    parts.push(`## ${f.label}`, '', f.markdown, '', '---', '')
  }
  parts.push(
    '> 免责声明：以上内容由模型基于公开数据与规则自动生成，仅供研究参考，不构成投资建议。',
    '',
  )
  return parts.join('\n')
}

/** CRC-32/ISO-HDLC — required by macOS Archive Utility (error 94 if wrong/zero). */
const CRC32_TABLE = (() => {
  const table = new Uint32Array(256)
  for (let i = 0; i < 256; i++) {
    let c = i
    for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1
    table[i] = c >>> 0
  }
  return table
})()

function crc32(data: Uint8Array): number {
  let c = 0xffffffff
  for (let i = 0; i < data.length; i++) {
    c = CRC32_TABLE[(c ^ data[i]) & 0xff] ^ (c >>> 8)
  }
  return (c ^ 0xffffffff) >>> 0
}

/** Store-only ZIP (no compression) — enough for .md bundles without new deps. */
function buildStoreZip(entries: Array<{ name: string; data: Uint8Array }>): Blob {
  const enc = new TextEncoder()
  const parts: Uint8Array[] = []
  const central: Uint8Array[] = []
  let offset = 0

  const u16 = (n: number) => {
    const b = new Uint8Array(2)
    new DataView(b.buffer).setUint16(0, n, true)
    return b
  }
  const u32 = (n: number) => {
    const b = new Uint8Array(4)
    new DataView(b.buffer).setUint32(0, n, true)
    return b
  }
  const concat = (chunks: Uint8Array[]) => {
    const len = chunks.reduce((s, c) => s + c.length, 0)
    const out = new Uint8Array(len)
    let o = 0
    for (const c of chunks) { out.set(c, o); o += c.length }
    return out
  }

  for (const entry of entries) {
    const nameBytes = enc.encode(entry.name)
    const crc = crc32(entry.data)
    // Bit 11 = UTF-8 filenames (needed for Chinese agent labels on macOS)
    const flags = 0x0800
    const local = concat([
      u32(0x04034b50),
      u16(20), u16(flags), u16(0),
      u16(0), u16(0),
      u32(crc),
      u32(entry.data.length),
      u32(entry.data.length),
      u16(nameBytes.length),
      u16(0),
      nameBytes,
      entry.data,
    ])
    parts.push(local)

    const centralHeader = concat([
      u32(0x02014b50),
      u16(20), u16(20), u16(flags), u16(0),
      u16(0), u16(0),
      u32(crc),
      u32(entry.data.length),
      u32(entry.data.length),
      u16(nameBytes.length),
      u16(0), u16(0), u16(0), u16(0),
      u32(0),
      u32(offset),
      nameBytes,
    ])
    central.push(centralHeader)
    offset += local.length
  }

  const centralDir = concat(central)
  const end = concat([
    u32(0x06054b50),
    u16(0), u16(0),
    u16(entries.length), u16(entries.length),
    u32(centralDir.length),
    u32(offset),
    u16(0),
  ])

  return new Blob([concat([...parts, centralDir, end])], { type: 'application/zip' })
}

export function downloadAgentReportsZip(
  files: AgentReportFile[],
  meta: { symbol: string; stockName?: string },
) {
  const enc = new TextEncoder()
  const sym = meta.symbol.replace(/\./g, '-')
  const entries = files.map((f) => ({
    name: `${f.fileSlug}-${f.label}.md`,
    data: enc.encode(`# ${f.label}\n\n${f.markdown}\n`),
  }))
  // Also include combined
  const combined = buildCombinedMarkdown(files, meta)
  entries.unshift({ name: `_all-${sym}.md`, data: enc.encode(combined) })
  const blob = buildStoreZip(entries)
  triggerDownload(blob, `ai-analysis-${sym}.zip`)
}

export function downloadAllAsCombinedMarkdown(
  files: AgentReportFile[],
  meta: { symbol: string; stockName?: string },
) {
  const md = buildCombinedMarkdown(files, meta)
  const sym = meta.symbol.replace(/\./g, '-')
  downloadMarkdownFile(md, `ai-analysis-${sym}.md`)
}
