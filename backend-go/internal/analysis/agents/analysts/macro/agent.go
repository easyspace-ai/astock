package macro

import (
	"aistock/backend/internal/analysis/agents/common"
	"aistock/backend/internal/analysis/prompts"
	"aistock/backend/internal/analysis/tools"
	"context"
	"fmt"
	"time"

	"github.com/cloudwego/eino/adk"
)

// NewAgent 创建宏观分析师 Agent
func NewAgent(ctx context.Context) (adk.Agent, error) {
	instruction := fmt.Sprintf("当前时间：%s\n\n%s", time.Now().Format("2006-01-02 15:04:05"), prompts.Resolve("macro", macroAnalystInstruction))
	return common.NewAgentBuilder("宏观分析师", "专业的宏观经济分析专家，擅长从政策和经济大环境角度分析。").
		WithInstruction(instruction).
		WithTools(tools.GetGlobalTools()...).
		WithModel(common.NewQuickThinkModel()).
		Build(ctx)
}
