package prompts

import (
	"os"
	"path/filepath"
	"testing"
)

func TestResolvePrefersOverride(t *testing.T) {
	dir := t.TempDir()
	Register("test-agent", "测试", "测试", "default prompt")
	if err := Init(dir); err != nil {
		t.Fatal(err)
	}
	if got := Resolve("test-agent", "default prompt"); got != "default prompt" {
		t.Fatalf("expected default, got %q", got)
	}
	if _, err := Set("test-agent", "override prompt"); err != nil {
		t.Fatal(err)
	}
	if got := Resolve("test-agent", "default prompt"); got != "override prompt" {
		t.Fatalf("expected override, got %q", got)
	}
	path := filepath.Join(dir, overrideFileName)
	if _, err := os.Stat(path); err != nil {
		t.Fatalf("override file missing: %v", err)
	}
	// Reload from disk
	overrides = map[string]string{}
	if err := Init(dir); err != nil {
		t.Fatal(err)
	}
	if got := Resolve("test-agent", "default prompt"); got != "override prompt" {
		t.Fatalf("expected persisted override, got %q", got)
	}
}
