package news

import (
	"aistock/backend/internal/analysis/prompts"
	"context"
	"fmt"
	"strings"
	"sync"
	"time"

	"github.com/cloudwego/eino/adk"
	"github.com/cloudwego/eino/components/tool"

	"aistock/backend/internal/analysis/agents/common"
	"aistock/backend/internal/analysis/tools"
)

// ctxKey 是预取 W6 新闻 Markdown 的 context.Context 键类型。
// 通过 context 传递可避免改动 agents.Registry 的 Creator 签名，
// 同时保持 graph.NewTradingWorkflow(ctx) 一致的入口。
type ctxKey struct{}

// prefetchCtxKey 标记 ctx 中存在进行中的 W6 预取；新闻分析师 Run 时会阻塞等待。
type prefetchCtxKey struct{}

// PrefetchHandle 由 RunTradingWorkflow 创建并在后台 goroutine 中 Complete。
// 新闻分析师通过 deferredNewsAgent 在 Run 时 wait，使 W6 预取与其余 5 名分析师并行。
type PrefetchHandle struct {
	ready chan struct{}
	mu    sync.Mutex
	md    string
}

// AttachPrefetch 在 ctx 上挂载 W6 预取句柄；NewAgent 会返回阻塞型包装 agent。
func AttachPrefetch(ctx context.Context) (context.Context, *PrefetchHandle) {
	h := &PrefetchHandle{ready: make(chan struct{})}
	return context.WithValue(ctx, prefetchCtxKey{}, h), h
}

// Complete 由预取 goroutine 调用，解除新闻分析师的阻塞。
func (h *PrefetchHandle) Complete(md string) {
	h.mu.Lock()
	h.md = md
	h.mu.Unlock()
	close(h.ready)
}

func prefetchFrom(ctx context.Context) *PrefetchHandle {
	h, _ := ctx.Value(prefetchCtxKey{}).(*PrefetchHandle)
	return h
}

func (h *PrefetchHandle) wait(ctx context.Context) string {
	select {
	case <-h.ready:
		h.mu.Lock()
		md := h.md
		h.mu.Unlock()
		return md
	case <-ctx.Done():
		return fmt.Sprintf("# W6新闻调研\n\n⚠️ W6 预取失败：%v。已降级，请改用 get_news / get_global_news 补充新闻。", ctx.Err())
	}
}

func ctxWithoutPrefetch(ctx context.Context) context.Context {
	if prefetchFrom(ctx) == nil {
		return ctx
	}
	return context.WithValue(ctx, prefetchCtxKey{}, (*PrefetchHandle)(nil))
}

// W6NewsResearchToolName 是 W6 联网调研工具的工具名（与 w6_tools.go 中
// utils.InferTool 的第一个参数一致），用于在工具集中过滤掉它。
const W6NewsResearchToolName = "w6_news_research"

// WithPreFetchedW6News 返回一份携带预取 W6 新闻 Markdown 的 context。
// 调用方：RunTradingWorkflow 在 graph.NewTradingWorkflow(ctx) 之前注入，
// 以便 news.NewAgent(ctx) 在构建 instruction 时读取并嵌入。
func WithPreFetchedW6News(ctx context.Context, md string) context.Context {
	if md == "" {
		return ctx
	}
	return context.WithValue(ctx, ctxKey{}, md)
}

// preFetchedW6NewsFrom 从 ctx 中读取预取的 W6 Markdown，未注入则返回空串。
func preFetchedW6NewsFrom(ctx context.Context) string {
	v, _ := ctx.Value(ctxKey{}).(string)
	return v
}

// newsToolsWithoutW6 返回全局工具集，但移除 w6_news_research。
// 新闻分析师不再调用 W6 工具（W6 已由系统预取并嵌入 instruction），
// 因此需要从其工具集中剔除，避免模型尝试调用一个本agent不应再用的工具。
func newsToolsWithoutW6() []tool.BaseTool {
	all := tools.GetGlobalTools()
	out := make([]tool.BaseTool, 0, len(all))
	for _, t := range all {
		info, err := t.Info(context.Background())
		if err != nil || info == nil {
			out = append(out, t)
			continue
		}
		if info.Name == W6NewsResearchToolName {
			continue
		}
		out = append(out, t)
	}
	return out
}

// buildInstruction 构建新闻分析师的系统提示词：在基础 instruction 之上
// 拼接「## W6 联网调研」预取内容（若有）。
func buildInstruction(ctx context.Context) string {
	base := fmt.Sprintf("当前时间：%s\n\n%s",
		time.Now().Format("2006-01-02 15:04:05"), prompts.Resolve("news", newsAnalystInstructionBase))

	md := strings.TrimSpace(preFetchedW6NewsFrom(ctx))
	if md == "" {
		// 系统未注入 W6 预取结果：明确告知 agent 失败，要求显式标注。
		return base + "\n\n## W6 联网调研\n\n（系统未预取 W6 调研结果，请在报告中按规则显式标注「W6 调研失败：系统未预取」。）\n"
	}
	return base + "\n\n## W6 联网调研\n\n" + md + "\n"
}

const newsAgentName = "新闻分析师"
const newsAgentDesc = "专业的新闻分析专家，擅长从新闻中提取关键信息并分析影响。"

// NewAgent 创建新闻分析师 Agent。
// ctx 中可携带预取的 W6 新闻 Markdown（见 WithPreFetchedW6News），
// 或 AttachPrefetch 句柄（Run 时再注入 W6，与其余分析师并行）。
func NewAgent(ctx context.Context) (adk.Agent, error) {
	if pf := prefetchFrom(ctx); pf != nil {
		return &deferredNewsAgent{prefetch: pf, baseCtx: ctxWithoutPrefetch(ctx)}, nil
	}
	return newReadyAgent(ctx)
}

func newReadyAgent(ctx context.Context) (adk.Agent, error) {
	instruction := buildInstruction(ctx)
	return common.NewAgentBuilder(newsAgentName, newsAgentDesc).
		WithInstruction(instruction).
		WithTools(newsToolsWithoutW6()...).
		WithModel(common.NewQuickThinkModel()).
		Build(ctx)
}

// deferredNewsAgent 在 Run 时等待 W6 预取完成，再构建并运行真实新闻分析师。
type deferredNewsAgent struct {
	prefetch *PrefetchHandle
	baseCtx  context.Context
}

func (a *deferredNewsAgent) Name(context.Context) string        { return newsAgentName }
func (a *deferredNewsAgent) Description(context.Context) string { return newsAgentDesc }

func (a *deferredNewsAgent) Run(ctx context.Context, input *adk.AgentInput, opts ...adk.AgentRunOption) *adk.AsyncIterator[*adk.AgentEvent] {
	iter, gen := adk.NewAsyncIteratorPair[*adk.AgentEvent]()
	go func() {
		defer gen.Close()
		md := a.prefetch.wait(ctx)
		innerCtx := WithPreFetchedW6News(a.baseCtx, md)
		agent, err := newReadyAgent(innerCtx)
		if err != nil {
			gen.Send(&adk.AgentEvent{AgentName: newsAgentName, Err: err})
			return
		}
		innerIter := agent.Run(ctx, input, opts...)
		for {
			ev, ok := innerIter.Next()
			if !ok {
				return
			}
			gen.Send(ev)
		}
	}()
	return iter
}
