package httpapi

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log"
	"os"
	"regexp"
	"strings"
	"sync"
	"time"

	"aistock/backend/internal/analysis/agents/analysts/news"
	"aistock/backend/internal/analysis/agents/common"
	"aistock/backend/internal/analysis/graph"
	"aistock/backend/internal/analysis/tools"

	"github.com/cloudwego/eino/adk"
	"github.com/cloudwego/eino/schema"
)

// AnalysisWorkflowTimeout 单次完整分析（含并行分析师阶段与后续辩论）的最长等待时间。
// W6 联网调研在并行分析师阶段内后台预取（W6PrefetchTimeout，默认 18m），
// 新闻分析师 Run 时等待预取结果，与其余 5 名分析师并行，不单独占用工作流前的串行时间。
func AnalysisWorkflowTimeout() time.Duration {
	const defaultD = 30 * time.Minute
	s := os.Getenv("AIGOSTOCK_ANALYSIS_TIMEOUT")
	if s == "" {
		return defaultD
	}
	d, err := time.ParseDuration(s)
	if err != nil || d <= 0 {
		return defaultD
	}
	return d
}

// JobEmit 分析过程的 SSE / 订阅广播回调（event 名与 Python、前端一致）
type JobEmit func(event string, data map[string]any)

// RegisterJob 将任务登记到内存（chat 路径在已发送 job.created 后调用，避免重复 event）
func (jm *JobManager) RegisterJob(jobID string, req AnalysisRequest) *Job {
	now := time.Now()
	job := &Job{
		ID:        jobID,
		Status:    JobStatusPending,
		Request:   req,
		CreatedAt: now,
		UpdatedAt: now,
		Events:    []JobEvent{},
	}
	jm.mu.Lock()
	jm.jobs[jobID] = job
	jm.mu.Unlock()
	return job
}

// CommitJobResult 仅更新任务为成功（不附加 SSE 事件，由调用方 emit job.completed）
func (jm *JobManager) CommitJobResult(jobID string, result map[string]any) {
	jm.mu.Lock()
	defer jm.mu.Unlock()
	if job, ok := jm.jobs[jobID]; ok {
		job.Result = result
		job.Status = JobStatusCompleted
		job.UpdatedAt = time.Now()
	}
}

// CommitJobFailure 仅更新任务为失败（不附加事件）
func (jm *JobManager) CommitJobFailure(jobID string) {
	jm.mu.Lock()
	defer jm.mu.Unlock()
	if job, ok := jm.jobs[jobID]; ok {
		job.Status = JobStatusFailed
		job.UpdatedAt = time.Now()
	}
}

var (
	reTSCodeDot = regexp.MustCompile(`(?i)\b(\d{6})\.(SH|SZ|BJ)\b`)
	reSH6       = regexp.MustCompile(`(?i)SH(\d{6})\b`)
	reSZ6       = regexp.MustCompile(`(?i)SZ(\d{6})\b`)
	reBJ6       = regexp.MustCompile(`(?i)BJ(\d{6})\b`)
	reBare6     = regexp.MustCompile(`\b(\d{6})\b`)
	reISODate   = regexp.MustCompile(`\b(20\d{2})-(\d{2})-(\d{2})\b`)
)

func todayCN() string {
	loc, err := time.LoadLocation("Asia/Shanghai")
	if err != nil {
		return time.Now().Format("2006-01-02")
	}
	return time.Now().In(loc).Format("2006-01-02")
}

// ExtractSymbolAndDate 规则优先提取标的与交易日，失败则返回需走 LLM 的错误
func ExtractSymbolAndDate(text string) (symbol string, tradeDate string, needLLM bool) {
	tradeDate = todayCN()
	if m := reISODate.FindStringSubmatch(text); len(m) == 4 {
		tradeDate = fmt.Sprintf("%s-%s-%s", m[1], m[2], m[3])
	}

	if m := reTSCodeDot.FindStringSubmatch(text); len(m) == 3 {
		return strings.ToUpper(m[1] + "." + m[2]), tradeDate, false
	}
	if m := reSH6.FindStringSubmatch(text); len(m) == 2 {
		return m[1] + ".SH", tradeDate, false
	}
	if m := reSZ6.FindStringSubmatch(text); len(m) == 2 {
		return m[1] + ".SZ", tradeDate, false
	}
	if m := reBJ6.FindStringSubmatch(text); len(m) == 2 {
		return m[1] + ".BJ", tradeDate, false
	}
	if m := reBare6.FindStringSubmatch(text); len(m) == 2 {
		code := m[1]
		switch {
		case strings.HasPrefix(code, "6") || strings.HasPrefix(code, "5"):
			return code + ".SH", tradeDate, false
		case strings.HasPrefix(code, "0") || strings.HasPrefix(code, "3") || strings.HasPrefix(code, "2"):
			return code + ".SZ", tradeDate, false
		case strings.HasPrefix(code, "8") || strings.HasPrefix(code, "4"):
			return code + ".BJ", tradeDate, false
		}
	}
	return "", tradeDate, true
}

