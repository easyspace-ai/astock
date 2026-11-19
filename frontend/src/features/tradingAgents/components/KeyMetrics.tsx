import { BarChart3 } from 'lucide-react'
import type { KeyMetric } from '../types'

const STATUS_COLOR = {
  good: 'text-bull',
  neutral: 'text-secondary',
  bad: 'text-bear',
}

export default function KeyMetrics({ items }: { items?: KeyMetric[] }) {
  const metrics: KeyMetric[] = items ?? []

  return (
    <div className="rounded-card border border-border/60 bg-surface/40 p-4">
      <div className="flex items-center gap-2 mb-4">
        <div className="p-1.5 rounded-lg bg-sky-500/20">
          <BarChart3 className="w-4 h-4 text-sky-400" />
        </div>
        <h3 className="text-sm font-semibold text-foreground">关键指标速览</h3>
      </div>

      {metrics.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-6 text-center">
          <BarChart3 className="w-8 h-8 text-muted mb-2" />
          <p className="text-xs text-muted">分析完成后展示关键指标</p>
        </div>
      ) : (
        <div className="space-y-2">
          {metrics.map((metric) => (
            <div
              key={metric.name}
              className="flex items-center justify-between py-2 border-b border-border/30 last:border-0"
            >
              <span className="text-sm text-secondary">{metric.name}</span>
              <span className={`text-sm font-medium ${STATUS_COLOR[metric.status]}`}>
                {metric.value}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}