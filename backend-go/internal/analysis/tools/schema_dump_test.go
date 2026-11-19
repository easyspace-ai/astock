package tools

import (
	"context"
	"encoding/json"
	"sort"
	"strings"
	"testing"

	"github.com/cloudwego/eino/components/tool"
)

// allToolsForTest builds the full tool set without going through InitGlobalTools
// (which needs a data dir / tsdb). Schemas are inferred purely from struct tags,
// so a nil tsdb client is fine.
func allToolsForTest() []tool.BaseTool {
	stock := &StockTools{} // nil client is OK for schema generation
	em := NewEastMoneyTools()
	ths := NewTHSTools()

	var all []tool.BaseTool
	all = append(all, stock.NewGetStockDataTool())
	all = append(all, stock.NewGetStockBasicTool())
	all = append(all, stock.NewGetTradeCalendarTool())
	all = append(all, stock.NewGetDailyBasicTool())

	all = append(all, em.GetAllTools()...)
	all = append(all, ths.GetAllTools()...)
	all = append(all, NewW6NewsResearchTool())
	return all
}

func dumpToolSchema(t *testing.T, bt tool.BaseTool) (name string, required []string, properties []string, raw []byte) {
	info, err := bt.Info(context.Background())
	if err != nil {
		t.Fatalf("Info: %v", err)
	}
	name = info.Name
	js, err := info.ParamsOneOf.ToJSONSchema()
	if err != nil {
		t.Fatalf("ToJSONSchema: %v", err)
	}
	raw, err = json.MarshalIndent(js, "", "  ")
	if err != nil {
		t.Fatalf("marshal: %v", err)
	}
	required = append(required, js.Required...)
	if js.Properties != nil {
		for pair := js.Properties.Oldest(); pair != nil; pair = pair.Next() {
			properties = append(properties, pair.Key)
		}
	}
	return name, required, properties, raw
}

func hasDupes(s []string) (string, bool) {
	seen := map[string]bool{}
	for _, v := range s {
		if seen[v] {
			return v, true
		}
		seen[v] = true
	}
	return "", false
}

func TestDumpAllToolSchemas(t *testing.T) {
	tools := allToolsForTest()
	for _, bt := range tools {
		name, required, properties, raw := dumpToolSchema(t, bt)
		t.Logf("==== TOOL %s ====", name)
		t.Logf("required:  %v", required)
		t.Logf("properties: %v", properties)
		t.Logf("full schema:\n%s", string(raw))
		_, dupReq := hasDupes(required)
		_, dupProp := hasDupes(properties)
		if dupReq || dupProp {
			t.Errorf("TOOL %s: dupReq=%v dupProp=%v required=%v properties=%v",
				name, dupReq, dupProp, required, properties)
		}
	}
}

func TestVerifyAllToolSchemasUnique(t *testing.T) {
	tools := allToolsForTest()
	sort.Slice(tools, func(i, j int) bool {
		ni, _ := tools[i].Info(context.Background())
		nj, _ := tools[j].Info(context.Background())
		return ni.Name < nj.Name
	})
	pass := 0
	fail := 0
	for _, bt := range tools {
		name, required, properties, _ := dumpToolSchema(t, bt)
		_, dupReq := hasDupes(required)
		_, dupProp := hasDupes(properties)
		status := "PASS"
		if dupReq || dupProp {
			status = "FAIL"
			fail++
		} else {
			pass++
		}
		t.Logf("| %s | %s | required=%v | properties=%v |", status, name, required, properties)
		_ = strings.TrimSpace // keep import used in case
	}
	t.Logf("SUMMARY: pass=%d fail=%d total=%d", pass, fail, len(tools))
	if fail > 0 {
		t.Errorf("%d tools have non-unique schema elements", fail)
	}
}
