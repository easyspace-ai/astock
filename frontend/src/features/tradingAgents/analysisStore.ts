import { create } from 'zustand'
import { createJSONStorage, persist } from 'zustand/middleware'
import { SECTION_SOLE_AGENT } from './agentMeta'
import type {
    Agent,
    JobStatus,
    AnalysisReport,
    LogEntry,
    AgentStatusEvent,
    AgentMessageEvent,
    AgentToolCallEvent,
    AgentReportEvent,
    AgentSnapshotEvent,
    ReportChunkEvent,
    AgentMilestoneEvent,
    AgentTokenEvent,
    StreamingSectionState,
    MilestoneMessage,
    RiskItem,
    KeyMetric,
} from './types'

/**
 * Backend emits agent.status=completed after EVERY MessageOutput, including
 * intermediate tool-round messages. Debounce completed so a quick follow-up
 * in_progress cancels the premature "完成" flash.
 */
const COMPLETED_DEBOUNCE_MS = 1400
const pendingCompletedTimers = new Map<string, ReturnType<typeof setTimeout>>()

function clearPendingCompleted(agent?: string) {
    if (agent) {
        const t = pendingCompletedTimers.get(agent)
        if (t) clearTimeout(t)
        pendingCompletedTimers.delete(agent)
        return
    }
    for (const t of pendingCompletedTimers.values()) clearTimeout(t)
    pendingCompletedTimers.clear()
}

function flushPendingCompleted() {
    const agents = [...pendingCompletedTimers.keys()]
    clearPendingCompleted()
    if (!agents.length) return
    const done = new Set(agents)
    useAnalysisStore.setState((state) => ({
        agents: state.agents.map((a) =>
            done.has(a.name)
                ? { ...a, status: 'completed' as const, finishedAt: a.finishedAt ?? Date.now() }
                : a,
        ),
    }))
}

export interface ChatMessage {
    id: string
    role: 'user' | 'assistant' | 'system' | 'report'
    content: string
    timestamp: string
    agent?: string      // The name of the agent who sent the message
    section?: string    // only for role='report'
    complete?: boolean  // only for role='report'
}

const createInitialChatMessages = (): ChatMessage[] => []

interface BackgroundJobMeta {
    jobId: string
    symbol: string
    status: 'pending' | 'running' | 'completed' | 'failed'
    updatedAt: number
}

/**
 * Per-job view snapshot for the multi-task queue. The top-level store fields
 * keep driving the visible panel; this bucket freezes a task's view when it
 * goes to the background so switching back can restore it before replaying SSE.
 */
export interface JobViewSnapshot {
    jobId: string
    symbol: string
    name?: string
    status: 'pending' | 'running' | 'completed' | 'failed'
    createdAt: number
    updatedAt: number
    report: AnalysisReport | null
    agentReports: Record<string, string>
    streamingSections: Record<string, StreamingSectionState>
    agents: Agent[]
    riskItems: RiskItem[]
    keyMetrics: KeyMetric[]
    confidence: number | null
    targetPrice: number | null
    stopLoss: number | null
    currentHorizon: string | null
}

interface AnalysisState {
    // Current Job
    currentJobId: string | null
    /** Active (visible) task in the multi-task queue. */
    activeJobId: string | null
    /** Per-task view snapshots keyed by jobId. */
    jobViews: Record<string, JobViewSnapshot>
    currentSymbol: string
    jobStatus: JobStatus | null

    // Other background jobs (multi-task): kept so the panel can list / switch back
    // to a running analysis that lives in the sidecar after leaving this page.
    backgroundJobs: Record<string, BackgroundJobMeta>

    // Agents
    agents: Agent[]

    // Report
    report: AnalysisReport | null

    // Structured data from job.completed SSE event (LLM-extracted)
    riskItems: RiskItem[]
    keyMetrics: KeyMetric[]
    jobConfidence: number | null
    jobTargetPrice: number | null
    jobStopLoss: number | null

    // Streaming Report State (for typewriter effect) — keyed by section
    streamingSections: Record<string, StreamingSectionState>

    /**
     * Per-agent report text (keyed by English agent name).
     * Bull/Bear/Risk agents share section keys in SSE; this keeps their content distinct.
     */
    agentReports: Record<string, string>

    // Milestones for chat display
    milestones: MilestoneMessage[]

    // Chat messages (persisted across route changes)
    chatMessages: ChatMessage[]

    // Logs (kept for system messages only)
    logs: LogEntry[]

    // Loading States
    isAnalyzing: boolean
    isConnected: boolean

    // Current analysis horizon (for badge display)
    currentHorizon: string | null