// ExtractSymbolWithLLM 使用单次 Generate 抽取 JSON
func ExtractSymbolWithLLM(ctx context.Context, userText string) (symbol string, tradeDate string, err error) {
	cm, err := common.TryChatModel(ctx)
	if err != nil {
		return "", "", err
	}
	tradeDate = todayCN()
	sys := `你是 A 股标的提取器。只输出一行 JSON，不要 markdown。格式 strictly:
{"symbol":"600519.SH","trade_date":"2006-01-02"} 或 {"symbol":"","trade_date":""}
symbol 必须是 ts_code（六位代码.SH/.SZ/.BJ）。trade_date 若无法确定则用空字符串。`
	user := "用户原话：\n" + userText
	msgs := []*schema.Message{
		schema.SystemMessage(sys),
		schema.UserMessage(user),
	}
	out, err := cm.Generate(ctx, msgs)
	if err != nil {
		return "", "", err
	}
	raw := strings.TrimSpace(out.Content)
	raw = strings.TrimPrefix(raw, "```json")
	raw = strings.TrimPrefix(raw, "```")
	raw = strings.TrimSuffix(raw, "```")
	raw = strings.TrimSpace(raw)

	var payload struct {
		Symbol    string `json:"symbol"`
		TradeDate string `json:"trade_date"`
	}
	if e := json.Unmarshal([]byte(raw), &payload); e != nil {
		return "", "", fmt.Errorf("parse extract json: %w", e)
	}
	if payload.Symbol == "" {
		return "", "", fmt.Errorf("model could not extract symbol")
	}
	if payload.TradeDate != "" {
		tradeDate = payload.TradeDate
	}
	return strings.ToUpper(payload.Symbol), tradeDate, nil
}

// ConcatUserMessages 拼接会话中的用户句，用于意图与标的提取
func ConcatUserMessages(msgs []Message) string {
	var b strings.Builder
	for _, m := range msgs {
		if m.Role != "user" || m.Content == "" {
			continue
		}
		if b.Len() > 0 {
			b.WriteByte('\n')
		}
		b.WriteString(strings.TrimSpace(m.Content))
	}
	return b.String()
}

func buildUserQuery(req AnalysisRequest) string {
	td := req.TradeDate
	if td == "" {
		td = todayCN()
	}
	obj := req.Objective
	if obj == "" {
		obj = "完整投研与交易风险评估"
	}
	return fmt.Sprintf(`请对 A 股标的 %s 进行完整的多智能体投研分析。交易基准日：%s。
分析目标：%s。
请按需调用数据工具，依次完成各角色分析，最后给出明确风险提示（不构成投资建议）。`,
		req.Symbol, td, obj)
}

type textAccumulator struct {
	byAgent map[string]string
}

func newAccumulator() *textAccumulator {
	return &textAccumulator{byAgent: make(map[string]string)}
}

func (a *textAccumulator) add(agentName, text string) {
	if agentName == "" || text == "" {
		return
	}
	a.byAgent[agentName] = a.byAgent[agentName] + text
}

// 与 frontend AgentCollaboration / ReportViewer 的 section key 一致（含 Python ANALYST_AGENT_NAMES）
func canonicalReportSection(agentNameCN string) string {
	m := map[string]string{
		"市场分析师":   "market_report",
		"舆情分析师":   "sentiment_report",
		"新闻分析师":   "news_report",
		"基本面分析师":  "fundamentals_report",
		"宏观分析师":   "macro_report",
		"主力资金分析师": "smart_money_report",
		"博弈论经理":   "game_theory_report",
		"多头研究员":   "investment_plan",
		"空头研究员":   "investment_plan",
		"研究经理":    "investment_plan",
		"交易员":     "trader_investment_plan",
		"激进风控":    "final_trade_decision",
		"中性风控":    "final_trade_decision",
		"保守风控":    "final_trade_decision",
		"风险法官":    "final_trade_decision",
		"风险经理":    "final_trade_decision",
	}
	if s, ok := m[agentNameCN]; ok {
		return s
	}
	return ""
}

