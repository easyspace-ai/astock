package httpapi

import (
	"encoding/json"
	"fmt"
	"log"
	"regexp"
	"strconv"
	"strings"
)

var (
	reConfidenceZH       = regexp.MustCompile(`(?i)置信度[:：]\s*(\d+)%`)
	reConfidenceEN       = regexp.MustCompile(`(?i)confidence[:：]\s*(\d+)%`)
	reConfidenceWord     = regexp.MustCompile(`(?i)置信度[:：]\s*(高|中|低)`)
	reTargetPrice        = regexp.MustCompile(`(?i)(?:目标价(?:格|点位)?|止盈(?:价|点位?|位)|target\s*price)[:：\s]*[¥$]?\s*(\d+\.?\d*)\s*元?(?:/股)?`)
	reStopLoss           = regexp.MustCompile(`(?i)(?:止损价(?:格)?|止损点位|止损位|stop[-\s_]?loss)[:：\s]*[¥$]?\s*(\d+\.?\d*)\s*元?(?:/股)?`)
	reVerdict            = regexp.MustCompile(`(?is)<!--\s*VERDICT:\s*(\{.*?\})\s*-->`)
	reVerdictDir         = regexp.MustCompile(`(?i)['"]direction['"]\s*:\s*['"]([^'"]+)['"]`)
	reInlineRisk         = regexp.MustCompile(`(?:^|\n)\s*(?:\d+\.\s*)?主要风险[:：]\s*([^\n]+)`)
	reRiskNotice         = regexp.MustCompile(`(?s)【风险提示】\s*(.*?)(?:\n【|$)`)
	reStructuredDirection = regexp.MustCompile(`方向[:：]\s*[*_]*\s*(看多|看空|观望|持有|空仓|中性|谨慎|偏多|偏空)`)
	reZeroPosition       = regexp.MustCompile(`建议仓位[:：]\s*0\s*[%％]`)
	reNoEntryPoint       = regexp.MustCompile(`入场(?:点位)?[:：]\s*无`)
)

// confidenceWordValue maps the qualitative prompt rating to a numeric 0–100 score.
func confidenceWordValue(w string) *int {
	switch w {
	case "高":
		v := 80
		return &v
	case "中":
		v := 60
		return &v
	case "低":
		v := 40
		return &v
	}
	return nil
}

func extractConfidenceRegex(text string) *int {
	if text == "" {
		return nil
	}
	for _, src := range []string{finalDecisionBlock(text), text} {
		if src == "" {
			continue
		}
		for _, re := range []*regexp.Regexp{reConfidenceZH, reConfidenceEN} {
			m := re.FindStringSubmatch(src)
			if len(m) > 1 {
				v, err := strconv.Atoi(m[1])
				if err == nil && v >= 0 && v <= 100 {
					return &v
				}
			}
		}
		if m := reConfidenceWord.FindStringSubmatch(src); len(m) > 1 {
			if v := confidenceWordValue(m[1]); v != nil {
				return v
			}
		}
	}
	return nil
}

func isValidTradePrice(f float64) bool {
	return f > 0
}

func extractTargetPriceRegex(text string) *float64 {
	if text == "" {
		return nil
	}
	m := reTargetPrice.FindStringSubmatch(text)
	if len(m) > 1 {
		f, err := strconv.ParseFloat(m[1], 64)
		if err == nil && isValidTradePrice(f) {
			return &f
		}
	}
	return nil
}

func extractStopLossRegex(text string) *float64 {
	if text == "" {
		return nil
	}
	m := reStopLoss.FindStringSubmatch(text)
	if len(m) > 1 {
		f, err := strconv.ParseFloat(m[1], 64)
		if err == nil && isValidTradePrice(f) {
			return &f
		}
	}
	return nil
}

// extractVerdictDirection mirrors Python _extract_verdict → direction string for reports.direction.
func extractVerdictDirection(text string) string {
	if text == "" {
		return ""
	}
	m := reVerdict.FindStringSubmatch(text)
	if len(m) < 2 {
		return ""
	}
	raw := strings.TrimSpace(strings.ReplaceAll(strings.ReplaceAll(m[1], "\n", " "), "\r", " "))
	var payload struct {
		Direction string `json:"direction"`
	}
	if err := json.Unmarshal([]byte(raw), &payload); err != nil {
		if dir := extractDirectionFromMalformedVerdict(raw); dir != "" {
			log.Printf("[report] VERDICT JSON repaired direction=%q snippet=%q", dir, truncateForLog(raw, 120))
			return dir
		}
		log.Printf("[report] VERDICT JSON parse failed: %v snippet=%q", err, truncateForLog(raw, 120))
		return ""
	}
	if strings.TrimSpace(payload.Direction) == "" {
		return ""
	}
	return strings.TrimSpace(payload.Direction)
}

