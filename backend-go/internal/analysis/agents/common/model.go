package common

import (
	"bytes"
	"context"
	"fmt"
	"io"
	"net/http"
	"os"
	"sync"

	"github.com/cloudwego/eino-ext/components/model/openai"
	"github.com/cloudwego/eino/components/model"
)

// TryChatModel 从环境变量创建 ChatModel（与 backend/.env 中 OPENAI_* 命名一致；由 godotenv 加载）。
func TryChatModel(ctx context.Context) (model.ToolCallingChatModel, error) {
	apiKey := os.Getenv("OPENAI_API_KEY")
	if apiKey == "" {
		return nil, fmt.Errorf("OPENAI_API_KEY is required")
	}

	baseURL := os.Getenv("OPENAI_BASE_URL")
	if baseURL == "" {
		return nil, fmt.Errorf("OPENAI_BASE_URL is required")
	}

	modelName := os.Getenv("OPENAI_MODEL")
	if modelName == "" {
		return nil, fmt.Errorf("OPENAI_MODEL is required")
	}

	cfg := &openai.ChatModelConfig{
		APIKey:  apiKey,
		BaseURL: baseURL,
		Model:   modelName,
	}
	if os.Getenv("AI_DEBUG_DUMP") == "1" {
		cfg.HTTPClient = &http.Client{Transport: &dumpTransport{base: http.DefaultTransport}}
	}

	cm, err := openai.NewChatModel(ctx, cfg)
	if err != nil {
		return nil, fmt.Errorf("openai chat model: %w", err)
	}
	return cm, nil
}

// dumpTransport 在 AI_DEBUG_DUMP=1 时把出站请求体落盘，用于排查工具 schema 问题。
// 默认关闭，不影响线上行为。
type dumpTransport struct {
	base http.RoundTripper
	mu   sync.Mutex
}

func (t *dumpTransport) RoundTrip(req *http.Request) (*http.Response, error) {
	if req.Body != nil {
		body, err := io.ReadAll(req.Body)
		if err == nil {
			req.Body = io.NopCloser(bytes.NewReader(body))
			t.mu.Lock()
			f, ferr := os.OpenFile("/tmp/ai_req_dump.json", os.O_CREATE|os.O_WRONLY|os.O_TRUNC, 0o600)
			if ferr == nil {
				_, _ = f.Write(body)
				_ = f.Close()
			}
			t.mu.Unlock()
		}
	}
	return t.base.RoundTrip(req)
}

// NewChatModel 创建默认的 ChatModel（供各 Agent 构建使用；未配置密钥时 panic）
func NewChatModel() model.ToolCallingChatModel {
	cm, err := TryChatModel(context.Background())
	if err != nil {
		panic(err.Error())
	}
	return cm
}

// NewDeepThinkModel 创建深度思考模型（用于复杂决策）
func NewDeepThinkModel() model.ToolCallingChatModel {
	// 可以用不同的模型配置
	return NewChatModel()
}

// NewQuickThinkModel 创建快速思考模型（用于分析师）
func NewQuickThinkModel() model.ToolCallingChatModel {
	// 可以用更快速、更便宜的模型
	return NewChatModel()
}