// cnAgentNameToFrontend 将 eino Agent.Name（中文）转为前端 store / Python 使用的英文名；合成节点不对外发事件
func cnAgentNameToFrontend(cn string) (frontend string, ok bool) {
	// Parallel / Sequential 父节点（事件上的 AgentName 可能是父级，前端无对应卡片）
	skipParents := map[string]struct{}{
		"分析师团队":               {},
		"风控三方辩论":              {},
		"多空辩论":                {},
		"AIGoStock 完整交易分析工作流": {},
	}
	if _, skip := skipParents[cn]; skip {
		return "", false
	}
	m := map[string]string{
		"市场分析师":   "Market Analyst",
		"舆情分析师":   "Social Analyst",
		"新闻分析师":   "News Analyst",
		"基本面分析师":  "Fundamentals Analyst",
		"宏观分析师":   "Macro Analyst",
		"主力资金分析师": "Smart Money Analyst",
		"博弈论经理":   "Game Theory Manager",
		"多头研究员":   "Bull Researcher",
		"空头研究员":   "Bear Researcher",
		"研究经理":    "Research Manager",
		"交易员":     "Trader",
		"激进风控":    "Aggressive Analyst",
		"中性风控":    "Neutral Analyst",
		"保守风控":    "Conservative Analyst",
		"风险法官":    "Portfolio Manager",
		"风险经理":    "Portfolio Manager",
	}
	if en, hit := m[cn]; hit {
		return en, true
	}
	// 已是英文或其它未知名：原样发出，便于排查
	return cn, true
}

func emitAgentStream(ctx context.Context, mo *adk.MessageVariant, agentNameCN string, emit JobEmit, acc *textAccumulator) error {
	if mo.MessageStream == nil {
		return nil
	}
	feAgent, ok := cnAgentNameToFrontend(agentNameCN)
	if !ok {
		return nil
	}
	section := canonicalReportSection(agentNameCN)
	emit("agent.status", map[string]any{"agent": feAgent, "status": "in_progress"})

	for {
		if ctx.Err() != nil {
			return ctx.Err()
		}
		msg, err := mo.MessageStream.Recv()
		if errors.Is(err, io.EOF) {
			break
		}
		if err != nil {
			return err
		}
		if msg == nil || msg.Content == "" {
			continue
		}
		emit("agent.token", map[string]any{"agent": feAgent, "token": msg.Content, "report": section})
		acc.add(agentNameCN, msg.Content)
	}
	emit("agent.status", map[string]any{"agent": feAgent, "status": "completed"})
	return nil
}

func mapAgentEvent(ctx context.Context, ev *adk.AgentEvent, emit JobEmit, acc *textAccumulator) error {
	if ev == nil {
		return nil
	}
	if ev.Err != nil {
		return ev.Err
	}
	agentNameCN := ev.AgentName
	if agentNameCN == "" {
		return nil
	}
	if ev.Output == nil || ev.Output.MessageOutput == nil {
		return nil
	}
	mo := ev.Output.MessageOutput
	if mo.IsStreaming {
		return emitAgentStream(ctx, mo, agentNameCN, emit, acc)
	}
	msg := mo.Message
	if msg == nil || mo.Role != schema.Assistant {
		return nil
	}
	content := strings.TrimSpace(msg.Content)
	if content == "" {
		return nil
	}
	feAgent, ok := cnAgentNameToFrontend(agentNameCN)
	if !ok {
		return nil
	}
	section := canonicalReportSection(agentNameCN)
	emit("agent.status", map[string]any{"agent": feAgent, "status": "in_progress"})
	emit("agent.token", map[string]any{"agent": feAgent, "token": content + "\n\n", "report": section})
	acc.add(agentNameCN, content)
	emit("agent.status", map[string]any{"agent": feAgent, "status": "completed"})
	return nil
}