func extractDirectionFromMalformedVerdict(raw string) string {
	if m := reVerdictDir.FindStringSubmatch(raw); len(m) > 1 {
		return strings.TrimSpace(m[1])
	}
	return ""
}

func truncateForLog(s string, max int) string {
	if len(s) <= max {
		return s
	}
	return s[:max] + "..."
}

// verdictDecision maps a VERDICT direction string to a buy/sell/hold decision.
// 看多/偏多 → BUY ; 看空/偏空 → SELL ; 中性/谨慎/观望/持有/空仓 → HOLD.
func verdictDecision(direction string) string {
	d := strings.TrimSpace(direction)
	switch d {
	case "看多", "偏多":
		return "BUY"
	case "看空", "偏空":
		return "SELL"
	case "中性", "谨慎", "观望", "持有", "空仓":
		return "HOLD"
	default:
		return "HOLD"
	}
}

func isHoldDirectionWord(dir string) bool {
	switch strings.TrimSpace(dir) {
	case "观望", "持有", "空仓", "中性", "谨慎":
		return true
	default:
		return false
	}
}

// finalDecisionBlock 截取最后一个【最终决策】起至文末的全部内容。
// 其后的【决策逻辑】【交易计划】【风险提示】都属于最终裁决；前文激进/中性/
// 保守角色的方向与价位不应污染最终结论。
func finalDecisionBlock(text string) string {
	if text == "" {
		return ""
	}
	idx := strings.LastIndex(text, "【最终决策】")
	if idx < 0 {
		return text
	}
	return text[idx:]
}

// extractStructuredDirection reads 方向： from the 【最终决策】 block first,
// falling back to the whole text so older prompts without the block still parse.
func extractStructuredDirection(text string) string {
	if text == "" {
		return ""
	}
	if block := finalDecisionBlock(text); block != "" {
		if m := reStructuredDirection.FindStringSubmatch(block); len(m) > 1 {
			return strings.TrimSpace(m[1])
		}
	}
	if m := reStructuredDirection.FindStringSubmatch(text); len(m) > 1 {
		return strings.TrimSpace(m[1])
	}
	return ""
}

// extractStructuredHoldDecision detects non-actionable reports (观望 + 0%仓位 / 无入场).
func extractStructuredHoldDecision(text string) bool {
	if text == "" {
		return false
	}
	dir := extractStructuredDirection(text)
	if dir == "" || !isHoldDirectionWord(dir) {
		return false
	}
	if reZeroPosition.MatchString(text) || reNoEntryPoint.MatchString(text) ||
		strings.Contains(text, "严禁新开仓") || strings.Contains(text, "空仓观望") {
		return true
	}
	return dir == "观望" && strings.Contains(text, "0%")
}

// extractStructuredTradeDecision maps structured 【最终决策】 fields to BUY/SELL/HOLD.
func extractStructuredTradeDecision(text string) string {
	if extractStructuredHoldDecision(text) {
		return "HOLD"
	}
	dir := extractStructuredDirection(text)
	if dir == "" {
		return ""
	}
	switch dir {
	case "看多", "偏多":
		return "BUY"
	case "看空", "偏空":
		return "SELL"
	case "观望", "持有", "空仓", "中性", "谨慎":
		return "HOLD"
	default:
		return ""
	}
}

// extractExplicitTradeDecision reads the trader/risk closing line when present.
func extractExplicitTradeDecision(text string) string {
	if text == "" {
		return ""
	}
	lines := strings.Split(text, "\n")
	for i := len(lines) - 1; i >= 0; i-- {
		line := strings.TrimSpace(lines[i])
		if line == "" {
			continue
		}
		upper := strings.ToUpper(line)
		if strings.Contains(line, "最终交易建议") || strings.Contains(line, "【最终决策】") ||
			strings.Contains(upper, "FINAL TRANSACTION PROPOSAL") {
			switch {
			case strings.Contains(line, "观望") || strings.Contains(upper, "HOLD") || strings.Contains(line, "持有") || strings.Contains(line, "空仓"):
				return "HOLD"
			case strings.Contains(line, "卖出") || strings.Contains(upper, "SELL") || strings.Contains(line, "减持"):
				return "SELL"
			case strings.Contains(line, "买入") || strings.Contains(upper, "BUY") || strings.Contains(line, "增持"):
				return "BUY"
			}
		}
	}
	return ""
}

