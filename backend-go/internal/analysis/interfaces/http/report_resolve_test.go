package httpapi

import (
	"strings"
	"testing"
)

func TestExtractRiskItemsFromText(t *testing.T) {
	text := `【决策逻辑】
1. 核心逻辑：业绩稳健
2. 催化剂：新品发布
3. 主要风险：估值偏高，宏观下行

【风险提示】
- 行业竞争加剧
- 政策不确定性
`
	items := extractRiskItemsFromText(text)
	if len(items) < 2 {
		t.Fatalf("expected at least 2 risk items, got %d", len(items))
	}
}

func TestExtractKeyMetricsFromText(t *testing.T) {
	text := `基本面概览
PE：28.5
PB：4.2
| 指标 | 值 |
| 换手率 | 1.2% |
置信度：75%
`
	items := extractKeyMetricsFromText(text)
	if len(items) < 3 {
		t.Fatalf("expected at least 3 metrics, got %d", len(items))
	}
}

func TestExtractVerdictDirectionSingleQuotedJSON(t *testing.T) {
	text := `结论如下
<!-- VERDICT: {'direction': '看多', 'reason': '业绩改善'} -->
`
	got := extractVerdictDirection(text)
	if got != "看多" {
		t.Fatalf("expected 看多, got %q", got)
	}
}

func TestExtractTargetPriceRejectsZero(t *testing.T) {
	text := `【交易计划】
目标价: 0
止损价: 1149
`
	if p := extractTargetPriceRegex(text); p != nil {
		t.Fatalf("expected nil target for zero price, got %v", *p)
	}
	if p := extractStopLossRegex(text); p == nil || *p != 1149 {
		t.Fatalf("expected stop 1149, got %v", p)
	}
}

func TestVerdictDecisionHoldCases(t *testing.T) {
	for _, dir := range []string{"观望", "持有", "空仓", "中性", "谨慎"} {
		if got := verdictDecision(dir); got != "HOLD" {
			t.Fatalf("direction %q: want HOLD, got %q", dir, got)
		}
	}
}

func TestExtractExplicitTradeDecision(t *testing.T) {
	text := `正文若干
最终交易建议：观望（对应 HOLD）
目标价: 不适用
`
	if got := extractExplicitTradeDecision(text); got != "HOLD" {
		t.Fatalf("want HOLD, got %q", got)
	}
}

func TestResolveTradeDecision300757Like(t *testing.T) {
	text := `【最终决策】
- 标的：300757.SZ
- 方向：观望
- 置信度：中
- 建议仓位：0%（严禁新开仓）

【交易计划】
- 入场点位：无
- 止损点位：不适用
- 止盈点位：不适用
- 持有周期：空仓观望

<!-- VERDICT: {"direction": "偏多", "reason": "技术反弹"} -->
目标价: 430
止损价: 460
`
	dec := resolveTradeDecisionFromText(text)
	if dec != "HOLD" {
		t.Fatalf("want HOLD, got %q", dec)
	}
	dir := extractStructuredDirection(text)
	if dir != "观望" {
		t.Fatalf("want 观望 direction, got %q", dir)
	}
}

func TestMergePayloadExtrasHoldIgnoresTrailingPrices(t *testing.T) {
	result := map[string]any{
		"final_trade_decision": `【最终决策】
- 方向：观望
- 建议仓位：0%（严禁新开仓）
【交易计划】
- 入场点位：无
目标价: 430
止损价: 460
`,
	}
	payload := completionPayload(result)
	mergePayloadExtras(result, payload)

	if got := stringFromAny(payload["decision"]); got != "HOLD" {
		t.Fatalf("want HOLD decision, got %q", got)
	}
	if payload["target_price"] != nil {
		t.Fatalf("expected nil target_price for HOLD, got %v", payload["target_price"])
	}
	if payload["stop_loss_price"] != nil {
		t.Fatalf("expected nil stop_loss_price for HOLD, got %v", payload["stop_loss_price"])
	}
}

func TestMergePayloadExtrasKeepsExistingPricesOnSecondPass(t *testing.T) {
	result := map[string]any{
		"final_trade_decision": `【最终决策】
- 方向：看多
- 置信度：中
【交易计划】
目标价: 8.00
止损价: 7.48
`,
	}
	payload := completionPayload(result)
	// 首次 merge 抽取价位。
	mergePayloadExtras(result, payload)
	if payload["target_price"] == nil || payload["stop_loss_price"] == nil {
		t.Fatalf("expected prices after first pass, got target=%v stop=%v", payload["target_price"], payload["stop_loss_price"])
	}
	// 二次 merge（持久化路径会再调一次）不得把已有有效价位清空。
	mergePayloadExtras(result, payload)
	if payload["target_price"] == nil {
		t.Fatal("target_price was reset to nil on second merge")
	}
	if payload["stop_loss_price"] == nil {
		t.Fatal("stop_loss_price was reset to nil on second merge")
	}
}