func buildResultMap(req AnalysisRequest, acc *textAccumulator) map[string]any {
	res := map[string]any{
		"symbol":              req.Symbol,
		"trade_date":          req.TradeDate,
		"company_of_interest": req.Symbol,
	}
	// 固定顺序合并同一 section，避免 range map 随机
	agentOrder := []string{
		"市场分析师", "舆情分析师", "新闻分析师", "基本面分析师", "宏观分析师", "主力资金分析师",
		"博弈论经理", "多头研究员", "空头研究员", "研究经理", "交易员",
		"激进风控", "中性风控", "保守风控", "风险法官", "风险经理",
	}
	sections := make(map[string][]string)
	// Per-agent text for frontend history hydrate (Bull/Bear share investment_plan section).
	agentReports := make(map[string]string)
	for _, agent := range agentOrder {
		text := acc.byAgent[agent]
		key := canonicalReportSection(agent)
		if key == "" || text == "" {
			continue
		}
		sections[key] = append(sections[key], text)
		if en, ok := cnAgentNameToFrontend(agent); ok && en != "" {
			if prev := agentReports[en]; prev != "" {
				agentReports[en] = prev + "\n\n" + text
			} else {
				agentReports[en] = text
			}
		}
	}
	for key, parts := range sections {
		res[key] = strings.Join(parts, "\n\n---\n\n")
	}
	if len(agentReports) > 0 {
		res["agent_reports"] = agentReports
	}
	if fd, ok := res["final_trade_decision"].(string); ok && fd != "" {
		res["final_summary"] = fd
	}
	return res
}

func inferDirectionDecision(final string) (direction string, decision string) {
	decision = resolveTradeDecisionFromText(final)
	if decision == "" {
		decision = "HOLD"
	}
	structuredDir := extractStructuredDirection(final)
	if structuredDir != "" {
		direction = structuredDir
	} else if vdir := extractVerdictDirection(final); vdir != "" {
		direction = vdir
	} else {
		direction = directionLabelForDecision(decision, "")
	}
	return direction, decision
}

func completionPayload(result map[string]any) map[string]any {
	final, _ := result["final_trade_decision"].(string)
	dir, dec := inferDirectionDecision(final)
	return map[string]any{
		"result":          result,
		"risk_items":      []any{},
		"key_metrics":     []any{},
		"confidence":      nil,
		"target_price":    nil,
		"stop_loss_price": nil,
		"direction":       dir,
		"decision":        dec,
	}
}

