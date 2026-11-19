import type { FC } from 'react'
import {
  TrendingUp, MessageCircle, Newspaper, Calculator,
  BarChart2, DollarSign, Swords, ArrowBigUp, ArrowBigDown,
  Brain, Briefcase, Flame, Scale, Shield, CheckCircle2,
} from 'lucide-react'

export interface AgentMeta {
  name: string
  label: string
  goal: string
  /** Canonical report section key from backend (may be shared across agents). */
  section?: string
  debate?: 'research' | 'risk'
  Icon: FC<{ className?: string }>
  badgeBg: string
  badgeText: string
  fileSlug: string
}

/** UI metadata for each TradingAgents node. */
export const AGENT_META: AgentMeta[] = [
  { name: 'Market Analyst', label: '技术面', goal: '技术指标与价格形态分析', section: 'market_report', Icon: TrendingUp, badgeBg: 'bg-blue-500/20', badgeText: 'text-blue-400', fileSlug: 'market' },
  { name: 'Social Analyst', label: '舆情', goal: '舆论情绪与社交媒体分析', section: 'sentiment_report', Icon: MessageCircle, badgeBg: 'bg-fuchsia-500/20', badgeText: 'text-fuchsia-400', fileSlug: 'sentiment' },
  { name: 'News Analyst', label: '新闻', goal: '政策资讯与行业动态分析', section: 'news_report', Icon: Newspaper, badgeBg: 'bg-cyan-500/20', badgeText: 'text-cyan-400', fileSlug: 'news' },
  { name: 'Fundamentals Analyst', label: '基本面', goal: '财务报表与估值分析', section: 'fundamentals_report', Icon: Calculator, badgeBg: 'bg-emerald-500/20', badgeText: 'text-emerald-400', fileSlug: 'fundamentals' },
  { name: 'Macro Analyst', label: '宏观', goal: '板块轮动与政策驱动分析', section: 'macro_report', Icon: BarChart2, badgeBg: 'bg-violet-500/20', badgeText: 'text-violet-400', fileSlug: 'macro' },
  { name: 'Smart Money Analyst', label: '主力资金', goal: '机构资金行为与龙虎榜', section: 'smart_money_report', Icon: DollarSign, badgeBg: 'bg-amber-500/20', badgeText: 'text-amber-400', fileSlug: 'smart-money' },
  { name: 'Game Theory Manager', label: '博弈裁判', goal: '主力与散户预期差裁判', section: 'game_theory_report', Icon: Swords, badgeBg: 'bg-rose-500/20', badgeText: 'text-rose-400', fileSlug: 'game-theory' },
  { name: 'Bull Researcher', label: '多头', goal: '评估投资价值与上行潜力', section: 'investment_plan', debate: 'research', Icon: ArrowBigUp, badgeBg: 'bg-emerald-500/20', badgeText: 'text-emerald-400', fileSlug: 'bull' },
  { name: 'Bear Researcher', label: '空头', goal: '评估下行风险与潜在危机', section: 'investment_plan', debate: 'research', Icon: ArrowBigDown, badgeBg: 'bg-rose-500/20', badgeText: 'text-rose-400', fileSlug: 'bear' },
  { name: 'Research Manager', label: '研究总监', goal: '综合多空论据形成投资计划', section: 'investment_plan', debate: 'research', Icon: Brain, badgeBg: 'bg-indigo-500/20', badgeText: 'text-indigo-400', fileSlug: 'research-manager' },
  { name: 'Trader', label: '交易员', goal: '将研究结论转化为可执行指令', section: 'trader_investment_plan', Icon: Briefcase, badgeBg: 'bg-orange-500/20', badgeText: 'text-orange-400', fileSlug: 'trader' },
  { name: 'Aggressive Analyst', label: '激进', goal: '高风险高收益策略约束', section: 'final_trade_decision', debate: 'risk', Icon: Flame, badgeBg: 'bg-red-500/20', badgeText: 'text-red-400', fileSlug: 'risk-aggressive' },
  { name: 'Neutral Analyst', label: '中性', goal: '均衡风险收益策略约束', section: 'final_trade_decision', debate: 'risk', Icon: Scale, badgeBg: 'bg-slate-500/20', badgeText: 'text-slate-400', fileSlug: 'risk-neutral' },
  { name: 'Conservative Analyst', label: '稳健', goal: '低风险保守策略约束', section: 'final_trade_decision', debate: 'risk', Icon: Shield, badgeBg: 'bg-amber-500/20', badgeText: 'text-amber-400', fileSlug: 'risk-conservative' },
  { name: 'Portfolio Manager', label: '组合经理', goal: '综合裁决形成最终决策', section: 'final_trade_decision', debate: 'risk', Icon: CheckCircle2, badgeBg: 'bg-teal-500/20', badgeText: 'text-teal-400', fileSlug: 'portfolio-manager' },
]

/** Sections owned by exactly one agent — safe fallback when SSE omits agent name. */
export const SECTION_SOLE_AGENT: Record<string, string> = (() => {
  const owners = new Map<string, string[]>()
  for (const m of AGENT_META) {
    if (!m.section) continue
    const list = owners.get(m.section) || []
    list.push(m.name)
    owners.set(m.section, list)
  }
  const sole: Record<string, string> = {}
  for (const [section, list] of owners) {
    if (list.length === 1) sole[section] = list[0]
  }
  return sole
})()

export function getAgentMeta(name: string): AgentMeta | undefined {
  return AGENT_META.find((m) => m.name === name)
}
