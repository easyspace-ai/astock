package sentiment

import (
	"aistock/backend/internal/analysis/agents/common"
	"aistock/backend/internal/analysis/prompts"
	"aistock/backend/internal/analysis/tools"
	"context"
	"fmt"
	"time"

	"github.com/cloudwego/eino/adk"
)

// NewAgent 创建舆情分析师 Agent
func NewAgent(ctx context.Context) (adk.Agent, error) {
	instruction := fmt.Sprintf("当前时间：%s\n\n%s", time.Now().Format("2006-01-02 15:04:05"), prompts.Resolve("sentiment", sentimentAnalystInstruction))
	return common.NewAgentBuilder("舆情分析师", "专业的市场情绪分析专家，擅长分析投资者心理和资金流向。").
		WithInstruction(instruction).
		WithTools(tools.GetGlobalTools()...).
		WithModel(common.NewQuickThinkModel()).
		Build(ctx)
}
