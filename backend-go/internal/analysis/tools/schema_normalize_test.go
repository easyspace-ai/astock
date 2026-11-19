package tools

import (
	"testing"

	orderedmap "github.com/wk8/go-ordered-map/v2"

	"github.com/eino-contrib/jsonschema"
)

// TestDedupeSchemaRequired proves the helper removes duplicate `required`
// entries while preserving first-occurrence order, and recurses into nested
// object schemas. This guards the regression even when the upstream
// jsonschema generator happens to produce a clean schema today.
func TestDedupeSchemaRequired(t *testing.T) {
	// Build a schema that mimics the production failure: required has a dup.
	inner := &jsonschema.Schema{
		Type:     "object",
		Required: []string{"b", "a", "b", "c", "a"},
		Properties: func() *orderedmap.OrderedMap[string, *jsonschema.Schema] {
			m := orderedmap.New[string, *jsonschema.Schema]()
			m.Set("a", &jsonschema.Schema{Type: "string"})
			m.Set("b", &jsonschema.Schema{Type: "string"})
			m.Set("c", &jsonschema.Schema{Type: "string"})
			return m
		}(),
	}
	root := &jsonschema.Schema{
		Type:     "object",
		Required: []string{"ts_code", "start_date", "ts_code", "end_date", "start_date"},
		Properties: func() *orderedmap.OrderedMap[string, *jsonschema.Schema] {
			m := orderedmap.New[string, *jsonschema.Schema]()
			m.Set("ts_code", &jsonschema.Schema{Type: "string"})
			m.Set("start_date", &jsonschema.Schema{Type: "string"})
			m.Set("end_date", inner) // nested object with its own dup required
			return m
		}(),
	}

	dedupeSchemaRequired(root)

	if got, want := len(root.Required), 3; got != want {
		t.Fatalf("root required len: got %d want %d (%v)", got, want, root.Required)
	}
	if !equalSlice(root.Required, []string{"ts_code", "start_date", "end_date"}) {
		t.Fatalf("root required order: got %v want [ts_code start_date end_date]", root.Required)
	}
	if got, want := len(inner.Required), 3; got != want {
		t.Fatalf("inner required len: got %d want %d (%v)", got, want, inner.Required)
	}
	if !equalSlice(inner.Required, []string{"b", "a", "c"}) {
		t.Fatalf("inner required order: got %v want [b a c]", inner.Required)
	}

	// Idempotent: running again must not change anything.
	prev := append([]string(nil), root.Required...)
	dedupeSchemaRequired(root)
	if !equalSlice(root.Required, prev) {
		t.Fatalf("dedupe not idempotent: got %v want %v", root.Required, prev)
	}

	// Nil safety.
	dedupeSchemaRequired(nil)
	empty := &jsonschema.Schema{}
	dedupeSchemaRequired(empty) // must not panic
}

func equalSlice(a, b []string) bool {
	if len(a) != len(b) {
		return false
	}
	for i := range a {
		if a[i] != b[i] {
			return false
		}
	}
	return true
}