func TestValidateTradePricesBuyRejectsInverted(t *testing.T) {
	target := 430.0
	stop := 460.0
	tPtr, sPtr := validateTradePrices("BUY", &target, &stop)
	if tPtr != nil || sPtr != nil {
		t.Fatalf("expected nil prices for invalid BUY pair, got target=%v stop=%v", tPtr, sPtr)
	}
}

func TestValidateTradePricesBuyAcceptsValid(t *testing.T) {
	target := 1215.0
	stop := 1149.0
	tPtr, sPtr := validateTradePrices("BUY", &target, &stop)
	if tPtr == nil || sPtr == nil || *tPtr != 1215 || *sPtr != 1149 {
		t.Fatalf("expected valid BUY prices, got target=%v stop=%v", tPtr, sPtr)
	}
}

func TestResolveDecisionPrefersFinalBlock(t *testing.T) {
	text := `# 激进风控分析师
**方向：偏多 / 分批建仓，优于观望。**

【最终决策】
- 标的：600901.SH
- 方向：观望
- 置信度：中
- 建议仓位：0%（当前不新增）

【决策逻辑】
3. 主要风险：① 股价贴近前高，短线乖离偏大；② 融资降、融券升，资金方向背离；③ 低换手滑点；④ 无一致预期，估值锚缺失；⑤ 行业盈利承压。

【交易计划】
- 止损点位：收盘 < 7.42 清仓

目标价: 不适用
止损价: 不适用
`
	if dec := resolveTradeDecisionFromText(text); dec != "HOLD" {
		t.Fatalf("want HOLD (final block 观望), got %q", dec)
	}
	if dir := extractStructuredDirection(text); dir != "观望" {
		t.Fatalf("want 观望 direction, got %q", dir)
	}
	conf := extractConfidenceRegex(text)
	if conf == nil || *conf != 60 {
		t.Fatalf("want confidence 60 (中), got %v", conf)
	}
	risks := extractRiskItemsFromText(text)
	if len(risks) < 4 {
		t.Fatalf("want >=4 split risk items, got %d: %v", len(risks), risks)
	}
	for _, item := range risks {
		name := item["name"].(string)
		if strings.HasPrefix(name, "①") || strings.Contains(name, "；②") {
			t.Fatalf("risk item not split: %q", name)
		}
	}
}

func TestExtractKeyMetricsOverviewLine(t *testing.T) {
	text := `**交易基准日：2026-11-18 | 现价 7.81 元 | PE(TTM) 12.96 | PB 1.69 | 总市值 452 亿 | 换手 0.66%**`
	metrics := extractKeyMetricsFromText(text)
	byName := map[string]string{}
	for _, m := range metrics {
		byName[m["name"].(string)] = m["value"].(string)
	}
	if byName["市盈率(PE)"] != "12.96" {
		t.Fatalf("PE not parsed: %v", byName)
	}
	if byName["市净率(PB)"] != "1.69" {
		t.Fatalf("PB not parsed: %v", byName)
	}
	if byName["换手率"] != "0.66%" {
		t.Fatalf("turnover not parsed: %v", byName)
	}
	if _, ok := byName["现价 7.81 元"]; ok {
		t.Fatalf("overview row must not produce misaligned metric: %v", byName)
	}
}

func TestMergePayloadExtrasFillsRisksAndMetrics(t *testing.T) {
	result := map[string]any{
		"final_trade_decision": `3. 主要风险：流动性不足
【风险提示】
- 业绩不及预期`,
		"fundamentals_report": "PE：15.3\nPB：2.1",
	}
	payload := completionPayload(result)
	mergePayloadExtras(result, payload)

	risks, ok := payload["risk_items"].([]any)
	if !ok || len(risks) == 0 {
		t.Fatal("expected risk_items to be populated")
	}
	metrics, ok := payload["key_metrics"].([]any)
	if !ok || len(metrics) == 0 {
		t.Fatal("expected key_metrics to be populated")
	}

	// 落库的是 result，payload 中的风险/指标/置信度必须回写到 result。
	applyPayloadToResult(result, payload)
	if _, ok := result["risk_items"].([]any); !ok {
		t.Fatal("expected result.risk_items to be copied from payload")
	}
	if _, ok := result["key_metrics"].([]any); !ok {
		t.Fatal("expected result.key_metrics to be copied from payload")
	}
}