// resolveTradeDecisionFromText picks decision with structured block > closing line > VERDICT > fuzzy.
// The 【最终决策】 block wins over any earlier opinion paragraph in the same report.
func resolveTradeDecisionFromText(text string) string {
	if text == "" {
		return ""
	}
	block := finalDecisionBlock(text)
	for _, src := range []string{block, text} {
		if src == "" {
			continue
		}
		if dec := extractStructuredTradeDecision(src); dec != "" {
			return dec
		}
		if dec := extractExplicitTradeDecision(src); dec != "" {
			return dec
		}
		if vdir := extractVerdictDirection(src); vdir != "" {
			return verdictDecision(vdir)
		}
	}
	return inferFuzzyTradeDecision(text)
}

func inferFuzzyTradeDecision(text string) string {
	if text == "" {
		return "HOLD"
	}
	upper := strings.ToUpper(text)
	switch {
	case strings.Contains(text, "观望") || strings.Contains(text, "持有") || strings.Contains(text, "空仓") ||
		strings.Contains(text, "中性") || strings.Contains(text, "谨慎") || strings.Contains(upper, "HOLD"):
		return "HOLD"
	case strings.Contains(upper, "SELL") || strings.Contains(text, "卖出") || strings.Contains(text, "减持"):
		return "SELL"
	case strings.Contains(upper, "BUY") || strings.Contains(text, "买入") || strings.Contains(text, "增持"):
		return "BUY"
	default:
		return "HOLD"
	}
}

func directionLabelForDecision(decision, structuredDir string) string {
	if structuredDir != "" {
		return structuredDir
	}
	switch decision {
	case "BUY":
		return "偏多"
	case "SELL":
		return "偏空"
	default:
		return "中性"
	}
}

func validateTradePrices(decision string, target, stop *float64) (*float64, *float64) {
	if decision == "HOLD" {
		return nil, nil
	}
	if target == nil || stop == nil {
		return target, stop
	}
	switch decision {
	case "BUY":
		if *target <= *stop {
			return nil, nil
		}
	case "SELL":
		if *target >= *stop {
			return nil, nil
		}
	}
	return target, stop
}

func stringFromAny(v any) string {
	if v == nil {
		return ""
	}
	switch t := v.(type) {
	case string:
		return t
	default:
		return fmt.Sprint(t)
	}
}

func isEmptyPayloadSlice(v any) bool {
	if v == nil {
		return true
	}
	switch arr := v.(type) {
	case []any:
		return len(arr) == 0
	case []map[string]any:
		return len(arr) == 0
	default:
		return false
	}
}

var reRiskBullet = regexp.MustCompile(`[①②③④⑤⑥⑦⑧⑨⑩]`)

// splitRiskSegments splits a risk line on semicolons / circled-number bullets,
// stripping leading numbering so a single ①…；②… line becomes separate items.
func splitRiskSegments(line string) []string {
	line = strings.TrimSpace(line)
	if line == "" {
		return nil
	}
	var semis []string
	for _, s := range strings.Split(line, "；") {
		semis = append(semis, strings.Split(s, ";")...)
	}
	out := make([]string, 0, len(semis))
	for _, s := range semis {
		for _, p := range reRiskBullet.Split(s, -1) {
			p = strings.TrimSpace(strings.TrimLeft(p, "①②③④⑤⑥⑦⑧⑨⑩-•*·、 "))
			if p != "" {
				out = append(out, p)
			}
		}
	}
	return out
}

// normalizeRiskName strips leading bullets, circled numbers, "N." / "N、"
// numbering, and colons/punctuation so the same risk does not duplicate under
// different prefixes.
func normalizeRiskName(name string) string {
	name = strings.TrimSpace(name)
	for {
		name = strings.TrimLeft(name, "-•*·、 ")
		if i := strings.IndexAny(name, ".、"); i > 0 && i <= 2 {
			if n, err := strconv.Atoi(name[:i]); err == nil && n >= 1 && n <= 10 {
				name = strings.TrimSpace(name[i+1:])
				continue
			}
		}
		break
	}
	return strings.TrimSpace(strings.Trim(name, "：:，。；;、-•*·【】[] "))
}