    // Actions
    setCurrentJobId: (jobId: string | null) => void
    setActiveJobId: (jobId: string | null) => void
    /** Freeze the current top-level view into the job's snapshot bucket. */
    snapshotActiveView: (jobId?: string) => void
    /** Restore a task's snapshot into the top-level view. */
    restoreJobView: (jobId: string) => void
    /** Update lightweight queue metadata without touching the frozen view. */
    upsertJobViewMeta: (jobId: string, patch: Partial<Omit<JobViewSnapshot, 'jobId'>>) => void
    removeJobView: (jobId: string) => void
    setBackgroundJob: (meta: BackgroundJobMeta) => void
    removeBackgroundJob: (jobId: string) => void
    setCurrentSymbol: (symbol: string) => void
    setJobStatus: (status: JobStatus | null) => void
    updateAgentStatus: (event: AgentStatusEvent) => void
    updateAgentSnapshot: (event: AgentSnapshotEvent) => void
    addAgentMessage: (event: AgentMessageEvent) => void
    addAgentToolCall: (event: AgentToolCallEvent) => void
    addAgentReport: (event: AgentReportEvent) => void
    addReportChunk: (event: ReportChunkEvent) => void
    addAgentToken: (event: AgentTokenEvent) => void
    addMilestone: (event: AgentMilestoneEvent) => void
    addLog: (log: LogEntry) => void
    setReport: (report: AnalysisReport | null) => void
    setStructuredData: (data: {
        riskItems?: RiskItem[]
        keyMetrics?: KeyMetric[]
        confidence?: number | null
        targetPrice?: number | null
        stopLoss?: number | null
    }) => void
    setIsAnalyzing: (isAnalyzing: boolean) => void
    setIsConnected: (isConnected: boolean) => void
    setCurrentHorizon: (horizon: string | null) => void
    addChatMessage: (message: ChatMessage) => void
    appendToChatMessage: (id: string, chunk: string) => void
    setMessageContent: (id: string, content: string) => void
    markAgentMessagesComplete: (msgIds?: string[]) => void
    clearChatMessages: () => void
    clearSession: () => void
    reset: () => void
}

const initialAgents: Agent[] = [
    // Analyst Team
    { id: 'market', name: 'Market Analyst', team: 'Analyst Team', status: 'pending' },
    { id: 'social', name: 'Social Analyst', team: 'Analyst Team', status: 'pending' },
    { id: 'news', name: 'News Analyst', team: 'Analyst Team', status: 'pending' },
    { id: 'fundamentals', name: 'Fundamentals Analyst', team: 'Analyst Team', status: 'pending' },
    { id: 'macro', name: 'Macro Analyst', team: 'Analyst Team', status: 'pending' },
    { id: 'smart_money', name: 'Smart Money Analyst', team: 'Analyst Team', status: 'pending' },

    // Research Team
    { id: 'game_theory', name: 'Game Theory Manager', team: 'Research Team', status: 'pending' },
    { id: 'bull', name: 'Bull Researcher', team: 'Research Team', status: 'pending' },
    { id: 'bear', name: 'Bear Researcher', team: 'Research Team', status: 'pending' },
    { id: 'research_manager', name: 'Research Manager', team: 'Research Team', status: 'pending' },

    // Trading Team
    { id: 'trader', name: 'Trader', team: 'Trading Team', status: 'pending' },

    // Risk Management
    { id: 'aggressive', name: 'Aggressive Analyst', team: 'Risk Management', status: 'pending' },
    { id: 'conservative', name: 'Conservative Analyst', team: 'Risk Management', status: 'pending' },
    { id: 'neutral', name: 'Neutral Analyst', team: 'Risk Management', status: 'pending' },

    // Portfolio Management
    { id: 'portfolio_manager', name: 'Portfolio Manager', team: 'Portfolio Management', status: 'pending' },
]

// Debounced localStorage storage to avoid blocking the main thread on every token
function createDebouncedStorage(delay = 800) {
    let pending: [string, string] | null = null
    let timer: ReturnType<typeof setTimeout> | null = null
    return {
        getItem: (name: string) => localStorage.getItem(name),
        setItem: (name: string, value: string) => {
            pending = [name, value]
            if (timer) clearTimeout(timer)
            timer = setTimeout(() => {
                if (pending) { localStorage.setItem(pending[0], pending[1]); pending = null }
                timer = null
            }, delay)
        },
        removeItem: (name: string) => {
            pending = null
            if (timer) { clearTimeout(timer); timer = null }
            localStorage.removeItem(name)
        },
    }
}
const debouncedStorage = createDebouncedStorage()