// RunTradingWorkflow 执行 graph.NewTradingWorkflow + adk.Runner，并通过 emit 推送事件。
//
// 传入的 ctx 应为请求级 context（无工作流超时）；本函数内部会：
//  1. 立即创建工作流 ctx（AnalysisWorkflowTimeout，默认 30m），并挂载 W6 预取句柄。
//  2. 后台 goroutine 以 W6PrefetchTimeout（默认 18m）调用 FetchW6NewsResearch，
//     期间 emit progress 事件；完成后 Complete 句柄，解除新闻分析师阻塞。
//  3. 同时启动 adk.Runner：6 名分析师并行，其中 5 名立即运行，新闻分析师在 Run 内
//     等待 W6；分析师阶段墙钟 ≈ max(W6+新闻分析, 其余 5 名)，而非 W6 串行在前。
//
// W6 预取失败不会中断流程：FetchW6NewsResearch 自身已返回中文降级 Markdown，
// 新闻分析师会据此显式标注「W6 调研失败」并继续用 get_news 补充。
func RunTradingWorkflow(ctx context.Context, jm *JobManager, jobID string, req AnalysisRequest, emit JobEmit) (err error) {
	defer func() {
		if r := recover(); r != nil {
			err = fmt.Errorf("workflow panic: %v", r)
			jm.CommitJobFailure(jobID)
			emit("job.failed", map[string]any{"error": err.Error()})
			jm.persistJobReportFailed(jobID, req, err.Error())
		}
	}()

	jm.UpdateJobStatus(jobID, JobStatusRunning)
	emit("job.running", map[string]any{"symbol": req.Symbol, "msg": "深度投研分析已启动"})

	currentDate := req.TradeDate
	if currentDate == "" {
		currentDate = todayCN()
	}

	// 工作流 ctx 立即启动；W6 预取在后台与其余分析师并行。
	wfCtx, wfCancel := context.WithTimeout(ctx, AnalysisWorkflowTimeout())
	defer wfCancel()

	var w6Prefetch *news.PrefetchHandle
	wfCtx, w6Prefetch = news.AttachPrefetch(wfCtx)

	// 等预取 goroutine 结束再返回，避免 handler 已 writeSSEDone 后仍 emit（竞态/断连 Flush）。
	var w6WG sync.WaitGroup
	w6WG.Add(1)
	go func() {
		defer w6WG.Done()
		emit("progress", map[string]any{
			"agent":   "News Analyst",
			"status":  "w6_prefetch",
			"message": "W6 联网新闻调研中（预取）…",
		})
		prefetchCtx, prefetchCancel := context.WithTimeout(ctx, tools.W6PrefetchTimeout())
		w6MD, w6Err := tools.FetchW6NewsResearch(prefetchCtx, "", req.Symbol, currentDate)
		prefetchCancel()
		if w6Err != nil {
			log.Printf("[w6] prefetch error: %v (symbol=%s)", w6Err, req.Symbol)
			w6MD = fmt.Sprintf("# W6新闻调研\n\n⚠️ W6 预取失败：%v。已降级，请改用 get_news / get_global_news 补充新闻。", w6Err)
		}
		degraded := strings.Contains(w6MD, "⚠️")
		if degraded {
			emit("progress", map[string]any{
				"agent":   "News Analyst",
				"status":  "w6_prefetch_degraded",
				"message": "W6 调研降级（预取）：" + firstLineAfterHeading(w6MD),
			})
		} else {
			emit("progress", map[string]any{
				"agent":   "News Analyst",
				"status":  "w6_prefetch_done",
				"message": "W6 调研完成（预取），新闻分析师开始撰写…",
			})
		}
		w6Prefetch.Complete(w6MD)
	}()
	defer w6WG.Wait()

	twf, err := graph.NewTradingWorkflow(wfCtx)
	if err != nil {
		jm.CommitJobFailure(jobID)
		emit("job.failed", map[string]any{"error": err.Error()})
		jm.persistJobReportFailed(jobID, req, err.Error())
		return err
	}

	runner := adk.NewRunner(wfCtx, adk.RunnerConfig{
		Agent:           twf.GetAgent(),
		EnableStreaming: true,
	})
	iter := runner.Query(wfCtx, buildUserQuery(req))
	acc := newAccumulator()

	type nextEv struct {
		ev *adk.AgentEvent
		ok bool
	}
	// 每次仅一个在途 Next，避免 goroutine 提前批读占满 channel 与 mapAgentEvent 慢处理形成死锁；超时时可能遗留一个在等 Next 的 goroutine
	for {
		nextCh := make(chan nextEv, 1)
		go func() {
			e, o := iter.Next()
			nextCh <- nextEv{e, o}
		}()
		select {
		case <-wfCtx.Done():
			msg := fmt.Sprintf("分析超时或已中断：%v。若长时间无输出，可尝试缩短标的复杂度或检查模型/数据服务可用性。", wfCtx.Err())
			jm.CommitJobFailure(jobID)
			emit("job.failed", map[string]any{"error": msg})
			jm.persistJobReportFailed(jobID, req, msg)
			return wfCtx.Err()
		case ne := <-nextCh:
			if !ne.ok {
				goto finished
			}
			if mapErr := mapAgentEvent(wfCtx, ne.ev, emit, acc); mapErr != nil {
				jm.CommitJobFailure(jobID)
				emit("job.failed", map[string]any{"error": mapErr.Error()})
				jm.persistJobReportFailed(jobID, req, mapErr.Error())
				return mapErr
			}
		}
	}

finished:
	if wfCtx.Err() != nil {
		jm.CommitJobFailure(jobID)
		emit("job.failed", map[string]any{"error": wfCtx.Err().Error()})
		jm.persistJobReportFailed(jobID, req, wfCtx.Err().Error())
		return wfCtx.Err()
	}

	result := buildResultMap(req, acc)
	payload := completionPayload(result)
	mergePayloadExtras(result, payload)
	applyPayloadToResult(result, payload)
	emit("job.completed", payload)
	jm.CommitJobResult(jobID, result)
	jm.persistJobReportCompleted(jobID, req, result, payload)
	return nil
}

// firstLineAfterHeading 从 W6 降级 Markdown 中取出 ⚠️ 那行用于 SSE 提示。
func firstLineAfterHeading(md string) string {
	for _, line := range strings.Split(md, "\n") {
		line = strings.TrimSpace(line)
		if strings.HasPrefix(line, "⚠️") {
			return line
		}
	}
	return "W6 调研失败"
}