// runeLen counts characters, avoiding byte-length miscounts for Chinese text.
func runeLen(s string) int {
	return len([]rune(s))
}

func inferRiskLevel(text string) string {
	t := strings.ToLower(text)
	if strings.Contains(t, "高风险") || strings.Contains(t, "重大风险") || strings.Contains(t, "严重") {
		return "high"
	}
	if strings.Contains(t, "低风险") || strings.Contains(t, "可控") || strings.Contains(t, "轻微") {
		return "low"
	}
	return "medium"
}

func extractRiskItemsFromText(text string) []map[string]any {
	if strings.TrimSpace(text) == "" {
		return nil
	}
	headers := []string{"【主要风险】", "【风险提示】", "主要风险", "风险提示", "核心风险"}
	items := make([]map[string]any, 0, 4)
	seen := make(map[string]struct{})

	isNoiseRisk := func(name string) bool {
		if strings.Contains(name, "目标价") || strings.Contains(name, "止损价") || strings.Contains(name, "现价") {
			return true
		}
		for _, boiler := range []string{"请务必独立判断", "投资有风险", "严格执行止损纪律", "仅供参考", "不构成投资建议"} {
			if strings.Contains(name, boiler) {
				return true
			}
		}
		if strings.Trim(name, "：:，。；;、-•*·【】[] ") == "" {
			return true
		}
		switch strings.Trim(name, "：:，。；;、-•*·【】[] ") {
		case "主要风险", "风险提示", "核心风险", "风险":
			return true
		}
		return false
	}

	addItem := func(name string) {
		name = normalizeRiskName(name)
		if name == "" || len(name) < 3 || isNoiseRisk(name) {
			return
		}
		if _, ok := seen[name]; ok {
			return
		}
		seen[name] = struct{}{}
		items = append(items, map[string]any{
			"name":  name,
			"level": inferRiskLevel(name),
		})
	}

	collectFrom := func(src string) {
		for _, header := range headers {
			idx := strings.Index(src, header)
			if idx < 0 {
				continue
			}
			section := src[idx+len(header):]
			if next := strings.Index(section, "【"); next > 0 {
				section = section[:next]
			}
			for _, line := range strings.Split(section, "\n") {
				line = strings.TrimSpace(line)
				if line == "" {
					continue
				}
				for _, seg := range splitRiskSegments(line) {
					if strings.HasPrefix(seg, "-") || strings.HasPrefix(seg, "•") || strings.HasPrefix(seg, "*") ||
						strings.Contains(seg, "风险") || runeLen(seg) <= 40 {
						addItem(seg)
					}
				}
			}
		}
	}

	// 最终【最终决策】块优先；块内无实质风险时再回退全文，避免被前文角色意见带偏。
	collectFrom(finalDecisionBlock(text))
	if len(items) == 0 {
		collectFrom(text)
	}

	if len(items) == 0 {
		for _, line := range strings.Split(text, "\n") {
			line = strings.TrimSpace(line)
			if strings.Contains(line, "风险") && (strings.HasPrefix(line, "-") || strings.HasPrefix(line, "•") || strings.HasPrefix(line, "*")) {
				addItem(line)
			}
		}
	}

	inlineSource := finalDecisionBlock(text)
	if len(items) == 0 {
		inlineSource = text
	}
	if m := reInlineRisk.FindStringSubmatch(inlineSource); len(m) > 1 {
		for _, seg := range splitRiskSegments(m[1]) {
			addItem(seg)
		}
	}

	if m := reRiskNotice.FindStringSubmatch(inlineSource); len(m) > 1 {
		for _, line := range strings.Split(m[1], "\n") {
			line = strings.TrimSpace(line)
			if strings.HasPrefix(line, "-") || strings.HasPrefix(line, "•") || strings.HasPrefix(line, "*") {
				addItem(line)
			}
		}
	}

	if len(items) > 6 {
		items = items[:6]
	}
	return items
}

func extractRiskItems(result map[string]any) []any {
	if result == nil {
		return nil
	}
	// 最终决策块优先；块内有风险结论时，不再回退多空辩论/新闻等前段文本，避免标题混入。
	items := extractRiskItemsFromText(stringFromAny(result["final_trade_decision"]))
	if len(items) == 0 {
		others := []string{
			stringFromAny(result["investment_plan"]),
			stringFromAny(result["trader_investment_plan"]),
			stringFromAny(result["news_report"]),
			stringFromAny(result["sentiment_report"]),
		}
		items = extractRiskItemsFromText(strings.Join(others, "\n"))
	}
	if len(items) == 0 {
		return nil
	}
	out := make([]any, 0, len(items))
	for _, item := range items {
		out = append(out, item)
	}
	return out
}

