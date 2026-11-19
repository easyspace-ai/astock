package tools

import (
	"sync"

	"github.com/cloudwego/eino/components/tool"
)

var (
	globalTools     []tool.BaseTool
	globalToolsOnce sync.Once
)

// InitGlobalTools 初始化全局工具集
func InitGlobalTools(dataDir string) error {
	var err error
	globalToolsOnce.Do(func() {
		stockTools, initErr := NewStockTools(dataDir)
		if initErr != nil {
			err = initErr
			return
		}
		allTools := stockTools.GetAllTools()

		// 添加东财工具
		eastmoneyTools := NewEastMoneyTools()
		allTools = append(allTools, eastmoneyTools.GetAllTools()...)

		// 添加同花顺工具
		thstools := NewTHSTools()
		allTools = append(allTools, thstools.GetAllTools()...)

		// 注意：W6 联网新闻调研已从全局工具集中移除。W6 调用现在由
		// RunTradingWorkflow 在工作流启动前预取（tools.FetchW6NewsResearch），
		// 结果通过 news.WithPreFetchedW6News 注入新闻分析师的 instruction。
		// 因此任何 agent 都不再看到 w6_news_research 工具，避免重复调用与
		// 工作流预算被 W6 的 ~12m 调用吃掉。
		// NewW6NewsResearchTool() 仍保留在 w6_tools.go 中（供测试与未来复用）。

		// 防御性归一化：去除每个工具参数 JSON schema 中可能出现的重复
		// `required` 条目（OpenAI/DeepSeek 在绑定工具时会校验 schema，
		// required 数组存在重复元素会整请求 400，导致所有分析失败）。
		// 归一化对工具执行逻辑无影响，且幂等。
		for i, t := range allTools {
			allTools[i] = NormalizeToolSchema(t)
			// 每个 agent 绑定时都拿到独立的 schema 副本，避免并发绑定互相踩坏。
			allTools[i] = WithIsolatedSchema(allTools[i])
		}

		globalTools = allTools
	})
	return err
}

// GetGlobalTools 获取全局工具集
func GetGlobalTools() []tool.BaseTool {
	return globalTools
}
