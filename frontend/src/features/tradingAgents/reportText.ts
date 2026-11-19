import type { AnalysisReport, KeyMetric, ReportDetail, RiskItem } from './types'

type ReportLike = AnalysisReport | ReportDetail | null | undefined

export function detectDecisionLabel(text?: string | null): string | null {
    if (!text) return null
    const normalized = text.toLowerCase()
    if (normalized.includes('增持')) return '增持'
    if (normalized.includes('减持')) return '减持'
    if (normalized.includes('buy') || normalized.includes('买入')) return '买入'
    if (normalized.includes('sell') || normalized.includes('卖出')) return '卖出'
    if (normalized.includes('watch') || normalized.includes('观望')) return '观望'
    if (normalized.includes('hold') || normalized.includes('持有')) return '持有'
    return null
}

export function sanitizeReportMarkdown(text?: string | null): string {
    if (!text) return ''
    return text
        .replace(/<!--\s*VERDICT:[^>]*-->/gi, '') // strip machine-readable verdict tag
        .replace(/FINAL TRANSACTION PROPOSAL:\s*\**\s*BUY\s*\**/gi, '最终交易建议：买入')
        .replace(/FINAL TRANSACTION PROPOSAL:\s*\**\s*SELL\s*\**/gi, '最终交易建议：卖出')
        .replace(/FINAL TRANSACTION PROPOSAL:\s*\**\s*HOLD\s*\**/gi, '最终交易建议：观望')
        .replace(/FINAL VERDICT:\s*/gi, '最终裁决：')
        .replace(/HOLD with Conditional Trigger/gi, '观望（条件触发）')
        .replace(/BUY with Conditional Trigger/gi, '买入（条件触发）')
        .replace(/SELL with Conditional Trigger/gi, '卖出（条件触发）')
}

