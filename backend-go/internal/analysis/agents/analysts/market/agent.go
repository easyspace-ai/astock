package market

import (
	"aistock/backend/internal/analysis/agents/common"
	"aistock/backend/internal/analysis/prompts"
	"aistock/backend/internal/analysis/tools"
	"context"
	"fmt"
	"time"

	"github.com/cloudwego/eino/adk"
)

// NewAgent 创建市场分析师 Agent
func NewAgent(ctx context.Context) (adk.Agent, error) {
	instruction := fmt.Sprintf("当前时间：%s\n\n%s", time.Now().Format("2006-01-02 15:04:05"), prompts.Resolve("market", marketAnalystInstruction))
	return common.NewAgentBuilder("市场分析师", "专业的技术面分析专家，擅长 K 线、技术指标和趋势判断。").
		WithInstruction(instruction).
		WithTools(tools.GetGlobalTools()...).
		WithModel(common.NewQuickThinkModel()).
		Build(ctx)
}
