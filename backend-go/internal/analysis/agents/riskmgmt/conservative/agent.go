package conservative

import (
	"aistock/backend/internal/analysis/agents/common"
	"aistock/backend/internal/analysis/prompts"
	"context"

	"github.com/cloudwego/eino/adk"
)

// NewAgent 创建保守风控辩论师 Agent
func NewAgent(ctx context.Context) (adk.Agent, error) {
	return common.NewAgentBuilder("保守风控", "从谨慎角度评估风险，强调本金安全，建议严格风控。").
		WithInstruction(prompts.Resolve("risk-conservative", conservativeInstruction)).
		WithModel(common.NewDeepThinkModel()).
		Build(ctx)
}