var metricPatterns = []struct {
	name string
	re   *regexp.Regexp
}{
	{"市盈率(PE)", regexp.MustCompile(`(?i)(?:市盈率|PE(?:\([^)]*\))?)[:：\s]*([0-9.,]+(?:%|倍)?)`)},
	{"市净率(PB)", regexp.MustCompile(`(?i)(?:市净率|PB(?:\([^)]*\))?)[:：\s]*([0-9.,]+(?:%|倍)?)`)},
	{"总市值", regexp.MustCompile(`总市值[:：\s]*([0-9.,]+(?:亿|万|元)?)`)},
	{"换手率", regexp.MustCompile(`(?:换手率|换手)[:：\s]*([0-9.,]+%?)`)},
	{"ROE", regexp.MustCompile(`(?i)ROE[:：\s]*([0-9.,]+%?)`)},
	{"RSI", regexp.MustCompile(`(?i)RSI[:：\s]*([0-9.,]+)`)},
	{"置信度", regexp.MustCompile(`置信度[:：\s]*((?:高|中|低)|\d+%?)`)},
	{"建议仓位", regexp.MustCompile(`建议仓位[:：\s]*([^\n|]+)`)},
	{"涨跌幅", regexp.MustCompile(`涨跌幅[:：\s]*([+-]?[0-9.,]+%?)`)},
}

func inferMetricStatus(name, value string) string {
	v := strings.TrimSpace(value)
	if v == "" {
		return "neutral"
	}
	if strings.Contains(name, "RSI") {
		if f, err := strconv.ParseFloat(strings.TrimSuffix(v, "%"), 64); err == nil {
			if f >= 70 {
				return "bad"
			}
			if f <= 30 {
				return "good"
			}
		}
	}
	return "neutral"
}

func extractKeyMetricsFromText(text string) []map[string]any {
	if strings.TrimSpace(text) == "" {
		return nil
	}
	items := make([]map[string]any, 0, 6)
	seen := make(map[string]struct{})
	addMetric := func(name, value string) {
		name = strings.TrimSpace(name)
		value = strings.TrimSpace(value)
		if name == "" || value == "" {
			return
		}
		if _, ok := seen[name]; ok {
			return
		}
		seen[name] = struct{}{}
		items = append(items, map[string]any{
			"name":   name,
			"value":  value,
			"status": inferMetricStatus(name, value),
		})
	}

	block := finalDecisionBlock(text)
	conclusionMetrics := map[string]bool{"置信度": true, "建议仓位": true}
	for _, p := range metricPatterns {
		src := text
		if conclusionMetrics[p.name] {
			src = block
		}
		if m := p.re.FindStringSubmatch(src); len(m) > 1 {
			addMetric(p.name, m[1])
		}
	}

	for _, line := range strings.Split(text, "\n") {
		line = strings.TrimSpace(line)
		if !strings.Contains(line, "|") {
			continue
		}
		cells := strings.Split(line, "|")
		if len(cells) < 3 {
			continue
		}
		name := strings.TrimSpace(cells[1])
		value := strings.TrimSpace(cells[2])
		if name == "" || value == "" || strings.Contains(name, "---") {
			continue
		}
		if strings.Contains(name, "指标") || strings.Contains(name, "维度") || strings.Contains(name, "名称") {
			continue
		}
		// 仅真正的指标名才做表格 name/value 配对；概览粗体行交给逐片段命名正则，避免错位。
		known := false
		for _, kw := range []string{"PE", "PB", "市值", "换手", "ROE", "RSI", "置信", "仓位", "涨跌", "市盈率", "市净率"} {
			if strings.Contains(name, kw) {
				known = true
				break
			}
		}
		if !known {
			continue
		}
		addMetric(name, value)
	}

	// 概览行（粗体 | 分隔、非 markdown 表格）：逐片段跑命名正则，避免 name/value 错位。
	for _, line := range strings.Split(text, "\n") {
		if !strings.Contains(line, "|") {
			continue
		}
		for _, cell := range strings.Split(line, "|") {
			for _, p := range metricPatterns {
				if m := p.re.FindStringSubmatch(cell); len(m) > 1 {
					addMetric(p.name, m[1])
				}
			}
		}
	}

	if len(items) > 8 {
		items = items[:8]
	}
	return items
}

