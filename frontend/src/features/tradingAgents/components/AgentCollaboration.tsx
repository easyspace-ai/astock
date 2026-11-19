import { useMemo, useCallback, memo } from 'react'
import {
    ReactFlow,
    Handle,
    Position,
    MarkerType,
    Controls,
    Background,
    BackgroundVariant,
    ReactFlowProvider,
    type Node,
    type Edge,
    type NodeProps,
    type NodeTypes,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import { useAnalysisStore } from '../analysisStore'
import { AGENT_META, type AgentMeta } from '../agentMeta'
import type { AgentStatus } from '../types'
import { CheckCircle2, Loader2 } from 'lucide-react'
import { extractVerdict, type Verdict } from '../reportText'
import { resolveAgentReportText } from '../resolveAgentReport'

const STATUS_LABEL: Record<AgentStatus, string> = {
    pending: '待命', in_progress: '分析中', completed: '完成', skipped: '跳过', error: '异常',
}

const VERDICT_COLORS: Record<string, string> = {
    '看多': 'bg-emerald-500/20 text-emerald-300',
    '偏多': 'bg-teal-500/20 text-teal-300',
    '中性': 'bg-elevated text-muted',
    '偏空': 'bg-orange-500/20 text-orange-300',
    '看空': 'bg-red-500/20 text-red-300',
    '谨慎': 'bg-amber-500/20 text-amber-300',
    _default: 'bg-elevated text-muted',
}

const SOURCE_ANALYSTS = [
    'Market Analyst', 'Social Analyst', 'News Analyst', 'Fundamentals Analyst',
    'Macro Analyst', 'Smart Money Analyst', 'Game Theory Manager',
]

const NODE_POSITIONS: Record<string, { x: number; y: number }> = {
    'Market Analyst':       { x: 0, y: 0 },
    'Social Analyst':       { x: 0, y: 105 },
    'News Analyst':         { x: 0, y: 210 },
    'Fundamentals Analyst': { x: 0, y: 315 },
    'Macro Analyst':        { x: 0, y: 420 },
    'Smart Money Analyst':  { x: 0, y: 525 },
    'Game Theory Manager':  { x: 0, y: 630 },
    'Bull Researcher':      { x: 470, y: 80 },
    'Research Manager':     { x: 630, y: 240 },
    'Bear Researcher':      { x: 470, y: 400 },
    'Trader':               { x: 890, y: 240 },
    'Aggressive Analyst':   { x: 1180, y: 80 },
    'Neutral Analyst':      { x: 1180, y: 240 },
    'Conservative Analyst': { x: 1180, y: 400 },
    'Portfolio Manager':    { x: 1470, y: 240 },
}

const BOTTOM_HANDLE_NODES = new Set(['Bull Researcher'])
const TOP_HANDLE_NODES = new Set(['Bear Researcher'])

interface EdgeDef {
    source: string
    target: string
    sourceHandle?: string
    targetHandle?: string
    label?: string
    bidirectional?: boolean
    thin?: boolean
}

const EDGE_DEFS: EdgeDef[] = [
    ...SOURCE_ANALYSTS.map(s => ({ source: s, target: 'Bull Researcher', thin: true } as EdgeDef)),
    ...SOURCE_ANALYSTS.map(s => ({ source: s, target: 'Bear Researcher', thin: true } as EdgeDef)),
    { source: 'Bull Researcher', target: 'Bear Researcher', sourceHandle: 'bottom', targetHandle: 'top', label: '辩论', bidirectional: true },
    { source: 'Bull Researcher', target: 'Research Manager' },
    { source: 'Bear Researcher', target: 'Research Manager' },
    { source: 'Research Manager', target: 'Trader', label: '投资计划' },
    { source: 'Trader', target: 'Aggressive Analyst' },
    { source: 'Trader', target: 'Neutral Analyst', label: '交易方案' },
    { source: 'Trader', target: 'Conservative Analyst' },
    { source: 'Aggressive Analyst', target: 'Portfolio Manager' },
    { source: 'Neutral Analyst', target: 'Portfolio Manager' },
    { source: 'Conservative Analyst', target: 'Portfolio Manager' },
]

const GROUP_LABELS = [
    { id: 'group-sources', label: '技术分析', position: { x: -16, y: -30 }, width: 248, height: 760 },
    { id: 'group-research', label: '研究团队', position: { x: 454, y: 44 }, width: 410, height: 450 },
    { id: 'group-risk', label: '风控团队', position: { x: 1164, y: 44 }, width: 248, height: 450 },
]

interface AgentNodeData {
    meta: AgentMeta
    status: AgentStatus
    verdict: Verdict | null
    isParticipating: boolean
    selected: boolean
    [key: string]: unknown
}

type AgentFlowNode = Node<AgentNodeData, 'agent'>
type GroupLabelNodeData = {
    label: string
    width: number
    height: number
    [key: string]: unknown
}
type GroupLabelFlowNode = Node<GroupLabelNodeData, 'groupLabel'>

function AgentNodeComponent({ data }: NodeProps<AgentFlowNode>) {
    const { meta, status, verdict, isParticipating, selected } = data
    const active = status === 'in_progress'
    const done = status === 'completed'
    const skipped = status === 'skipped'
    const { Icon } = meta

    return (
        <div
            className={[
                'px-4 py-3 rounded-xl border transition-all duration-300 min-w-[210px] max-w-[218px]',
                !isParticipating ? 'opacity-30 grayscale' : '',
                selected
                    ? 'border-sky-400 bg-sky-500/10 shadow-lg ring-2 ring-sky-400/30'
                    : active
                    ? 'border-sky-400/60 bg-elevated shadow-[0_0_14px_rgba(56,189,248,0.2)]'
                    : done
                    ? 'border-emerald-500/50 bg-elevated/80 shadow-sm'
                    : skipped
                    ? 'border-border/40 bg-surface/50 opacity-40'
                    : 'border-border/60 bg-elevated shadow-sm',
            ].join(' ')}
        >
            <Handle type="target" position={Position.Left} id="left"
                className="!w-2 !h-2 !bg-muted !border-0 !min-w-0 !min-h-0" />
            <Handle type="source" position={Position.Right} id="right"
                className="!w-2 !h-2 !bg-muted !border-0 !min-w-0 !min-h-0" />

            {BOTTOM_HANDLE_NODES.has(meta.name) && (
                <Handle type="source" position={Position.Bottom} id="bottom"
                    className="!w-2 !h-2 !bg-muted !border-0 !min-w-0 !min-h-0" />
            )}
            {TOP_HANDLE_NODES.has(meta.name) && (
                <Handle type="target" position={Position.Top} id="top"
                    className="!w-2 !h-2 !bg-muted !border-0 !min-w-0 !min-h-0" />
            )}

            <div className="flex items-center gap-2.5">
                <div className={`shrink-0 w-9 h-9 rounded-lg flex items-center justify-center ${meta.badgeBg}`}>
                    {active
                        ? <Loader2 className={`w-[18px] h-[18px] animate-spin ${meta.badgeText}`} />
                        : <Icon className={`w-[18px] h-[18px] ${meta.badgeText}`} />}
                </div>
                <span className={`text-[15px] font-bold flex-1 leading-tight ${active ? 'text-sky-400' : 'text-foreground'}`}>
                    {meta.label}
                </span>
                <span className={[
                    'shrink-0 text-[11px] px-2 py-0.5 rounded-full font-bold',
                    active ? 'bg-sky-500 text-white animate-pulse'
                        : done ? 'bg-emerald-500 text-white'
                        : 'bg-elevated text-muted',
                ].join(' ')}>
                    {STATUS_LABEL[status]}
                </span>
            </div>

            {active && (
                <div className="flex items-center gap-2 mt-2">
                    <span className="flex gap-1">
                        <span className="w-1.5 h-1.5 bg-sky-500 rounded-full animate-bounce [animation-delay:-0.3s]" />
                        <span className="w-1.5 h-1.5 bg-sky-500 rounded-full animate-bounce [animation-delay:-0.15s]" />
                        <span className="w-1.5 h-1.5 bg-sky-500 rounded-full animate-bounce" />
                    </span>
                    <span className="text-[12px] text-sky-400 font-bold">研判中...</span>
                </div>
            )}

            {done && verdict && (
                <div className="flex items-start gap-2 mt-2 min-w-0">
                    <span className={`shrink-0 mt-0.5 text-[11px] font-black px-2 py-0.5 rounded-full leading-none ${VERDICT_COLORS[verdict.direction] ?? VERDICT_COLORS._default}`}>
                        {verdict.direction}
                    </span>
                    <span className="text-[12px] text-muted leading-snug line-clamp-2">
                        {verdict.reason}
                    </span>
                </div>
            )}

            {done && !verdict && (
                <div className="flex items-center gap-1.5 mt-2">
                    <CheckCircle2 className="w-4 h-4 text-emerald-500 shrink-0" />
                    <span className="text-[12px] text-emerald-400 font-bold">完成</span>
                </div>
            )}
        </div>
    )
}

function GroupLabelNode({ data }: NodeProps<GroupLabelFlowNode>) {
    return (
        <div
            className="rounded-2xl border-2 border-dashed border-border/50 pointer-events-none"
            style={{ width: data.width, height: data.height }}
        >
            <div className="absolute -top-3 left-4 px-2 bg-surface">
                <span className="text-[10px] font-bold text-muted uppercase tracking-widest">
                    {data.label}
                </span>
            </div>
        </div>
    )
}

const nodeTypes: NodeTypes = {
    agent: memo(AgentNodeComponent),
    groupLabel: memo(GroupLabelNode),
}

interface AgentCollaborationProps {
    onSelectAgent: (agentName?: string) => void
    selectedAgent?: string
}

export default function AgentCollaboration({ onSelectAgent, selectedAgent }: AgentCollaborationProps) {
    const { agents, isAnalyzing, streamingSections, report, agentReports, currentHorizon } = useAnalysisStore()

    const cards = useMemo(() => AGENT_META.map((meta) => {
        const agent = agents.find(a => a.name === meta.name)
        const src = resolveAgentReportText(meta.name, {
            agentReports,
            streamingSections,
            report: report as Record<string, unknown> | null,
            section: meta.section,
        })
        const hasAgentContent = src.trim().length > 0
        const isParticipating = isAnalyzing ? (agent ? agent.status !== 'skipped' : false) : true

        const baseStatus = (agent?.status ?? 'pending') as AgentStatus

        // Root-cause mitigation for premature completed:
        // backend marks completed after each MessageOutput (incl. tool rounds without report text).
        // While the job is running, only show 「完成」 once this agent has written report content.
        let status: AgentStatus = baseStatus
        if (isAnalyzing && baseStatus === 'completed' && !hasAgentContent) {
            status = 'in_progress'
        } else if (!isAnalyzing && hasAgentContent && (baseStatus === 'pending' || baseStatus === 'in_progress')) {
            status = 'completed'
        }

        return {
            meta,
            status,
            hasAgentContent,
            verdict: extractVerdict(src),
            isParticipating,
        }
    }), [agents, report, streamingSections, agentReports, isAnalyzing])

    const cardMap = useMemo(() => new Map(cards.map(c => [c.meta.name, c])), [cards])
    const doneN = cards.filter(c => c.status === 'completed').length
    const participatingCount = cards.filter(c => c.status !== 'skipped').length

    const nodes: (AgentFlowNode | GroupLabelFlowNode)[] = useMemo(() => {
        const agentNodes: AgentFlowNode[] = cards.map(card => ({
            id: card.meta.name,
            type: 'agent',
            position: NODE_POSITIONS[card.meta.name] ?? { x: 0, y: 0 },
            data: {
                meta: card.meta,
                status: card.status,
                verdict: card.verdict,
                isParticipating: card.isParticipating,
                selected: card.meta.name === selectedAgent,
            } satisfies AgentNodeData,
        }))

        const labelNodes: GroupLabelFlowNode[] = GROUP_LABELS.map(g => ({
            id: g.id,
            type: 'groupLabel',
            position: g.position,
            data: { label: g.label, width: g.width, height: g.height },
            selectable: false,
            draggable: false,
            zIndex: -1,
        }))

        return [...labelNodes, ...agentNodes]
    }, [cards, selectedAgent])

    const edges: Edge[] = useMemo(() => {
        return EDGE_DEFS.map((def, i) => {
            const sourceCard = cardMap.get(def.source)
            const targetCard = cardMap.get(def.target)
            const sourceDone = sourceCard?.status === 'completed'
            const targetActive = targetCard?.status === 'in_progress'
            const color = sourceDone ? '#10b981' : targetActive ? '#38bdf8' : '#475569'

            return {
                id: `e-${i}`,
                source: def.source,
                target: def.target,
                sourceHandle: def.sourceHandle ?? 'right',
                targetHandle: def.targetHandle ?? 'left',
                type: 'default',
                animated: targetActive,
                label: def.label,
                labelStyle: { fontSize: 10, fontWeight: 600, fill: '#94a3b8' },
                labelBgStyle: { fill: '#0f172a', fillOpacity: 0.9 },
                labelBgPadding: [4, 2] as [number, number],
                labelBgBorderRadius: 4,
                style: {
                    stroke: color,
                    strokeWidth: def.thin ? 1 : 1.5,
                    opacity: def.thin ? 0.6 : 1,
                },
                markerEnd: {
                    type: MarkerType.ArrowClosed,
                    color,
                    width: 16,
                    height: 16,
                },
                ...(def.bidirectional && {
                    markerStart: {
                        type: MarkerType.ArrowClosed,
                        color,
                        width: 16,
                        height: 16,
                    },
                }),
            } satisfies Edge
        })
    }, [cardMap])

    const handleNodeClick = useCallback((_: React.MouseEvent, node: Node) => {
        const card = cardMap.get(node.id)
        if (!card) return
        if (card.status !== 'completed' && card.status !== 'in_progress') return
        if (!card.hasAgentContent && card.status !== 'in_progress') return
        onSelectAgent(card.meta.name === selectedAgent ? undefined : card.meta.name)
    }, [cardMap, selectedAgent, onSelectAgent])

    return (
        <section className="relative overflow-hidden rounded-card border border-border/60 bg-surface/40">
            <div className="flex flex-wrap items-center justify-between gap-2 mb-2 relative z-10 border-b border-border/40 px-4 pt-4 pb-3">
                <div className="flex items-center gap-3">
                    <div className={`w-3 h-3 rounded-full ${isAnalyzing ? 'bg-sky-500 animate-pulse shadow-[0_0_12px_#38bdf8]' : 'bg-muted'}`} />
                    <h3 className="text-sm font-semibold text-foreground tracking-wide">
                        TradingAgents 协同工作流
                    </h3>
                </div>
                <div className="flex flex-wrap items-center gap-3">
                    <span className="text-[11px] text-muted">
                        拖拽平移 · 滚轮缩放 · 点击节点查看报告
                    </span>
                    {isAnalyzing && (
                        <div className="flex items-center gap-4">
                            {currentHorizon && (
                                <span className={`px-3 py-1 rounded-full text-[11px] font-black tracking-widest border animate-in fade-in duration-300 ${
                                    currentHorizon === 'short'
                                        ? 'bg-sky-500/10 text-sky-400 border-sky-400/30'
                                        : 'bg-violet-500/10 text-violet-300 border-violet-400/30'
                                }`}>
                                    {currentHorizon === 'short' ? '⚡ 短线视角' : '🔭 中线视角'}
                                </span>
                            )}
                            <div className="text-right">
                                <div className="text-2xl font-black text-sky-400 tabular-nums">
                                    {participatingCount > 0 ? Math.round((doneN / participatingCount) * 100) : 0}%
                                </div>
                                <p className="text-[10px] font-medium text-muted uppercase tracking-tighter">分析总进度</p>
                            </div>
                        </div>
                    )}
                </div>
            </div>

            <div className="mx-4 mb-4 h-[min(72vh,720px)] min-h-[480px] w-[calc(100%-2rem)] rounded-xl border border-border/40 bg-base/60">
                <ReactFlowProvider>
                    <ReactFlow
                        nodes={nodes}
                        edges={edges}
                        nodeTypes={nodeTypes}
                        onNodeClick={handleNodeClick}
                        fitView
                        fitViewOptions={{ padding: 0.14, minZoom: 0.35, maxZoom: 1.25 }}
                        minZoom={0.25}
                        maxZoom={2}
                        nodesDraggable={false}
                        nodesConnectable={false}
                        nodesFocusable={false}
                        edgesFocusable={false}
                        panOnDrag
                        panOnScroll={false}
                        zoomOnScroll
                        zoomOnPinch
                        zoomOnDoubleClick={false}
                        preventScrolling
                        translateExtent={[[-120, -80], [1850, 820]]}
                        proOptions={{ hideAttribution: true }}
                        className="rounded-xl"
                    >
                        <Background variant={BackgroundVariant.Dots} gap={18} size={1} color="#334155" />
                        <Controls
                            showInteractive={false}
                            position="bottom-right"
                            className="!rounded-lg !border-border !bg-surface/95 !shadow-md"
                        />
                    </ReactFlow>
                </ReactFlowProvider>
            </div>
        </section>
    )
}
