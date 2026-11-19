/**
 * 关于面板 — 产品定位与版本信息。
 * 内部终端: 不展示第三方开源项目的署名、打赏与上游 Release 入口。
 */
import { Info, Database, Radio, ShieldCheck } from 'lucide-react'
import { useVersion } from '@/lib/useSharedQueries'

export function SettingsAboutPanel() {
  const { data: versionData } = useVersion()
  const version = (versionData?.version ?? '').replace(/^v/, '') || '—'

  return (
    <div className="flex flex-col gap-6 pb-8">
      <section className="rounded-card border border-border bg-surface p-5">
        <div className="mb-3 flex items-center gap-2">
          <Info className="h-4 w-4 text-accent" />
          <h3 className="text-sm font-medium text-foreground">产品信息</h3>
          <span className="ml-auto rounded-full border border-border bg-elevated px-2 py-0.5 font-mono text-[10px] text-muted">
            v{version}
          </span>
        </div>
        <p className="text-[12px] leading-relaxed text-secondary">
          财富引擎是面向 A 股量化研究与交易的内部工作台：盘后数据管道、因子与回测、自选实时监控、
          策略筛选与扩展页, 全部数据落在本地, 行情与数据源在「数据源」中配置。
        </p>
      </section>

      <section className="rounded-card border border-border bg-surface p-5">
        <div className="mb-3 flex items-center gap-2">
          <Database className="h-4 w-4 text-accent" />
          <h3 className="text-sm font-medium text-foreground">数据与口径</h3>
        </div>
        <div className="grid grid-cols-1 gap-2 text-[11px] leading-relaxed text-secondary sm:grid-cols-2">
          <div className="flex items-start gap-2 rounded-btn border border-border bg-elevated/40 px-3 py-2">
            <Database className="mt-0.5 h-3.5 w-3.5 shrink-0 text-accent" />
            <span>盘后数据来自本地 enriched 表; 看板与选股默认取最新有数据日, 不是盘中实时。</span>
          </div>
          <div className="flex items-start gap-2 rounded-btn border border-border bg-elevated/40 px-3 py-2">
            <Radio className="mt-0.5 h-3.5 w-3.5 shrink-0 text-accent" />
            <span>实时行情取决于数据源权限; 未开通时状态栏显示「实时行情 未开启」, 属正常。</span>
          </div>
          <div className="flex items-start gap-2 rounded-btn border border-border bg-elevated/40 px-3 py-2">
            <ShieldCheck className="mt-0.5 h-3.5 w-3.5 shrink-0 text-accent" />
            <span>账号、偏好与自选均保存在本机; 偏好改动会同步到后端偏好文件, 换机后自动恢复。</span>
          </div>
        </div>
      </section>
    </div>
  )
}