func extractKeyMetrics(result map[string]any) []any {
	if result == nil {
		return nil
	}
	texts := []string{
		stringFromAny(result["fundamentals_report"]),
		stringFromAny(result["market_report"]),
		stringFromAny(result["macro_report"]),
		stringFromAny(result["smart_money_report"]),
		stringFromAny(result["final_trade_decision"]),
	}
	merged := strings.Join(texts, "\n")
	items := extractKeyMetricsFromText(merged)
	if len(items) == 0 {
		return nil
	}
	out := make([]any, 0, len(items))
	for _, item := range items {
		out = append(out, item)
	}
	return out
}

// applyPayloadToResult copies authoritative direction/decision onto the result map
// so job.result, SSE payloads, and persisted result_data stay aligned.
func applyPayloadToResult(result map[string]any, payload map[string]any) {
	if result == nil || payload == nil {
		return
	}
	if v := stringFromAny(payload["direction"]); v != "" {
		result["direction"] = v
	}
	if v := stringFromAny(payload["decision"]); v != "" {
		result["decision"] = v
	}
	if v, ok := payload["target_price"]; ok && v != nil {
		if f, ok := v.(float64); ok && isValidTradePrice(f) {
			result["target_price"] = f
		}
	}
	if v, ok := payload["stop_loss_price"]; ok && v != nil {
		if f, ok := v.(float64); ok && isValidTradePrice(f) {
			result["stop_loss_price"] = f
		}
	}
	if v, ok := payload["confidence"]; ok && v != nil {
		result["confidence"] = v
	}
	if v, ok := payload["risk_items"]; ok && !isEmptyPayloadSlice(v) {
		result["risk_items"] = v
	}
	if v, ok := payload["key_metrics"]; ok && !isEmptyPayloadSlice(v) {
		result["key_metrics"] = v
	}
}

// mergePayloadExtras fills nil confidence / prices / verdict direction like Python resolve_report_fields.
func mergePayloadExtras(result map[string]any, payload map[string]any) {
	if result == nil || payload == nil {
		return
	}
	final := stringFromAny(result["final_trade_decision"])
	trader := stringFromAny(result["trader_investment_plan"])

	decision := resolveTradeDecisionFromText(final)
	if decision != "" {
		payload["decision"] = decision
	}
	structuredDir := extractStructuredDirection(final)
	if vdir := extractVerdictDirection(final); vdir != "" {
		payload["direction"] = vdir
	}
	if structuredDir != "" {
		payload["direction"] = structuredDir
	} else if decision != "" {
		payload["direction"] = directionLabelForDecision(decision, "")
	}

	if payload["confidence"] == nil {
		if c := extractConfidenceRegex(final); c != nil {
			payload["confidence"] = float64(*c)
		}
	}

	decStr := stringFromAny(payload["decision"])
	var targetPtr, stopPtr *float64
	if payload["target_price"] == nil && decStr != "HOLD" {
		if p := extractTargetPriceRegex(final); p != nil {
			targetPtr = p
		} else if p := extractTargetPriceRegex(trader); p != nil {
			targetPtr = p
		}
	}
	if payload["stop_loss_price"] == nil && decStr != "HOLD" {
		if p := extractStopLossRegex(final); p != nil {
			stopPtr = p
		} else if p := extractStopLossRegex(trader); p != nil {
			stopPtr = p
		}
	}
	targetPtr, stopPtr = validateTradePrices(decStr, targetPtr, stopPtr)
	if targetPtr != nil {
		payload["target_price"] = *targetPtr
	} else if decStr == "HOLD" || payload["target_price"] == nil {
		// 无可用目标价（或 HOLD 应清空）；已有有效价位不被重置。
		payload["target_price"] = nil
	}
	if stopPtr != nil {
		payload["stop_loss_price"] = *stopPtr
	} else if decStr == "HOLD" || payload["stop_loss_price"] == nil {
		payload["stop_loss_price"] = nil
	}

	if isEmptyPayloadSlice(payload["risk_items"]) {
		if risks := extractRiskItems(result); len(risks) > 0 {
			payload["risk_items"] = risks
		}
	}
	if isEmptyPayloadSlice(payload["key_metrics"]) {
		if metrics := extractKeyMetrics(result); len(metrics) > 0 {
			payload["key_metrics"] = metrics
		}
	}
}
