import React from 'react'

/**
 * 根级错误边界 — 渲染树抛错时不能整页白屏。
 *
 * 这里只做兜底展示; 具体错误已由 componentDidCatch 打到控制台, 用户可复制
 * 报错和 request_id (如果有) 给运维。异步 Promise 错误由 main.tsx 的
 * unhandledrejection 监听记录。
 */
type State = { error: Error | null }

export class RootErrorBoundary extends React.Component<React.PropsWithChildren, State> {
  state: State = { error: null }

  static getDerivedStateFromError(error: Error): State {
    return { error }
  }

  componentDidCatch(error: Error, info: React.ErrorInfo) {
    console.error('[TSP] React render error', error, info.componentStack)
  }

  render() {
    if (!this.state.error) return this.props.children
    return (
      <div className="min-h-screen grid place-items-center bg-page p-6">
        <div className="max-w-lg rounded-card border border-border bg-surface p-6">
          <div className="text-sm font-medium text-foreground">页面加载出错</div>
          <p className="mt-2 text-xs leading-relaxed text-muted">
            界面遇到未处理的异常, 已阻止整页白屏。可以刷新重试; 若反复出现,
            请把下方错误信息发给运维。
          </p>
          <pre className="mt-3 max-h-40 overflow-auto rounded-btn bg-elevated p-3 text-[11px] text-secondary whitespace-pre-wrap">
            {this.state.error.message || String(this.state.error)}
          </pre>
          <button
            onClick={() => window.location.reload()}
            className="mt-4 inline-flex items-center rounded-btn bg-accent px-3 py-1.5 text-xs font-medium text-white"
          >
            重新加载
          </button>
        </div>
      </div>
    )
  }
}
