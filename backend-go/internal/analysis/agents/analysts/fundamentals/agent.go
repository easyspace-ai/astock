package fundamentals

import (
	"aistock/backend/internal/analysis/agents/common"
	"aistock/backend/internal/analysis/prompts"
	"aistock/backend/internal/analysis/tools"
	"context"
	"fmt"
	"time"

	"github.com/cloudwego/eino/adk"
)

// NewAgent 创建基本面分析师 Agent
func NewAgent(ctx context.Context) (adk.Agent, error) {
	instruction := fmt.Sprintf("当前时间：%s\n\n%s", time.Now().Format("2006-01-02 15:04:05"), prompts.Resolve("fundamentals", fundamentalsAnalystInstruction))
	return common.NewAgentBuilder("基本面分析师", "专业的财务分析专家，擅长从财报和商业模式角度评估公司价值。").
		WithInstruction(instruction).
		WithTools(tools.GetGlobalTools()...).
		WithModel(common.NewQuickThinkModel()).
		Build(ctx)
}