export function buildAgentSummary(text?: string | null): string {
    const cleaned = sanitizeReportMarkdown(text)
        .replace(/^#+\s*/gm, '')
        .replace(/\*\*/g, '')
        .replace(/\|/g, ' ')
        .replace(/\s+/g, ' ')
        .trim()
    const decision = detectDecisionLabel(cleaned)
    if (decision) return decision
    if (/偏多|看多|上涨|突破/.test(cleaned)) return '偏多'
    if (/偏空|看空|下跌|回撤/.test(cleaned)) return '偏空'
    if (/中性|震荡/.test(cleaned)) return '中性'
    if (cleaned.includes('风险')) return '风控结论'
    if (cleaned.includes('计划')) return '计划已生成'
    return cleaned.slice(0, 18) || '报告已生成'
}

export interface Verdict {
    direction: string
    reason: string
}

// Map English direction values (en.py prompts) to Chinese display labels
const DIRECTION_ALIAS: Record<string, string> = {
    BULLISH:  '看多',
    BEARISH:  '看空',
    NEUTRAL:  '中性',
    CAUTIOUS: '谨慎',
    SLIGHTLY_BULLISH: '偏多',
    SLIGHTLY_BEARISH: '偏空',
    'SLIGHTLY BULLISH': '偏多',
    'SLIGHTLY BEARISH': '偏空',
}

/**
 * Extract the structured verdict embedded by the agent as an HTML comment.
 * Format: <!-- VERDICT: {"direction": "...", "reason": "..."} -->
 */
export type TradingUiDecision = 'buy' | 'sell' | 'hold' | 'add' | 'reduce' | 'watch'

/** Map explicit trade action strings (decision field, closing line) to UI decision. */
export function mapTradingAction(value?: string | null): TradingUiDecision | undefined {
    if (!value) return undefined
    const d = value.toUpperCase()
    if (d.includes('BUY') || value.includes('买入')) return 'buy'
    if (d.includes('ADD') || value.includes('增持')) return 'add'
    if (d.includes('SELL') || value.includes('卖出')) return 'sell'
    if (d.includes('REDUCE') || value.includes('减持')) return 'reduce'
    if (d.includes('WATCH') || value.includes('观望')) return 'watch'
    if (d.includes('HOLD') || value.includes('中性') || value.includes('谨慎') || value.includes('持有') || value.includes('空仓')) return 'hold'
    return undefined
}

/** Map VERDICT / direction bias to trade action (aligned with backend verdictDecision). */
export function mapVerdictDirectionToAction(value?: string | null): TradingUiDecision | undefined {
    if (!value) return undefined
    if (value.includes('看多') || value.includes('偏多')) return 'buy'
    if (value.includes('看空') || value.includes('偏空')) return 'sell'
    if (value.includes('观望')) return 'watch'
    if (value.includes('中性') || value.includes('谨慎') || value.includes('持有') || value.includes('空仓')) return 'hold'
    return undefined
}

/** @deprecated Use mapTradingAction — kept for callers that fuzzy-match direction as action. */
export function mapStructuredDecision(value?: string | null): TradingUiDecision | undefined {
    return mapTradingAction(value) ?? mapVerdictDirectionToAction(value)
}

/** Normalize API/stream price: treat 0 and non-finite as missing. */
export function normalizeTradePrice(value?: number | null): number | undefined {
    if (value == null || !Number.isFinite(value) || value <= 0) return undefined
    return value
}

const STRUCTURED_DIRECTION_RE = /方向[:：]\s*[*_]*\s*(看多|看空|观望|持有|空仓|中性|谨慎|偏多|偏空)/
const ZERO_POSITION_RE = /建议仓位[:：]\s*0\s*[%％]/
const NO_ENTRY_RE = /入场(?:点位)?[:：]\s*无/

/** Slice from the last 【最终决策】 heading to end; earlier opinion paragraphs must not pollute the verdict. */
function finalDecisionBlock(text?: string | null): string {
    if (!text) return ''
    const idx = text.lastIndexOf('【最终决策】')
    return idx < 0 ? text : text.slice(idx)
}

function isHoldDirectionWord(dir: string): boolean {
    return ['观望', '持有', '空仓', '中性', '谨慎'].includes(dir.trim())
}

/** Detect structured non-actionable report (观望 + 0%仓位 / 无入场). */
export function extractStructuredHoldFromReport(text?: string | null): boolean {
    if (!text) return false
    const block = finalDecisionBlock(text)
    const dirMatch = block.match(STRUCTURED_DIRECTION_RE) ?? text.match(STRUCTURED_DIRECTION_RE)
    const dir = dirMatch?.[1]?.trim()
    if (!dir || !isHoldDirectionWord(dir)) return false
    if (
        ZERO_POSITION_RE.test(block || text) ||
        NO_ENTRY_RE.test(block || text) ||
        text.includes('严禁新开仓') ||
        text.includes('空仓观望')
    ) {
        return true
    }
    return dir === '观望' && text.includes('0%')
}

/** Read structured 方向 from the 【最终决策】 block first, falling back to whole text. */
export function extractStructuredDirection(text?: string | null): string | undefined {
    if (!text) return undefined
    const block = finalDecisionBlock(text)
    const m = block.match(STRUCTURED_DIRECTION_RE) ?? text.match(STRUCTURED_DIRECTION_RE)
    return m?.[1]?.trim()
}

/** Closing line or structured block trade action (before VERDICT / API fields). */
export function extractClosingTradeDecision(text?: string | null): TradingUiDecision | undefined {
    if (!text) return undefined
    if (extractStructuredHoldFromReport(text)) return 'watch'
    const structuredDir = extractStructuredDirection(text)
    if (structuredDir) {
        const fromDir = mapVerdictDirectionToAction(structuredDir)
        if (fromDir) return fromDir
    }
    const lines = text.split('\n')
    for (let i = lines.length - 1; i >= 0; i--) {
        const line = lines[i]?.trim()
        if (!line) continue
        if (
            line.includes('最终交易建议') ||
            line.includes('【最终决策】') ||
            /FINAL TRANSACTION PROPOSAL/i.test(line)
        ) {
            return mapTradingAction(line)
        }
    }
    return undefined
}

/** Whether UI should show target/stop price boxes. */
export function shouldShowTradePrices(decision?: TradingUiDecision): boolean {
    return decision === 'buy' || decision === 'sell' || decision === 'add' || decision === 'reduce'
}

/** Drop prices for HOLD/watch and reject invalid BUY/SELL pairs. */
export function resolveTradePrices(
    decision: TradingUiDecision | undefined,
    target?: number,
    stop?: number,
): { target?: number; stop?: number } {
    if (!decision || !shouldShowTradePrices(decision)) {
        return {}
    }
    const t = normalizeTradePrice(target)
    const s = normalizeTradePrice(stop)
    if (t == null || s == null) {
        return { target: t, stop: s }
    }
    if (decision === 'buy' || decision === 'add') {
        if (t <= s) return {}
    }
    if (decision === 'sell' || decision === 'reduce') {
        if (t >= s) return {}
    }
    return { target: t, stop: s }
}

/** Single source of truth: structured block > closing line > API decision > VERDICT — never infer BUY from 偏多 when 观望 present. */
export function resolveTradingDecision(input: {
    decision?: string | null
    direction?: string | null
    finalTradeDecision?: string | null
}): TradingUiDecision | undefined {
    const fromStructured = extractClosingTradeDecision(input.finalTradeDecision)
    if (fromStructured) return fromStructured
    const fromDecision = mapTradingAction(input.decision)
    if (fromDecision) {
        if (fromDecision === 'hold' && (input.direction?.includes('观望') || extractStructuredDirection(input.finalTradeDecision) === '观望')) {
            return 'watch'
        }
        return fromDecision
    }
    const verdict = extractVerdict(input.finalTradeDecision)
    if (verdict) {
        const fromVerdict = mapVerdictDirectionToAction(verdict.direction)
        if (fromVerdict === 'hold' && verdict.direction.includes('观望')) return 'watch'
        return fromVerdict
    }
    return undefined
}

export function extractVerdict(text?: string | null): Verdict | null {
    if (!text) return null
    const m = text.match(/<!--\s*VERDICT:\s*(\{[^>]+\})\s*-->/)
    if (!m) return null
    try {
        const parsed = JSON.parse(m[1]) as { direction?: string; reason?: string }
        if (!parsed.direction || !parsed.reason) return null
        const direction = DIRECTION_ALIAS[parsed.direction.toUpperCase()] ?? parsed.direction
        return { direction, reason: parsed.reason.trim().slice(0, 42) }
    } catch {
        return null
    }
}

const RISK_HEADERS = ['【主要风险】', '【风险提示】', '主要风险', '风险提示', '风险因素']

function inferRiskLevel(text: string): RiskItem['level'] {
    const t = text.toLowerCase()
    if (/极高|重大|严重|high/.test(t)) return 'high'
    if (/中等|一般|medium/.test(t)) return 'medium'
    if (/较低|轻微|low/.test(t)) return 'low'
    return 'medium'
}

function cleanRiskLine(line: string): string {
    return line.replace(/^[-•*]\s*/, '').replace(/^\d+\.\s*/, '').trim()
}

/** Split a risk line on semicolons / circled-number bullets, stripping leading numbering. */
function splitRiskSegments(line: string): string[] {
    return line
        .split(/[；;]/)
        .flatMap((seg) => seg.split(/[①②③④⑤⑥⑦⑧⑨⑩]/))
        .map((s) => s.replace(/^[-•*·、\s]/, '').trim())
        .filter(Boolean)
}

function addRiskItem(items: RiskItem[], seen: Set<string>, raw: string): void {
    const name = cleanRiskLine(raw)
    if (!name || name.length < 4 || seen.has(name)) return
    seen.add(name)
    items.push({ name, level: inferRiskLevel(name) })
}

export function deriveRiskItems(report?: ReportLike): RiskItem[] {
    if (!report) return []
    const texts = [
        report.final_trade_decision,
        report.investment_plan,
        report.trader_investment_plan,
        report.news_report,
        report.sentiment_report,
    ].filter(Boolean) as string[]

    const items: RiskItem[] = []
    const seen = new Set<string>()

    for (const text of texts) {
        for (const header of RISK_HEADERS) {
            const idx = text.indexOf(header)
            if (idx < 0) continue
            const chunk = text.slice(idx + header.length)
            for (const line of chunk.split('\n')) {
                const trimmed = line.trim()
                if (!trimmed) continue
                if (trimmed.startsWith('【') && trimmed.length < 20) break
                if (trimmed.startsWith('-') || trimmed.startsWith('•') || trimmed.startsWith('*')) {
                    for (const seg of splitRiskSegments(trimmed)) addRiskItem(items, seen, seg)
                } else if (/[①②③④⑤⑥⑦⑧⑨⑩；;]/.test(trimmed)) {
                    for (const seg of splitRiskSegments(trimmed)) addRiskItem(items, seen, seg)
                }
            }
        }

        const inline = text.match(/(?:^|\n)\s*(?:\d+\.\s*)?主要风险[:：]\s*([^\n]+)/)
        if (inline?.[1]) {
            for (const seg of splitRiskSegments(inline[1])) addRiskItem(items, seen, seg)
        }

        const notice = text.match(/【风险提示】\s*([\s\S]*?)(?:\n【|$)/)
        if (notice?.[1]) {
            for (const line of notice[1].split('\n')) {
                const trimmed = line.trim()
                if (trimmed.startsWith('-') || trimmed.startsWith('•') || trimmed.startsWith('*')) {
                    addRiskItem(items, seen, trimmed)
                }
            }
        }

        if (items.length === 0) {
            for (const line of text.split('\n')) {
                const trimmed = line.trim()
                if (trimmed.includes('风险') && /^[-•*]/.test(trimmed)) {
                    addRiskItem(items, seen, trimmed)
                }
            }
        }
    }

    return items.slice(0, 6)
}

const METRIC_PATTERNS: Array<{ name: string; re: RegExp }> = [
    { name: 'PE', re: /(?:PE|市盈率)(?:\([^)]*\))?[:：\s]*([0-9.,]+)/i },
    { name: 'PB', re: /(?:PB|市净率)(?:\([^)]*\))?[:：\s]*([0-9.,]+)/i },
    { name: '市值', re: /市值[:：\s]*([0-9.,]+(?:亿|万|元)?)/ },
    { name: '换手率', re: /(?:换手率|换手)[:：\s]*([0-9.,]+%?)/ },
    { name: 'ROE', re: /ROE[:：\s]*([0-9.,]+%?)/i },
    { name: 'RSI', re: /RSI[:：\s]*([0-9.,]+)/i },
    { name: '置信度', re: /置信度[:：\s]*((?:高|中|低)|\d+%?)/ },
    { name: '建议仓位', re: /建议仓位[:：\s]*([^\n|]+)/ },
    { name: '涨跌幅', re: /涨跌幅[:：\s]*([+-]?[0-9.,]+%?)/ },
]

