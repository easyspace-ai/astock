package toolerr

import (
	"context"
	"strings"
	"testing"

	"github.com/cloudwego/eino/compose"
)

func TestInvokableRecoversPanic(t *testing.T) {
	t.Parallel()

	endpoint := Invokable(func(ctx context.Context, in *compose.ToolInput) (*compose.ToolOutput, error) {
		_ = ctx
		_ = in
		panic("slice bounds out of range [:10] with length 0")
	})

	out, err := endpoint(context.Background(), &compose.ToolInput{Name: "get_dividend"})
	if err != nil {
		t.Fatalf("expected nil error after panic recovery, got %v", err)
	}
	if out == nil || !strings.Contains(out.Result, "tool panic recovered") {
		t.Fatalf("unexpected output: %#v", out)
	}
	if !strings.Contains(out.Result, "get_dividend") {
		t.Fatalf("expected tool name in output, got %q", out.Result)
	}
}
