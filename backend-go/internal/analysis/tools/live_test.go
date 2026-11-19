//go:build live

package tools

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"strings"
	"testing"
	"time"

	"github.com/cloudwego/eino/components/tool"
)

// TestLiveTools exercises every registered analysis tool against live HTTP
// endpoints (East Money / Tencent / Sina) to ensure no "数据找不到 / 表不存在"
// errors remain after the HTTP-first refactor.
//
// Run: go test -tags=live -run TestLiveTools -v ./internal/analysis/tools/ -timeout 300s
func TestLiveTools(t *testing.T) {
	if err := InitGlobalTools(""); err != nil {
		t.Fatalf("InitGlobalTools failed: %v", err)
	}

	all := GetGlobalTools()

	// name -> JSON input string
	inputs := map[string]string{
		"get_stock_data":          `{"ts_code":"600519.SH","start_date":"20250101","end_date":"20260701"}`,
		"get_stock_basic":         `{"ts_code":"600519.SH"}`,
		"get_trade_calendar":      `{"start_date":"20260601","end_date":"20260701"}`,
		"get_daily_basic":         `{"ts_code":"600519.SH","start_date":"20260101","end_date":"20260701"}`,
		"get_dragon_tiger_board":  `{"code":"600519","look_back_days":60}`,
		"get_daily_dragon_tiger":  `{"trade_date":"2026-06-29","min_net_buy":0}`,
		"get_concept_blocks":      `{"code":"600519"}`,
		"get_fund_flow":           `{"code":"600519"}`,
		"get_fund_flow_120d":      `{"code":"600519"}`,
		"get_industry_comparison": `{"top_n":10}`,
		"get_lockup_expiry":       `{"code":"600519","forward_days":90}`,
		"get_margin_trading":      `{"code":"600519","days":30}`,
		"get_block_trade":         `{"code":"600519","days":30}`,
		"get_holder_count":        `{"code":"600519"}`,
		"get_dividend":            `{"code":"600519"}`,
		"get_news":                `{"code":"600519","page_size":20}`,
		"get_global_news":         `{"page_size":50}`,
		"get_hot_stocks":          `{"top_n":10}`,
		"get_northbound_flow":     `{"days":10}`,
		"get_profit_forecast":     `{"code":"600519"}`,
	}

	type result struct {
		name   string
		status string
		note   string
	}
	var results []result
	anyFail := false

	for _, bt := range all {
		it, ok := bt.(tool.InvokableTool)
		if !ok {
			results = append(results, result{name: "?", status: "SKIP", note: "not InvokableTool"})
			continue
		}
		info, err := it.Info(context.Background())
		if err != nil {
			results = append(results, result{name: "?", status: "FAIL", note: "Info: " + err.Error()})
			anyFail = true
			continue
		}
		name := info.Name
		jsonIn, ok := inputs[name]
		if !ok {
			results = append(results, result{name: name, status: "SKIP", note: "no input mapped"})
			continue
		}

		ctx, cancel := context.WithTimeout(context.Background(), 40*time.Second)
		out, err := it.InvokableRun(ctx, jsonIn)
		cancel()

		status := "PASS"
		note := ""
		if err != nil {
			status = "FAIL"
			note = "err: " + err.Error()
			anyFail = true
		} else {
			s := fmt.Sprintf("%v", out)
			if len(s) < 30 {
				status = "FAIL"
				note = fmt.Sprintf("too short (%d): %q", len(s), s)
				anyFail = true
			} else if isBroken(s) {
				status = "FAIL"
				note = firstN(s, 160)
				anyFail = true
			} else {
				note = firstN(s, 90)
			}
		}
		results = append(results, result{name: name, status: status, note: note})
	}

	// Print summary table
	fmt.Fprintln(os.Stderr, "")
	fmt.Fprintln(os.Stderr, "TOOL | STATUS | NOTE")
	fmt.Fprintln(os.Stderr, "--- | --- | ---")
	for _, r := range results {
		fmt.Fprintf(os.Stderr, "%s | %s | %s\n", r.name, r.status, r.note)
	}
	fmt.Fprintln(os.Stderr, "")

	if anyFail {
		t.Fatalf("one or more tools failed live test (see table above)")
	}
}

// isBroken returns true if the output looks like a tool error rather than a
// legitimate (possibly empty) data response. A response that starts with the
// tool's own markdown header ("# ...") is considered valid even if the data
// list is empty (e.g. "近60日无龙虎榜记录").
func isBroken(s string) bool {
	trimmed := strings.TrimSpace(s)
	if trimmed == "" {
		return true
	}
	lower := strings.ToLower(trimmed)
	if strings.Contains(lower, "表不存在") {
		return true
	}
	if strings.Contains(lower, "request ") && strings.Contains(lower, "failed") {
		return true
	}
	if strings.Contains(lower, "parse ") && strings.Contains(lower, "failed") {
		return true
	}
	// "无数据" without a header is suspicious; with a header it's fine.
	if strings.Contains(trimmed, "无数据") && !strings.HasPrefix(trimmed, "#") {
		return true
	}
	return false
}

func firstN(s string, n int) string {
	s = strings.ReplaceAll(s, "\n", " ")
	s = strings.TrimSpace(s)
	if len(s) > n {
		return s[:n] + "..."
	}
	return s
}

// keep encoding/json import used in case future expansion needs it
var _ = json.Marshal