export const useAnalysisStore = create<AnalysisState>()(persist((set) => ({
    currentJobId: null,
    activeJobId: null,
    jobViews: {},
    backgroundJobs: {},
    currentSymbol: '000001.SH',
    jobStatus: null,
    agents: initialAgents,
    report: null,
    riskItems: [],
    keyMetrics: [],
    jobConfidence: null,
    jobTargetPrice: null,
    jobStopLoss: null,
    streamingSections: {},
    agentReports: {},
    milestones: [],
    chatMessages: createInitialChatMessages(),
    logs: [],
    isAnalyzing: false,
    isConnected: false,
    currentHorizon: null,

    setCurrentJobId: (jobId) => set({ currentJobId: jobId, activeJobId: jobId }),

    setActiveJobId: (jobId) => set({ activeJobId: jobId }),

    snapshotActiveView: (jobId) =>
        set((state) => {
            const id = jobId ?? state.activeJobId ?? state.currentJobId
            if (!id) return state
            const prev = state.jobViews[id]
            const snapshot: JobViewSnapshot = {
                ...prev,
                jobId: id,
                // The bucket's symbol is fixed at creation; the top-level currentSymbol
                // may have already moved to another task when this snapshot runs, so
                // never overwrite the frozen symbol.
                symbol: prev?.symbol ?? state.currentSymbol,
                status: state.isAnalyzing ? 'running' : (prev?.status ?? 'pending'),
                createdAt: prev?.createdAt ?? Date.now(),
                updatedAt: Date.now(),
                report: state.report,
                agentReports: state.agentReports,
                streamingSections: state.streamingSections,
                agents: state.agents,
                riskItems: state.riskItems,
                keyMetrics: state.keyMetrics,
                confidence: state.jobConfidence,
                targetPrice: state.jobTargetPrice,
                stopLoss: state.jobStopLoss,
                currentHorizon: state.currentHorizon,
            }
            return { jobViews: { ...state.jobViews, [id]: snapshot } }
        }),

    restoreJobView: (jobId) =>
        set((state) => {
            const snap = state.jobViews[jobId]
            if (!snap) return state
            return {
                activeJobId: jobId,
                currentJobId: jobId,
                currentSymbol: snap.symbol,
                report: snap.report,
                agentReports: snap.agentReports,
                streamingSections: snap.streamingSections,
                agents: snap.agents,
                riskItems: snap.riskItems,
                keyMetrics: snap.keyMetrics,
                jobConfidence: snap.confidence,
                jobTargetPrice: snap.targetPrice,
                jobStopLoss: snap.stopLoss,
                currentHorizon: snap.currentHorizon ?? null,
                isAnalyzing: snap.status === 'running' || snap.status === 'pending',
                isConnected: false,
            }
        }),

    upsertJobViewMeta: (jobId, patch) =>
        set((state) => {
            const prev = state.jobViews[jobId]
            const base: JobViewSnapshot = prev ?? {
                jobId,
                symbol: state.currentSymbol,
                status: 'pending',
                createdAt: Date.now(),
                updatedAt: Date.now(),
                report: null,
                agentReports: {},
                streamingSections: {},
                agents: initialAgents.map((a) => ({ ...a, status: 'pending' })),
                riskItems: [],
                keyMetrics: [],
                confidence: null,
                targetPrice: null,
                stopLoss: null,
                currentHorizon: null,
            }
            return {
                jobViews: { ...state.jobViews, [jobId]: { ...base, ...patch, jobId, updatedAt: Date.now() } },
            }
        }),

    removeJobView: (jobId) =>
        set((state) => {
            const next = { ...state.jobViews }
            delete next[jobId]
            return { jobViews: next }
        }),

    setBackgroundJob: (meta) =>
        set((state) => ({
            backgroundJobs: { ...state.backgroundJobs, [meta.jobId]: meta },
        })),

    removeBackgroundJob: (jobId) =>
        set((state) => {
            const next = { ...state.backgroundJobs }
            delete next[jobId]
            return { backgroundJobs: next }
        }),

    setCurrentSymbol: (symbol) => set({ currentSymbol: symbol }),

    setJobStatus: (status) => set({ jobStatus: status }),

    updateAgentStatus: (event) => {
        if (!event.agent) return

        if (event.status === 'in_progress') {
            clearPendingCompleted(event.agent)
            set((state) => ({
                agents: state.agents.map((agent) => {
                    if (agent.name !== event.agent) return agent
                    return {
                        ...agent,
                        status: 'in_progress',
                        startedAt: agent.startedAt ?? Date.now(),
                        finishedAt: undefined,
                    }
                }),
            }))
            return
        }

        if (event.status === 'completed') {
            // Keep showing in_progress until debounce elapses; a follow-up
            // in_progress (next tool round / debate round) cancels this timer.
            clearPendingCompleted(event.agent)
            pendingCompletedTimers.set(
                event.agent,
                setTimeout(() => {
                    pendingCompletedTimers.delete(event.agent)
                    set((state) => ({
                        agents: state.agents.map((agent) => {
                            if (agent.name !== event.agent) return agent
                            return {
                                ...agent,
                                status: 'completed',
                                finishedAt: Date.now(),
                            }
                        }),
                    }))
                }, COMPLETED_DEBOUNCE_MS),
            )
            return
        }

        clearPendingCompleted(event.agent)
        set((state) => ({
            agents: state.agents.map((agent) => {
                if (agent.name !== event.agent) return agent
                const updates: Partial<Agent> = { status: event.status }
                if ((event.status === 'skipped' || event.status === 'error') && !agent.finishedAt) {
                    updates.finishedAt = Date.now()
                }
                return { ...agent, ...updates }
            }),
        }))
    },

    updateAgentSnapshot: (event) => set((state) => {
        const agentMap = new Map(event.agents.map(a => [a.agent, a.status]))
        return {
            agents: state.agents.map(agent => {
                const next = agentMap.get(agent.name)
                if (!next) return agent
                return { ...agent, status: next }
            }),
        }
    }),

    // 不再将消息和工具调用添加到日志（已移至后端）
    addAgentMessage: () => {
        // 消息已移至后端日志，前端不再显示
    },

    addAgentToolCall: () => {
        // 工具调用已移至后端日志，前端不再显示
    },

    addAgentReport: (event) => set((state) => {
        const soleAgent = SECTION_SOLE_AGENT[event.section]
        const nextAgentReports = soleAgent
            ? { ...state.agentReports, [soleAgent]: event.content }
            : state.agentReports
        return {
            report: {
                ...state.report,
                [event.section]: event.content,
            } as AnalysisReport,
            agentReports: nextAgentReports,
        }
    }),

    // 处理报告分片（支持打字机效果）
    addReportChunk: (event) => set((state) => {
        const { section, chunk, is_complete } = event
        const current = state.streamingSections[section] || {
            buffer: '',
            displayed: '',
            isTyping: false,
            isComplete: false
        }

        if (is_complete) {
            const soleAgent = SECTION_SOLE_AGENT[section]
            return {
                streamingSections: {
                    ...state.streamingSections,
                    [section]: {
                        ...current,
                        buffer: current.buffer,
                        displayed: current.buffer,
                        isTyping: false,
                        isComplete: true
                    }
                },
                agentReports: soleAgent
                    ? { ...state.agentReports, [soleAgent]: current.buffer }
                    : state.agentReports,
            }
        }

        // 追加到缓冲区
        const newBuffer = current.buffer + chunk
        const soleAgent = SECTION_SOLE_AGENT[section]
        return {
            streamingSections: {
                ...state.streamingSections,
                [section]: {
                    ...current,
                    buffer: newBuffer,
                    displayed: newBuffer,
                    isTyping: true,
                    isComplete: false
                }
            },
            agentReports: soleAgent
                ? { ...state.agentReports, [soleAgent]: newBuffer }
                : state.agentReports,
        }
    }),

    addAgentToken: (event) => set((state) => {
        const { report: section, token, agent } = event
        if (!token) return state

        const agentKey = agent || (section ? SECTION_SOLE_AGENT[section] : undefined)
        const nextStreaming = { ...state.streamingSections }

        if (section) {
            const current = nextStreaming[section] || {
                buffer: '', displayed: '', isTyping: false, isComplete: false,
            }
            const buf = current.buffer + token
            nextStreaming[section] = {
                ...current, buffer: buf, displayed: buf, isTyping: true, isComplete: false,
            }
        }

        if (agentKey) {
            const agentStreamKey = `agent:${agentKey}`
            const current = nextStreaming[agentStreamKey] || {
                buffer: '', displayed: '', isTyping: false, isComplete: false,
            }
            const buf = current.buffer + token
            nextStreaming[agentStreamKey] = {
                ...current, buffer: buf, displayed: buf, isTyping: true, isComplete: false,
            }
            return {
                streamingSections: nextStreaming,
                agentReports: { ...state.agentReports, [agentKey]: buf },
            }
        }

        return { streamingSections: nextStreaming }
    }),

    // 添加里程碑消息（用于对话框显示）
    addMilestone: (event) => set((state) => {
        const milestone: MilestoneMessage = {
            id: `${Date.now()}-${Math.random()}`,
            stage: event.stage,
            title: event.title,
            summary: event.summary,
            timestamp: event.timestamp
        }
        return {
            milestones: [...state.milestones, milestone]
        }
    }),

    // 添加聊天记录（持久化）
    addChatMessage: (message) => set((state) => ({
        chatMessages: [...state.chatMessages, message]
    })),

    // 追加内容到已有消息（用于流式报告 chunk 更新）
    appendToChatMessage: (id, chunk) => set((state) => ({
        chatMessages: state.chatMessages.map(m =>
            m.id === id ? { ...m, content: m.content + chunk } : m
        )
    })),

    setMessageContent: (id, content) => set((state) => ({
        chatMessages: state.chatMessages.map(m =>
            m.id === id ? { ...m, content } : m
        )
    })),

    // 批量标记 agent 消息为已完成；不传 msgIds 则标记所有 agent assistant 消息
    markAgentMessagesComplete: (msgIds?: string[]) => set((state) => ({
        chatMessages: state.chatMessages.map(m => {
            if (m.role !== 'assistant' || !m.agent || m.complete) return m
            if (msgIds && !msgIds.includes(m.id)) return m
            return { ...m, complete: true }
        })
    })),

    // 清空聊天记录
    clearChatMessages: () => set({
        chatMessages: createInitialChatMessages()
    }),

    clearSession: () => {
        clearPendingCompleted()
        set({
            currentJobId: null,
            activeJobId: null,
            jobViews: {},
            currentSymbol: '000001.SH',
            jobStatus: null,
            agents: initialAgents.map(a => ({ ...a, status: 'pending' })),
            report: null,
            riskItems: [],
            keyMetrics: [],
            jobConfidence: null,
            jobTargetPrice: null,
            jobStopLoss: null,
            streamingSections: {},
            agentReports: {},
            milestones: [],
            chatMessages: createInitialChatMessages(),
            logs: [],
            isAnalyzing: false,
            isConnected: false,
            currentHorizon: null,
        })
    },

    addLog: (log) => set((state) => ({
        logs: [log, ...state.logs].slice(0, 100)
    })),

    setReport: (report) => set((state) => ({
        report,
        currentSymbol: report?.symbol || state.currentSymbol,
    })),

    setStructuredData: (data) => set({
        riskItems: data.riskItems ?? [],
        keyMetrics: data.keyMetrics ?? [],
        jobConfidence: data.confidence ?? null,
        jobTargetPrice: data.targetPrice ?? null,
        jobStopLoss: data.stopLoss ?? null,
    }),

    setIsAnalyzing: (isAnalyzing) => {
        if (!isAnalyzing) flushPendingCompleted()
        set({ isAnalyzing })
    },

    setIsConnected: (isConnected) => set({ isConnected }),

    setCurrentHorizon: (horizon) => set({ currentHorizon: horizon }),

    reset: () => {
        clearPendingCompleted()
        set((state) => ({
            currentJobId: null,
            currentSymbol: state.currentSymbol,
            jobStatus: null,
            agents: initialAgents.map(a => ({ ...a, status: 'pending' })),
            report: null,
            riskItems: [],
            keyMetrics: [],
            jobConfidence: null,
            jobTargetPrice: null,
            jobStopLoss: null,
            streamingSections: {},
            agentReports: {},
            milestones: [],
            // 注意：reset时不清空chatMessages，保持对话历史
            logs: [],
            isAnalyzing: false,
            isConnected: false,
            currentHorizon: null,
        }))
    }
}), {
    name: 'tradingagents-analysis',
    version: 2,
    storage: createJSONStorage(() => debouncedStorage),
    partialize: (state) => ({
        currentSymbol: state.currentSymbol,
        currentJobId: state.currentJobId,
        backgroundJobs: state.backgroundJobs,
        report: state.report,
        agentReports: state.agentReports,
        riskItems: state.riskItems,
        keyMetrics: state.keyMetrics,
        jobConfidence: state.jobConfidence,
        jobTargetPrice: state.jobTargetPrice,
        jobStopLoss: state.jobStopLoss,
    }),
    merge: (persistedState, currentState) => {
        const persisted = (persistedState ?? {}) as Partial<AnalysisState>
        return {
            ...currentState,
            ...persisted,
            activeJobId: null,
            jobViews: {},
            jobStatus: null,
            agents: initialAgents.map(a => ({ ...a, status: 'pending' })),
            streamingSections: {},
            agentReports: persisted.agentReports ?? {},
            milestones: [],
            logs: [],
            isAnalyzing: false,
            isConnected: false,
            chatMessages: createInitialChatMessages(),
        }
    },
}))
