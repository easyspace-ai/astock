// Package tools — tool schema isolation.
//
// eino builds every tool's parameter JSON schema from the Go input struct once
// (utils.InferTool) and hands the *same* *schema.ToolInfo (and its embedded
// *jsonschema.Schema) to every agent that binds that tool. Several analysts
// build their agents concurrently, and the OpenAI-compatible client mutates the
// schema in place while binding (it sorts `required` and walks properties).
// Because all agents share the same schema object, concurrent binding corrupts
// it: entries of `required` get swapped between tools (e.g. `start_date`
// becomes `end_date`) and stray fragments show up, so providers reject the
// whole request with:
//
//	Invalid schema for function 'get_stock_data':
//	  ["end_date","end_date","ts_code"] has non-unique elements
//
// isolatedTool wraps each registered tool and snapshots its schema as plain
// JSON once, at registration time (single-threaded, before any agent exists).
// Info() then rebuilds a private *schema.ToolInfo from those bytes on every
// call, so each agent binds its own copy and no mutation can leak across
// agents.
package tools

import (
	"context"
	"encoding/json"
	"fmt"

	"github.com/cloudwego/eino/components/tool"
	"github.com/cloudwego/eino/schema"
	"github.com/eino-contrib/jsonschema"
)

// isolatedTool is a tool.BaseTool + tool.InvokableTool that returns a fresh
// copy of its parameter schema on every Info() call.
type isolatedTool struct {
	origin     tool.BaseTool
	name       string
	desc       string
	paramsJSON []byte // nil when the tool takes no parameters
}

// WithIsolatedSchema snapshots the tool's schema and returns a wrapper that
// hands out an independent copy per Info() call. If the schema cannot be
// captured the original tool is returned unchanged.
func WithIsolatedSchema(bt tool.BaseTool) tool.BaseTool {
	if bt == nil {
		return bt
	}
	info, err := bt.Info(context.Background())
	if err != nil || info == nil {
		return bt
	}

	w := &isolatedTool{origin: bt, name: info.Name, desc: info.Desc}
	if info.ParamsOneOf != nil {
		js, err := info.ParamsOneOf.ToJSONSchema()
		if err != nil || js == nil {
			return bt
		}
		raw, err := json.Marshal(js)
		if err != nil {
			return bt
		}
		w.paramsJSON = raw
	}
	return w
}

// Info returns a tool info whose schema was freshly decoded from the snapshot,
// so callers may mutate it freely without affecting other agents.
func (t *isolatedTool) Info(_ context.Context) (*schema.ToolInfo, error) {
	info := &schema.ToolInfo{Name: t.name, Desc: t.desc}
	if len(t.paramsJSON) == 0 {
		return info, nil
	}
	js := &jsonschema.Schema{}
	if err := json.Unmarshal(t.paramsJSON, js); err != nil {
		return nil, fmt.Errorf("decode tool schema %s: %w", t.name, err)
	}
	info.ParamsOneOf = schema.NewParamsOneOfByJSONSchema(js)
	return info, nil
}

// InvokableRun delegates execution to the wrapped tool.
func (t *isolatedTool) InvokableRun(ctx context.Context, args string, opts ...tool.Option) (string, error) {
	it, ok := t.origin.(tool.InvokableTool)
	if !ok {
		return "", fmt.Errorf("tool %s is not invokable", t.name)
	}
	return it.InvokableRun(ctx, args, opts...)
}
