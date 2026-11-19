import { AGENT_META } from './agentMeta'

function joinArgs(value: unknown): string {
  if (Array.isArray(value)) return value.map(String).filter(Boolean).join('\n\n')
  if (typeof value === 'string') return value
  return ''
}

/** Matches Go `buildResultMap` join when multiple agents share one section key. */
const SECTION_JOIN = '\n\n---\n\n'

/**
 * Writer order for shared sections (same as backend agentOrder).
 * Portfolio Manager appears twice: 风险法官 + 风险经理 both map to it.
 */
const SHARED_SECTION_WRITERS: Record<string, string[]> = {
  investment_plan: ['Bull Researcher', 'Bear Researcher', 'Research Manager'],
  final_trade_decision: [
    'Aggressive Analyst',
    'Neutral Analyst',
    'Conservative Analyst',
    'Portfolio Manager',
    'Portfolio Manager',
  ],
}

/** Fallback split from debate state objects (astock / some Go payloads). */
function fromDebateState(agentName: string, report: Record<string, unknown> | null | undefined): string {
  if (!report) return ''
  const investment = report.investment_debate_state as Record<string, unknown> | undefined
  if (investment) {
    if (agentName === 'Bull Researcher') {
      return String(investment.bull_history || '') || joinArgs(investment.bull_arguments)
    }
    if (agentName === 'Bear Researcher') {
      return String(investment.bear_history || '') || joinArgs(investment.bear_arguments)
    }
    if (agentName === 'Research Manager') {
      return String(investment.judge_decision || '') || joinArgs(investment.common_ground)
    }
  }
  const risk = report.risk_debate_state as Record<string, unknown> | undefined
  if (risk) {
    if (agentName === 'Aggressive Analyst') {
      return String(risk.aggressive_history || risk.aggressive_view || '')
    }
    if (agentName === 'Conservative Analyst') {
      return String(risk.conservative_history || risk.conservative_view || '')
    }
    if (agentName === 'Neutral Analyst') {
      return String(risk.neutral_history || risk.neutral_view || '')
    }
    if (agentName === 'Portfolio Manager') {
      return String(risk.judge_decision || risk.revision_feedback || '')
    }
  }
  return ''
}

/**
 * History / persisted reports store multi-agent sections as joined blobs.
 * Split back into per-agent text so node clicks work without live SSE agentReports.
 */
function fromSharedSectionSplit(
  agentName: string,
  section: string,
  report: Record<string, unknown> | null | undefined,
): string {
  if (!report) return ''
  const writers = SHARED_SECTION_WRITERS[section]
  if (!writers?.includes(agentName)) return ''

  const stored = report[section]
  if (typeof stored !== 'string' || !stored.trim()) return ''

  const parts = stored.split(SECTION_JOIN).map((p) => p.trim()).filter(Boolean)
  if (parts.length <= 1) return ''

  const chunks: string[] = writers.map(() => '')
  if (parts.length === writers.length) {
    parts.forEach((p, i) => {
      chunks[i] = p
    })
  } else if (parts.length < writers.length) {
    // Some agents skipped — left-align what we have
    parts.forEach((p, i) => {
      chunks[i] = p
    })
  } else {
    writers.forEach((_, i) => {
      if (i < writers.length - 1) chunks[i] = parts[i] || ''
      else chunks[i] = parts.slice(i).join(SECTION_JOIN)
    })
  }

  const indices = writers
    .map((name, i) => (name === agentName ? i : -1))
    .filter((i) => i >= 0)
  return indices
    .map((i) => chunks[i])
    .filter((t) => t.trim())
    .join('\n\n')
}

function asAgentReportsMap(raw: unknown): Record<string, string> {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return {}
  const out: Record<string, string> = {}
  for (const [k, v] of Object.entries(raw as Record<string, unknown>)) {
    if (typeof v === 'string' && v.trim()) out[k] = v
  }
  return out
}

/**
 * Build per-agent text map for history hydrate (and downloads).
 * Prefers explicit `agent_reports` on result_data when present.
 */
export function agentReportsFromAnalysisReport(
  report: Record<string, unknown> | null | undefined,
): Record<string, string> {
  const out = asAgentReportsMap(report?.agent_reports)
  for (const meta of AGENT_META) {
    if (out[meta.name]?.trim()) continue
    const text = resolveAgentReportText(meta.name, {
      agentReports: out,
      streamingSections: {},
      report,
      section: meta.section,
    })
    if (text.trim()) out[meta.name] = text
  }
  return out
}

/** Resolve display text for one agent (per-agent first, then sole section fallback). */
export function resolveAgentReportText(
  agentName: string,
  opts: {
    agentReports: Record<string, string>
    streamingSections: Record<string, { displayed?: string; buffer?: string }>
    report: Record<string, unknown> | null | undefined
    section?: string
  },
): string {
  const fromAgent = opts.agentReports[agentName]
  if (typeof fromAgent === 'string' && fromAgent.trim()) return fromAgent

  const agentStream = opts.streamingSections[`agent:${agentName}`]
  if (agentStream?.displayed || agentStream?.buffer) {
    return agentStream.displayed || agentStream.buffer || ''
  }

  const fromDebate = fromDebateState(agentName, opts.report)
  if (fromDebate.trim()) return fromDebate

  const section = opts.section ?? AGENT_META.find((m) => m.name === agentName)?.section
  if (section) {
    const owners = AGENT_META.filter((m) => m.section === section)
    if (owners.length === 1 && owners[0].name === agentName) {
      const stream = opts.streamingSections[section]
      if (stream?.displayed || stream?.buffer) return stream.displayed || stream.buffer || ''
      const stored = opts.report?.[section]
      if (typeof stored === 'string') return stored
    }

    const fromSplit = fromSharedSectionSplit(agentName, section, opts.report)
    if (fromSplit.trim()) return fromSplit

    // Unsplit shared blob: final owners still get the full section text
    if (agentName === 'Research Manager' && section === 'investment_plan') {
      const stored = opts.report?.investment_plan
      if (typeof stored === 'string' && stored.trim()
        && !opts.agentReports['Bull Researcher']
        && !opts.agentReports['Bear Researcher']) {
        return stored
      }
    }
    if (agentName === 'Portfolio Manager' && section === 'final_trade_decision') {
      const stored = opts.report?.final_trade_decision
      if (typeof stored === 'string' && stored.trim()) return stored
    }
  }

  return ''
}
