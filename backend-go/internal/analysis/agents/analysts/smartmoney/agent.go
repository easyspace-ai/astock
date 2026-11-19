package smartmoney

import (
	"aistock/backend/internal/analysis/agents/common"
	"aistock/backend/internal/analysis/prompts"
	"aistock/backend/internal/analysis/tools"
	"context"
	"fmt"
	"time"

	"github.com/cloudwego/eino/adk"
)

// NewAgent 创建主力资金分析师 Agent
func NewAgent(ctx context.Context) (adk.Agent, error) {
	instruction := fmt.Sprintf("当前时间：%s\n\n%s", time.Now().Format("2006-01-02 15:04:05"), prompts.Resolve("smart-money", smartMoneyAnalystInstruction))
	return common.NewAgentBuilder("主力资金分析师", "专业的资金流向分析专家，擅长追踪机构动向和聪明钱行为。").
		WithInstruction(instruction).
		WithTools(tools.GetGlobalTools()...).
		WithModel(common.NewQuickThinkModel()).
		Build(ctx)
}
