// Package tools — schema normalization.
//
// OpenAI-compatible LLM providers (DeepSeek, OpenAI, ...) validate a tool's
// JSON schema when the agent first binds it. If the schema's `required` array
// contains duplicate entries the provider rejects the whole request with
// HTTP 400:
//
//	Invalid schema for function 'get_stock_data':
//	  ["start_date","ts_code","ts_code"] has non-unique elements
//
// eino's utils.InferTool builds the schema by reflecting Go struct tags via
// github.com/eino-contrib/jsonschema. The generator is opaque and version
// dependent; to guarantee the provider never sees a non-unique schema element
// for ANY registered tool, we post-process every tool's parameter schema once
// at registration time and remove duplicate `required` entries (preserving
// first-occurrence order). Properties keys are unique by construction (the
// schema uses an ordered map), but we recurse into nested object schemas so
// the whole tree is normalized.
//
// This normalization is idempotent and runs once per tool during
// InitGlobalTools; it does not change tool execution behavior.
package tools

import (
	"context"
	"log"
	"strings"

	"github.com/cloudwego/eino/components/tool"
	"github.com/eino-contrib/jsonschema"
)

// dedupeSchemaRequired removes duplicate entries from s.Required (preserving
// first-occurrence order) and recurses into nested object schemas, array
// items, and combinator sub-schemas (allOf/anyOf/oneOf/additionalProperties)
// so every object schema in the tree has a unique Required slice.
//
// It mutates s in place. The mutation is safe because each tool built by
// utils.InferTool owns a private *jsonschema.Schema (returned by
// ParamsOneOf.ToJSONSchema), so there is no cross-tool sharing.
func dedupeSchemaRequired(s *jsonschema.Schema) {
	if s == nil {
		return
	}

	if len(s.Required) > 1 {
		seen := make(map[string]struct{}, len(s.Required))
		out := make([]string, 0, len(s.Required))
		for _, r := range s.Required {
			if _, ok := seen[r]; ok {
				continue
			}
			seen[r] = struct{}{}
			out = append(out, r)
		}
		s.Required = out
	}

	if s.Properties != nil {
		for pair := s.Properties.Oldest(); pair != nil; pair = pair.Next() {
			dedupeSchemaRequired(pair.Value)
		}
	}
	if s.Items != nil {
		dedupeSchemaRequired(s.Items)
	}
	for _, sub := range s.AllOf {
		dedupeSchemaRequired(sub)
	}
	for _, sub := range s.AnyOf {
		dedupeSchemaRequired(sub)
	}
	for _, sub := range s.OneOf {
		dedupeSchemaRequired(sub)
	}
	if s.AdditionalProperties != nil && s.AdditionalProperties != jsonschema.FalseSchema {
		dedupeSchemaRequired(s.AdditionalProperties)
	}
	if s.If != nil {
		dedupeSchemaRequired(s.If)
	}
	if s.Then != nil {
		dedupeSchemaRequired(s.Then)
	}
	if s.Else != nil {
		dedupeSchemaRequired(s.Else)
	}
}

// NormalizeToolSchema post-processes a tool's parameter JSON schema so it has
// no duplicate `required` entries (and, by construction, no duplicate
// `properties` keys). It mutates the tool's underlying *schema.ToolInfo in
// place — every tool built by utils.InferTool returns the same *ToolInfo
// pointer from Info(), so the normalization persists for the life of the
// tool. It returns the same tool for chaining.
//
// If the tool has no parameter schema (ParamsOneOf == nil) or Info() fails,
// the tool is returned unchanged.
func NormalizeToolSchema(bt tool.BaseTool) tool.BaseTool {
	if bt == nil {
		return bt
	}
	info, err := bt.Info(context.Background())
	if err != nil || info == nil || info.ParamsOneOf == nil {
		return bt
	}
	js, err := info.ParamsOneOf.ToJSONSchema()
	if err != nil || js == nil {
		return bt
	}
	dedupeSchemaRequired(js)
	log.Printf("[tools] schema normalized name=%s required=[%s] unique=%v",
		info.Name, strings.Join(js.Required, " "), hasUniqueRequired(js.Required))
	return bt
}

// hasUniqueRequired reports whether rs has no duplicate entries.
func hasUniqueRequired(rs []string) bool {
	seen := make(map[string]struct{}, len(rs))
	for _, r := range rs {
		if _, ok := seen[r]; ok {
			return false
		}
		seen[r] = struct{}{}
	}
	return true
}
