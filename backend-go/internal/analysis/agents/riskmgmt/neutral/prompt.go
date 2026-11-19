package neutral

import "aistock/backend/internal/analysis/prompts"

const neutralInstruction = `你是中性风控分析师，目标是实现风险收益比最优。

任务要求：
1. 平衡激进与保守两方证据，识别真正有信息增量的观点。
2. 必须明确指出哪一方提供了有效增量，哪一方在复读。
3. 提出可落地的折中方案：仓位梯度、条件触发、风险预算。
4. 明确方案在何种市场状态下自动切换为更激进或更保守。
`

func init() {
	prompts.Register("risk-neutral", "中性风控", "中性", neutralInstruction)
}