const METRIC_KEYWORDS = ['PE', 'PB', '市值', '换手', 'ROE', 'RSI', '置信', '仓位', '涨跌', '市盈率', '市净率']

function inferMetricStatus(name: string, value: string): KeyMetric['status'] {
    const v = value.toLowerCase()
    if (/高|偏高|超买|风险|警示|bad/.test(v) || (name === 'PE' && parseFloat(v) > 50)) return 'bad'
    if (/低|偏低|超卖|良好|good/.test(v)) return 'good'
    return 'neutral'
}

function addMetric(items: KeyMetric[], seen: Set<string>, name: string, value: string): void {
    const v = value.trim()
    if (!v || seen.has(name)) return
    seen.add(name)
    items.push({ name, value: v, status: inferMetricStatus(name, v) })
}

export function deriveKeyMetrics(report?: ReportLike): KeyMetric[] {
    if (!report) return []
    const texts = [
        report.fundamentals_report,
        report.market_report,
        report.macro_report,
        report.smart_money_report,
        report.final_trade_decision,
    ].filter(Boolean) as string[]

    const items: KeyMetric[] = []
    const seen = new Set<string>()

    for (const text of texts) {
        for (const { name, re } of METRIC_PATTERNS) {
            const m = text.match(re)
            if (m?.[1]) addMetric(items, seen, name, m[1])
        }

        for (const line of text.split('\n')) {
            if (!line.includes('|')) continue
            const cells = line.split('|').map((c) => c.trim()).filter(Boolean)
            // 概览粗体行：逐片段跑命名正则，避免 name/value 错位。
            for (const cell of cells) {
                for (const { name, re } of METRIC_PATTERNS) {
                    const m = cell.match(re)
                    if (m?.[1]) addMetric(items, seen, name, m[1])
                }
            }
            // 真正表格行：仅当首列是已知指标名时才做 name/value 配对。
            if (cells.length >= 2 && !/^[-:]+$/.test(cells[0])) {
                if (METRIC_KEYWORDS.some((k) => cells[0].includes(k))) {
                    addMetric(items, seen, cells[0], cells[1])
                }
            }
        }
    }

    return items.slice(0, 8)
}
