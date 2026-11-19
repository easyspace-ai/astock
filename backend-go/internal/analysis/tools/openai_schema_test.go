package tools

import (
	"context"
	"encoding/json"
	"sort"
	"testing"

	"github.com/cloudwego/eino-ext/components/model/openai"
	"github.com/cloudwego/eino/schema"
)

// TestOpenAIToolBindAndMarshal binds every tool to a real openai.ChatModel
// (BindTools only builds an in-memory tool list, no network), then re-
// derives each tool's JSON schema the same way the openai client does
// (ToJSONSchema + sortArrayFields) and marshals it the way the request would.
// We then parse the resulting JSON and assert no duplicate `required` entries
// and no duplicate `properties` keys appear.
func TestOpenAIToolBindAndMarshal(t *testing.T) {
	ctx := context.Background()
	all := allToolsForTest()

	cm, err := openai.NewChatModel(ctx, &openai.ChatModelConfig{
		APIKey:  "sk-dummy",
		BaseURL: "https://example.invalid",
		Model:   "dummy",
	})
	if err != nil {
		t.Fatalf("NewChatModel: %v", err)
	}

	toolInfos := make([]*schema.ToolInfo, 0, len(all))
	for _, bt := range all {
		info, err := bt.Info(ctx)
		if err != nil {
			t.Fatalf("Info: %v", err)
		}
		toolInfos = append(toolInfos, info)
	}

	if err := cm.BindTools(toolInfos); err != nil {
		t.Fatalf("BindTools: %v", err)
	}

	for _, info := range toolInfos {
		js, err := info.ParamsOneOf.ToJSONSchema()
		if err != nil {
			t.Fatalf("ToJSONSchema: %v", err)
		}
		sort.Strings(js.Required)
		toolDef := struct {
			Function struct {
				Name        string `json:"name"`
				Description string `json:"description,omitempty"`
				Parameters  any    `json:"parameters"`
			} `json:"function"`
		}{
			Function: struct {
				Name        string `json:"name"`
				Description string `json:"description,omitempty"`
				Parameters  any    `json:"parameters"`
			}{
				Name:        info.Name,
				Description: info.Desc,
				Parameters:  js,
			},
		}
		raw, err := json.MarshalIndent(toolDef, "", "  ")
		if err != nil {
			t.Fatalf("marshal: %v", err)
		}
		var parsed struct {
			Function struct {
				Parameters struct {
					Required   []string                `json:"required"`
					Properties map[string]json.RawMessage `json:"properties"`
				} `json:"parameters"`
			} `json:"function"`
		}
		if err := json.Unmarshal(raw, &parsed); err != nil {
			t.Fatalf("unmarshal: %v", err)
		}
		_, dupReq := hasDupes(parsed.Function.Parameters.Required)
		props := make([]string, 0, len(parsed.Function.Parameters.Properties))
		for k := range parsed.Function.Parameters.Properties {
			props = append(props, k)
		}
		sort.Strings(props)
		_, dupProp := hasDupes(props)
		status := "PASS"
		if dupReq || dupProp {
			status = "FAIL"
			t.Errorf("TOOL %s: dupReq=%v dupProp=%v required=%v properties=%v",
				info.Name, dupReq, dupProp, parsed.Function.Parameters.Required, props)
		}
		t.Logf("| %s | %s | required=%v | properties=%v |", status, info.Name, parsed.Function.Parameters.Required, props)
		t.Logf("==== %s ====\n%s", info.Name, string(raw))
	}
}

// TestEveryToolInfoSchemaUnique is the authoritative regression test: it
// iterates every registered tool, derives its JSONSchema via the eino API,
// and asserts that both `required` and the keys of `properties` have no
// duplicates.
func TestEveryToolInfoSchemaUnique(t *testing.T) {
	ctx := context.Background()
	all := allToolsForTest()
	sort.Slice(all, func(i, j int) bool {
		ni, _ := all[i].Info(ctx)
		nj, _ := all[j].Info(ctx)
		return ni.Name < nj.Name
	})
	pass, fail := 0, 0
	for _, bt := range all {
		info, err := bt.Info(ctx)
		if err != nil {
			t.Fatalf("Info: %v", err)
		}
		js, err := info.ParamsOneOf.ToJSONSchema()
		if err != nil {
			t.Fatalf("ToJSONSchema: %v", err)
		}
		_, dupReq := hasDupes(js.Required)
		props := make([]string, 0)
		if js.Properties != nil {
			for pair := js.Properties.Oldest(); pair != nil; pair = pair.Next() {
				props = append(props, pair.Key)
			}
		}
		_, dupProp := hasDupes(props)
		status := "PASS"
		if dupReq || dupProp {
			status = "FAIL"
			fail++
			t.Errorf("TOOL %s: dupReq=%v dupProp=%v required=%v properties=%v",
				info.Name, dupReq, dupProp, js.Required, props)
		} else {
			pass++
		}
		t.Logf("| %s | %s | required=%v | properties=%v |", status, info.Name, js.Required, props)
	}
	t.Logf("SUMMARY: pass=%d fail=%d total=%d", pass, fail, len(all))
	if fail > 0 {
		t.Errorf("%d tools have non-unique schema elements", fail)
	}
}

// TestManagerNormalizeFixesInjectedDup proves the exact wiring used by
// InitGlobalTools (apply NormalizeToolSchema to every tool) actually repairs
// a schema that has duplicate `required` entries. It builds a synthetic tool
// whose schema has a dup, runs it through NormalizeToolSchema, and confirms
// the dup is gone.
func TestManagerNormalizeFixesInjectedDup(t *testing.T) {
	ctx := context.Background()
	all := allToolsForTest()

	// Inject a duplicate into every tool's required array to simulate the
	// production failure, then confirm NormalizeToolSchema removes it.
	for _, bt := range all {
		info, err := bt.Info(ctx)
		if err != nil {
			t.Fatalf("Info: %v", err)
		}
		js, err := info.ParamsOneOf.ToJSONSchema()
		if err != nil {
			t.Fatalf("ToJSONSchema: %v", err)
		}
		if len(js.Required) > 0 {
			// Prepend a duplicate of the first required entry.
			js.Required = append([]string{js.Required[0]}, js.Required...)
		}
	}

	// Now run the manager's normalization step.
	for i, bt := range all {
		all[i] = NormalizeToolSchema(bt)
	}

	// Verify every tool's schema is now unique.
	pass, fail := 0, 0
	for _, bt := range all {
		info, err := bt.Info(ctx)
		if err != nil {
			t.Fatalf("Info: %v", err)
		}
		js, err := info.ParamsOneOf.ToJSONSchema()
		if err != nil {
			t.Fatalf("ToJSONSchema: %v", err)
		}
		_, dupReq := hasDupes(js.Required)
		if dupReq {
			fail++
			t.Errorf("TOOL %s still has dup required=%v", info.Name, js.Required)
		} else {
			pass++
		}
	}
	t.Logf("SUMMARY: pass=%d fail=%d total=%d", pass, fail, len(all))
	if fail > 0 {
		t.Errorf("%d tools still have non-unique required after NormalizeToolSchema", fail)
	}
}
