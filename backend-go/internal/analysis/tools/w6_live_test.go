//go:build live

package tools

import (
	"context"
	"fmt"
	"os"
	"strings"
	"testing"
	"time"

	"github.com/joho/godotenv"
)

// TestW6NewsLive directly invokes W6NewsResearch against the live W6 gateway
// so we can see exactly what the tool returns (or where it fails) from the
// backend's own code path and network.
//
// Run from backend/:
//
//	go test -tags live -run TestW6NewsLive -v -timeout 15m ./internal/analysis/tools/
func TestW6NewsLive(t *testing.T) {
	// Best-effort .env load (when running from backend/ or its parent).
	_ = godotenv.Load("../.env")
	_ = godotenv.Load(".env")

	// Hardcode the known-good values from backend/.env so the test works
	// even if godotenv can't find the file (e.g. run from a different cwd).
	if os.Getenv("AI_SDK_BASE_URL") == "" {
		os.Setenv("AI_SDK_BASE_URL", "https://w6.hc.infhis.cn")
	}
	if os.Getenv("AI_SDK_SERVICE_API_KEY") == "" {
		os.Setenv("AI_SDK_SERVICE_API_KEY", "iDPSJOiKbUumcT3xhIl4V4XTVNdSZVI4")
	}
	if os.Getenv("AI_SDK_UPLOAD_PATH") == "" {
		os.Setenv("AI_SDK_UPLOAD_PATH", "/api/upload")
	}
	// Make sure provider debug frame logs print to stderr.
	os.Setenv("AI_SDK_DEBUG", "true")
	os.Setenv("W6SDK_DEBUG", "1")

	ctx, cancel := context.WithTimeout(context.Background(), 15*time.Minute)
	defer cancel()

	start := time.Now()
	md, err := W6NewsResearch(ctx, "深高速", "600548.SH", "2026-07-02")
	elapsed := time.Since(start)

	fmt.Fprintf(os.Stderr, "\n===== W6NewsResearch LIVE =====\n")
	fmt.Fprintf(os.Stderr, "elapsed: %s\n", elapsed)
	fmt.Fprintf(os.Stderr, "err: %v\n", err)
	fmt.Fprintf(os.Stderr, "md_len: %d\n", len(md))
	degraded := strings.HasPrefix(strings.TrimSpace(md), "⚠️") ||
		strings.Contains(md, "⚠️ W6") ||
		strings.Contains(md, "降级")
	fmt.Fprintf(os.Stderr, "degraded: %v\n", degraded)
	fmt.Fprintf(os.Stderr, "----- markdown (first 2000 bytes) -----\n")
	preview := md
	if len(preview) > 2000 {
		preview = preview[:2000] + "...[truncated]"
	}
	fmt.Fprintf(os.Stderr, "%s\n", preview)
	fmt.Fprintf(os.Stderr, "===== end =====\n\n")

	if err != nil {
		t.Fatalf("W6NewsResearch returned error: %v", err)
	}
	if degraded {
		t.Fatalf("W6NewsResearch returned degraded output (len=%d): %s", len(md), firstN(md, 200))
	}
	if len(md) < 200 {
		t.Fatalf("W6NewsResearch returned suspiciously short output (len=%d): %q", len(md), md)
	}
}
