// Package toolerr provides ToolNode middleware that turns tool invocation errors
// into successful tool outputs carrying an error description, so the LLM can
// continue the conversation instead of aborting the whole run.
package toolerr

import (
	"context"
	"fmt"
	"runtime/debug"

	"github.com/cloudwego/eino/compose"
	"github.com/cloudwego/eino/schema"
)

func swallowErrorHandler(ctx context.Context, in *compose.ToolInput, err error) string {
	_ = ctx
	return fmt.Sprintf(
		"[tool failed; you may continue analysis with other data or reasoning] tool=%s error=%s",
		in.Name,
		err.Error(),
	)
}

func swallowPanicHandler(in *compose.ToolInput, recovered any) string {
	return fmt.Sprintf(
		"[tool panic recovered; you may continue analysis with other data or reasoning] tool=%s panic=%v stack=%s",
		in.Name,
		recovered,
		debug.Stack(),
	)
}

// Invokable wraps invokable tool endpoints.
func Invokable(next compose.InvokableToolEndpoint) compose.InvokableToolEndpoint {
	return func(ctx context.Context, in *compose.ToolInput) (out *compose.ToolOutput, err error) {
		defer func() {
			if r := recover(); r != nil {
				out = &compose.ToolOutput{Result: swallowPanicHandler(in, r)}
				err = nil
			}
		}()
		out, err = next(ctx, in)
		if err != nil {
			if _, ok := compose.IsInterruptRerunError(err); ok {
				return nil, err
			}
			return &compose.ToolOutput{Result: swallowErrorHandler(ctx, in, err)}, nil
		}
		return out, nil
	}
}

// Streamable wraps streamable tool endpoints.
func Streamable(next compose.StreamableToolEndpoint) compose.StreamableToolEndpoint {
	return func(ctx context.Context, in *compose.ToolInput) (streamOut *compose.StreamToolOutput, err error) {
		defer func() {
			if r := recover(); r != nil {
				msg := swallowPanicHandler(in, r)
				streamOut = &compose.StreamToolOutput{Result: schema.StreamReaderFromArray([]string{msg})}
				err = nil
			}
		}()
		streamOut, err = next(ctx, in)
		if err != nil {
			if _, ok := compose.IsInterruptRerunError(err); ok {
				return nil, err
			}
			msg := swallowErrorHandler(ctx, in, err)
			return &compose.StreamToolOutput{Result: schema.StreamReaderFromArray([]string{msg})}, nil
		}
		return streamOut, nil
	}
}

// Middleware must be the first entry in ToolCallMiddlewares so outer middleware
// errors are still converted (see eino errorremover example).
func Middleware() compose.ToolMiddleware {
	return compose.ToolMiddleware{Invokable: Invokable, Streamable: Streamable}
}
