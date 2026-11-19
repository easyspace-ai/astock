import { useEffect, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { Download, FileText, Loader2, X } from 'lucide-react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { getAgentMeta } from '../agentMeta'
import { useAnalysisStore } from '../analysisStore'
import { sanitizeReportMarkdown } from '../reportText'
import { downloadMarkdownFile, downloadPdfViaPrint } from '../downloadReports'
import { resolveAgentReportText } from '../resolveAgentReport'
import { toast } from '@/components/Toast'

type Props = {
  agentName?: string
  symbol: string
  stockName?: string
  onClose: () => void
}

const MD_COMPONENTS = {
  table: ({ children }: { children?: ReactNode }) => (
    <table className="my-4 w-full border-collapse border border-border">{children}</table>
  ),
  thead: ({ children }: { children?: ReactNode }) => (
    <thead className="bg-elevated">{children}</thead>
  ),
  th: ({ children }: { children?: ReactNode }) => (
    <th className="border border-border px-3 py-2 text-left font-semibold">{children}</th>
  ),
  td: ({ children }: { children?: ReactNode }) => (
    <td className="border border-border px-3 py-2">{children}</td>
  ),
}

export function ReportAgentSheet({ agentName, symbol, stockName, onClose }: Props) {
  const { report, streamingSections, agentReports, isAnalyzing } = useAnalysisStore()

  useEffect(() => {
    if (!agentName) return
    const prev = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => {
      document.body.style.overflow = prev
      window.removeEventListener('keydown', onKey)
    }
  }, [agentName, onClose])

  if (!agentName) return null

  const meta = getAgentMeta(agentName)
  if (!meta) return null

  const raw = resolveAgentReportText(agentName, {
    agentReports,
    streamingSections,
    report: report as Record<string, unknown> | null,
    section: meta.section,
  })
  const content = sanitizeReportMarkdown(raw)
  const typing = Boolean(streamingSections[`agent:${agentName}`]?.isTyping
    || (meta.section && streamingSections[meta.section]?.isTyping && !agentReports[agentName]))

  const baseName = `${meta.fileSlug}-${symbol.replace(/\./g, '-')}`

  const handleMd = () => {
    if (!content.trim()) {
      toast('暂无报告内容可下载', 'error')
      return
    }
    const md = `# ${meta.label}\n\n> ${stockName || symbol}（${symbol}）\n\n${content}\n`
    downloadMarkdownFile(md, `${baseName}.md`)
    toast('已下载 Markdown', 'success')
  }

  const handlePdf = () => {
    if (!content.trim()) {
      toast('暂无报告内容可下载', 'error')
      return
    }
    const ok = downloadPdfViaPrint({
      title: `${meta.label} · ${stockName || symbol}`,
      subtitle: symbol,
      markdown: content,
    })
    if (!ok) toast('请允许弹窗后重试（用于打印/另存为 PDF）', 'error')
    else toast('已打开打印窗口，可另存为 PDF', 'success')
  }

  return createPortal(
    <div
      className="fixed inset-0 z-[200] flex items-end justify-center sm:items-center sm:p-4"
      role="dialog"
      aria-modal="true"
      aria-labelledby="agent-report-title"
    >
      <button
        type="button"
        className="absolute inset-0 bg-black/60 backdrop-blur-sm"
        onClick={onClose}
        aria-label="关闭"
      />
      <div className="relative z-[201] flex max-h-[min(85vh,900px)] w-full max-w-3xl flex-col overflow-hidden rounded-t-2xl border border-border bg-surface shadow-2xl sm:rounded-2xl">
        <div className="flex shrink-0 items-center justify-between gap-3 border-b border-border/60 px-4 py-3">
          <div className="flex items-center gap-2 min-w-0">
            <FileText className="h-4 w-4 text-sky-400 shrink-0" />
            <div className="min-w-0">
              <h3 id="agent-report-title" className="text-sm font-semibold text-foreground truncate">
                {meta.label}
              </h3>
              <p className="text-[11px] text-muted truncate">{meta.goal}</p>
            </div>
            {isAnalyzing && typing && (
              <Loader2 className="h-3.5 w-3.5 animate-spin text-sky-400 shrink-0" />
            )}
          </div>
          <div className="flex items-center gap-1.5 shrink-0">
            <button
              type="button"
              onClick={handleMd}
              disabled={!content.trim()}
              className="inline-flex items-center gap-1 rounded-lg border border-border/60 bg-elevated px-2.5 py-1.5 text-[11px] font-medium text-secondary hover:text-foreground disabled:opacity-40"
            >
              <Download className="h-3.5 w-3.5" />
              MD
            </button>
            <button
              type="button"
              onClick={handlePdf}
              disabled={!content.trim()}
              className="inline-flex items-center gap-1 rounded-lg border border-border/60 bg-elevated px-2.5 py-1.5 text-[11px] font-medium text-secondary hover:text-foreground disabled:opacity-40"
            >
              <Download className="h-3.5 w-3.5" />
              PDF
            </button>
            <button
              type="button"
              onClick={onClose}
              className="rounded-lg p-1.5 text-muted hover:bg-elevated hover:text-foreground"
            >
              <X className="h-4 w-4" />
            </button>
          </div>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto p-4 sm:p-6">
          {content ? (
            <div className="prose prose-sm prose-invert max-w-none sm:prose-base">
              <ReactMarkdown remarkPlugins={[remarkGfm]} components={MD_COMPONENTS}>
                {content}
              </ReactMarkdown>
            </div>
          ) : (
            <p className="py-8 text-center text-sm text-muted">
              {isAnalyzing ? '该 Agent 正在撰写报告…' : '该 Agent 暂无报告内容'}
            </p>
          )}
        </div>
      </div>
    </div>,
    document.body,
  )
}
