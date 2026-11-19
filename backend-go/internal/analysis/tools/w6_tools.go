package tools

import (
	"context"
	"encoding/json"
	"fmt"
	"log"
	"os"
	"regexp"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/cloudwego/eino/components/tool"
	"github.com/cloudwego/eino/components/tool/utils"

	"github.com/easyspace-ai/w6sdk/pkg/gateway/client"
	"github.com/easyspace-ai/w6sdk/pkg/gateway/provider"
)

const (
	w6NewsStreamTimeout = 15 * time.Minute
	// W6PrefetchTimeout 是预取阶段（在工作流 graph 之外）调用 W6 的独立预算。
	// 它独立于 AnalysisWorkflowTimeout，避免占用工作流预算。
	w6PrefetchTimeoutDefault = 18 * time.Minute
)

// W6PrefetchTimeout 返回 W6 预取阶段的超时（默认 18m，可用 AIGOSTOCK_W6_PREFETCH_TIMEOUT_SEC 覆盖，单位秒）。
func W6PrefetchTimeout() time.Duration {
	return envDuration("AIGOSTOCK_W6_PREFETCH_TIMEOUT_SEC", w6PrefetchTimeoutDefault)
}

var (
	w6ClientOnce     sync.Once
	w6ClientInst     *client.Client
	w6ProviderInst   *provider.Provider
	w6InitErr        error
	w6WSReadTimeout  = 5 * time.Minute
	w6WSWriteTimeout = 5 * time.Minute
)

// envDuration reads an env var as seconds; returns def if unset/invalid.
func envDuration(secEnv string, def time.Duration) time.Duration {
	v := strings.TrimSpace(os.Getenv(secEnv))
	if v == "" {
		return def
	}
	n, err := strconv.Atoi(v)
	if err != nil || n <= 0 {
		return def
	}
	return time.Duration(n) * time.Second
}

// maskKey returns a masked form of an API key for safe logging.
func maskKey(k string) string {
	k = strings.TrimSpace(k)
	if k == "" {
		return "<empty>"
	}
	if len(k) <= 8 {
		return "***"
	}
	return k[:4] + "***" + k[len(k)-4:]
}

func initW6Client() {
	w6ClientOnce.Do(func() {
		baseURL := strings.TrimSpace(os.Getenv("AI_SDK_BASE_URL"))
		apiKey := strings.TrimSpace(os.Getenv("AI_SDK_SERVICE_API_KEY"))
		uploadPath := strings.TrimSpace(os.Getenv("AI_SDK_UPLOAD_PATH"))

		// HTTP timeout (also used as a fallback for WS read deadline).
		httpTimeout := envDuration("AI_SDK_TIMEOUT_SEC", 120*time.Second)
		// WS per-frame read deadline. OSINT agents can think for minutes
		// between frames, so default to 5m — independent of the HTTP timeout.
		wsReadTimeout := envDuration("AI_SDK_WS_TIMEOUT", 5*time.Minute)
		// WS handshake: wait for first update+status. Default 5m to tolerate
		// slow upstream session creation (overridable via env).
		wsHandshake := envDuration("AI_SDK_WS_HANDSHAKE_TIMEOUT_SEC", 5*time.Minute)
		// WS write frame timeout (input/stop). Default 5m.
		wsWrite := envDuration("AI_SDK_WS_WRITE_TIMEOUT_SEC", 5*time.Minute)
		// WS push ack timeout (PushUserInput only). Default 30s.
		wsPushAck := envDuration("AI_SDK_WS_PUSH_ACK_TIMEOUT_SEC", 30*time.Second)

		retryMax := 1
		if v := strings.TrimSpace(os.Getenv("AI_SDK_RETRY_MAX")); v != "" {
			if n, err := strconv.Atoi(v); err == nil && n >= 0 {
				retryMax = n
			}
		}

		if baseURL == "" || apiKey == "" {
			w6InitErr = fmt.Errorf("缺少 AI_SDK_BASE_URL / AI_SDK_SERVICE_API_KEY 环境变量")
			log.Printf("[w6] init FAILED: %v (base=%q key=%s)", w6InitErr, baseURL, maskKey(apiKey))
			return
		}

		// Use the longer of httpTimeout / wsReadTimeout as the provider
		// cfg.Timeout — it serves both as httpClient.Timeout and as the
		// per-frame WS read deadline in streamViaWS.
		effectiveTimeout := wsReadTimeout
		if httpTimeout > effectiveTimeout {
			effectiveTimeout = httpTimeout
		}

		cfg := provider.Config{
			BaseURL:            baseURL,
			ServiceAPIKey:      apiKey,
			UploadPath:         uploadPath,
			Timeout:            effectiveTimeout,
			WSWriteTimeout:     wsWrite,
			WSHandshakeTimeout: wsHandshake,
			WSPushAckTimeout:   wsPushAck,
			Debug:              true,
		}
		p := provider.New(cfg)
		w6ProviderInst = p
		w6ClientInst = client.New(p, client.RetryConfig{
			MaxAttempts: retryMax,
			BaseDelay:   500 * time.Millisecond,
		})
		w6WSReadTimeout = wsReadTimeout
		w6WSWriteTimeout = wsWrite
		log.Printf("[w6] init: base=%q key=%s http_timeout=%s ws_read_timeout=%s ws_handshake=%s ws_write=%s ok",
			baseURL, maskKey(apiKey), httpTimeout, wsReadTimeout, wsHandshake, wsWrite)
	})
}

func buildW6NewsPrompt(companyName, symbol, currentDate string) string {
	return fmt.Sprintf(`你是 A 股新闻调研员。请对标的「%s（%s）」做实时联网调研，覆盖截止 %s 的最近 7 个自然日。

调研目标：
1. 个股层面：公司公告、业绩预告、重大事项、管理层变动、监管问询/处罚、股东增持减持、回购、分红、限售解禁、产品/业务进展、诉讼。
2. 行业层面：所在行业的政策、供需、价格、竞争格局变化。
3. 宏观层面：影响该股的宏观/资金面事件（利率、汇率、北向、行业政策）。

输出要求（Markdown，严格按此结构，不要加额外标题）：
## 个股新闻
- [YYYY-MM-DD] 来源：xxx | 标题：xxx | 摘要：一句话
（按时间倒序，最多 15 条；只列真实可溯源的，找不到就写"近7日无明显个股新闻"）

## 行业与宏观
- [YYYY-MM-DD] 板块/主题：xxx | 事件：xxx | 对该股方向：利好/利空/中性

## 关键事件时间线
| 日期 | 事件 | 来源 | 影响方向 |
|---|---|---|---|

## 数据缺口声明
（明确写出哪些维度没查到、是否因风控/无公开信息，不要编造）

硬性规则：
- 每条必须带可溯源的来源（媒体名/网站/公告来源）和日期；无来源的不写。
- 区分"事实"与"市场传闻"，传闻单独标注。
- 不要做投资建议、不要给目标价/止损价、不要写 VERDICT——你只负责新闻素材。
- 当前日期是 %s，不是未来日期，按此为"今天"界定最近7日。`, companyName, symbol, currentDate, currentDate)
}

var w6FirstSectionRe = regexp.MustCompile(`(?s)^.*?(## 个股新闻)`)

func trimBeforeFirstSection(md string) string {
	loc := w6FirstSectionRe.FindStringSubmatchIndex(md)
	if loc == nil {
		return md
	}
	// loc[2] is the start index of the "## 个股新闻" capture group.
	return md[loc[2]:]
}

// FetchW6NewsResearch 调用 W6 联网 agent 对指定 A 股标的做最近 7 日新闻/行业/宏观调研。
// 配置缺失或调用失败时返回中文降级 Markdown 与 nil error，避免新闻 agent 卡死。
// 该函数为预取入口：在工作流 graph 启动前由 RunTradingWorkflow 调用，
// 结果注入到新闻分析师的 instruction 中，避免在工作流预算内被 W6 的 ~12m 调用吃掉。
func FetchW6NewsResearch(ctx context.Context, companyName, symbol, currentDate string) (string, error) {
	initW6Client()
	if w6InitErr != nil {
		out := fmt.Sprintf("# W6新闻调研\n\n⚠️ W6 网关未配置：%v。已降级，请改用 get_news / get_global_news 补充新闻。", w6InitErr)
		log.Printf("[w6] return final_md_len=%d degraded=true (init_err=%v)", len(out), w6InitErr)
		return out, nil
	}
	if w6ProviderInst == nil || w6ProviderInst.Upstream() == nil {
		out := "# W6新闻调研\n\n⚠️ W6 网关未初始化。已降级，请改用 get_news / get_global_news 补充新闻。"
		log.Printf("[w6] return final_md_len=%d degraded=true (client_nil)", len(out))
		return out, nil
	}

	log.Printf("[w6] stream start company=%s symbol=%s date=%s timeout=%s",
		companyName, symbol, currentDate, w6NewsStreamTimeout)

	streamCtx, cancel := context.WithTimeout(ctx, w6NewsStreamTimeout)
	defer cancel()

	prompt := buildW6NewsPrompt(companyName, symbol, currentDate)
	content, artifactIDs, err := w6RunSession(streamCtx, prompt)

	log.Printf("[w6] stream done err=%v content_len=%d artifacts=%d",
		err, len(content), len(artifactIDs))

	if err != nil {
		out := fmt.Sprintf("# W6新闻调研\n\n⚠️ W6 调研会话超时/失败：%v。已降级，请改用 get_news / get_global_news 补充新闻。", err)
		log.Printf("[w6] return final_md_len=%d degraded=true (stream_err=%v)", len(out), err)
		return out, nil
	}

	md := strings.TrimSpace(content)
	if md == "" {
		out := "# W6新闻调研\n\n⚠️ W6 调研返回空内容。已降级，请改用 get_news / get_global_news 补充新闻。"
		log.Printf("[w6] return final_md_len=%d degraded=true (empty_content)", len(out))
		return out, nil
	}

	upstream := w6ProviderInst.Upstream()
	if upstream != nil && len(artifactIDs) > 0 {
		seen := make(map[string]struct{}, len(artifactIDs))
		for _, id := range artifactIDs {
			if _, ok := seen[id]; ok {
				continue
			}
			seen[id] = struct{}{}
			body, _, dlErr := upstream.DownloadSource(ctx, id)
			dlBytes := 0
			if dlErr == nil {
				dlBytes = len(body)
			}
			log.Printf("[w6] artifact id=%s dl_err=%v bytes=%d", id, dlErr, dlBytes)
			if dlErr != nil || len(body) == 0 {
				continue
			}
			md += "\n\n" + string(body)
		}
	}

	cleaned := trimBeforeFirstSection(md)
	out := "# W6新闻调研\n\n" + cleaned
	degraded := strings.HasPrefix(strings.TrimSpace(out), "⚠️") || strings.Contains(out, "⚠️")
	log.Printf("[w6] return final_md_len=%d degraded=%v", len(out), degraded)
	return out, nil
}

// W6NewsResearch 是工具可调用入口的兼容包装：直接委托给 FetchW6NewsResearch。
// 保留是为了 w6_live_test.go 以及未来可能在其它上下文中复用该工具。
func W6NewsResearch(ctx context.Context, companyName, symbol, currentDate string) (string, error) {
	return FetchW6NewsResearch(ctx, companyName, symbol, currentDate)
}

// w6RunSession dials the W6 gateway with a fresh client-generated session id,
// completes the handshake (the gateway echoes our id in state.id + sends
// status=idle), sends the user prompt, then accumulates the assistant's reply
// until the upstream goes idle again.
//
// Two important behaviors discovered via live probing:
//   - Dialing with an EMPTY id makes the gateway attach to some pre-existing
//     OSINT session (with leftover title/todos/messages), contaminating output.
//     Generating a fresh id makes the gateway create a clean new session.
//   - The provider's Stream handshake requires a non-empty state.id, so it can
//     only work with the fresh-id flow. We bypass provider.Stream and drive the
//     WS directly here to control both the id generation and the content
//     selection (we prefer the final assistant message over the raw thinking
//     trace).
func w6RunSession(ctx context.Context, prompt string) (string, []string, error) {
	upstream := w6ProviderInst.Upstream()
	// Client-generated fresh session id. The gateway creates a new OSINT
	// session with this id; reusing an empty id would attach to an existing
	// session and contaminate the output.
	sessionID := fmt.Sprintf("w6news_%d", time.Now().UnixNano())
	conn, err := upstream.DialSession(ctx, sessionID)
	if err != nil {
		return "", nil, fmt.Errorf("ws dial: %w", err)
	}
	defer conn.Close()
	go func() { <-ctx.Done(); _ = conn.Close() }()

	var thinking strings.Builder
	var lastAssistant string
	artifactIDs := make([]string, 0)
	inputSent := false
	handshakeReady := false
	frameCount := 0
	updateFrames, statusFrames, thinkingFrames := 0, 0, 0
	lastStatus := ""
	started := time.Now()

	for {
		if ctx.Err() != nil {
			return "", nil, ctx.Err()
		}
		_ = conn.SetReadDeadline(time.Now().Add(w6WSReadTimeout))
		_, msg, rerr := conn.ReadMessage()
		if rerr != nil {
			// If we already have content, treat the read error as a graceful end
			// (upstream closed after delivering the reply).
			if thinking.Len() > 0 || lastAssistant != "" {
				log.Printf("[w6] ws read ended early (treated as done) frames=%d updates=%d thinkings=%d statuses=%d last_status=%q elapsed=%s err=%v",
					frameCount, updateFrames, thinkingFrames, statusFrames, lastStatus, time.Since(started), rerr)
				break
			}
			return "", nil, fmt.Errorf("ws read: %w", rerr)
		}
		frameCount++
		var frame map[string]any
		if json.Unmarshal(msg, &frame) != nil {
			continue
		}
		typeLower := strings.ToLower(w6ToString(frame["type"]))

		switch typeLower {
		case "error":
			errMsg := w6ToString(frame["error"])
			if errMsg == "" {
				errMsg = "upstream error"
			}
			return "", nil, fmt.Errorf("upstream: %s", errMsg)

		case "update":
			updateFrames++
			if a := w6ExtractAssistant(frame["messages"]); a != "" {
				lastAssistant = a
			}
			for _, a := range w6ExtractArtifactIDs(frame) {
				artifactIDs = append(artifactIDs, a)
			}

		case "status":
			statusFrames++
			st := strings.ToLower(w6ToString(frame["status"]))
			lastStatus = st
			if st != "idle" {
				continue
			}
			if !handshakeReady {
				// Initial handshake complete: gateway echoed our session id and
				// is now idle, waiting for our input frame. Send it.
				handshakeReady = true
				if !inputSent {
					inputFrame := map[string]any{
						"type":        "input",
						"id":          sessionID,
						"content":     prompt,
						"attachments": []string{},
					}
					_ = conn.SetWriteDeadline(time.Now().Add(w6WSWriteTimeout))
					if werr := conn.WriteJSON(inputFrame); werr != nil {
						return "", nil, fmt.Errorf("ws input write: %w", werr)
					}
					inputSent = true
					log.Printf("[w6] input sent session=%q prompt_len=%d after %s",
						sessionID, len(prompt), time.Since(started))
				}
				continue
			}
			// Post-input idle: run complete.
			if inputSent && (thinking.Len() > 0 || lastAssistant != "") {
				log.Printf("[w6] upstream idle (done) session=%q frames=%d updates=%d thinkings=%d statuses=%d elapsed=%s",
					sessionID, frameCount, updateFrames, thinkingFrames, statusFrames, time.Since(started))
				goto done
			}

		case "thinking":
			thinkingFrames++
			chunk := w6ToString(frame["content"])
			if chunk != "" {
				thinking.WriteString(chunk)
			}
		}
	}

done:
	// Prefer the final assistant message (the structured markdown report) over
	// the raw thinking trace. Fall back to thinking only if no assistant message
	// was ever delivered.
	content := lastAssistant
	if content == "" {
		content = thinking.String()
	}
	return content, artifactIDs, nil
}

// w6ToString returns v as a string, ignoring non-string values.
func w6ToString(v any) string {
	if s, ok := v.(string); ok {
		return s
	}
	return ""
}

// w6ExtractAssistant pulls the assistant's text content out of an update
// frame's messages array (last assistant-like message wins).
func w6ExtractAssistant(messages any) string {
	list, ok := messages.([]any)
	if !ok {
		return ""
	}
	for i := len(list) - 1; i >= 0; i-- {
		msg, ok := list[i].(map[string]any)
		if !ok {
			continue
		}
		role := strings.ToLower(w6ToString(msg["role"]))
		kind := strings.ToLower(w6ToString(msg["kind"]))
		isAssistant := role == "assistant" || role == "ai" || role == "bot" || role == "model" ||
			kind == "user_facing" || kind == "assistant" || kind == "from_assistant"
		if !isAssistant {
			continue
		}
		if c := w6ExtractText(msg["content"]); c != "" {
			return c
		}
		if t := w6ExtractText(msg["text"]); t != "" {
			return t
		}
		if parts, ok := msg["message_parts"].([]any); ok {
			var b strings.Builder
			for _, p := range parts {
				part, ok := p.(map[string]any)
				if !ok {
					continue
				}
				if strings.ToLower(w6ToString(part["type"])) == "text" {
					b.WriteString(w6ExtractText(part["content"]))
				}
			}
			if b.Len() > 0 {
				return b.String()
			}
		}
	}
	return ""
}

func w6ExtractText(v any) string {
	switch t := v.(type) {
	case string:
		return strings.TrimSpace(t)
	case []any:
		var b strings.Builder
		for _, item := range t {
			if s := w6ExtractText(item); s != "" {
				b.WriteString(s)
			}
		}
		return strings.TrimSpace(b.String())
	case map[string]any:
		for _, key := range []string{"text", "content", "value", "output"} {
			if val, ok := t[key]; ok {
				if s := w6ExtractText(val); s != "" {
					return s
				}
			}
		}
		return ""
	default:
		return ""
	}
}

// w6ExtractArtifactIDs pulls resource artifact ids from an update frame's
// messages[].message_parts[] (type=resource).
func w6ExtractArtifactIDs(frame map[string]any) []string {
	rawMessages, ok := frame["messages"].([]any)
	if !ok {
		return nil
	}
	seen := map[string]struct{}{}
	out := make([]string, 0)
	for _, raw := range rawMessages {
		msg, ok := raw.(map[string]any)
		if !ok {
			continue
		}
		parts, _ := msg["message_parts"].([]any)
		for _, rawPart := range parts {
			part, ok := rawPart.(map[string]any)
			if !ok || strings.ToLower(w6ToString(part["type"])) != "resource" {
				continue
			}
			resource, ok := part["resource"].(map[string]any)
			if !ok {
				continue
			}
			id := strings.TrimSpace(w6ToString(resource["id"]))
			if id == "" {
				continue
			}
			if _, exists := seen[id]; exists {
				continue
			}
			seen[id] = struct{}{}
			out = append(out, id)
		}
	}
	return out
}

// NewW6NewsResearchTool 创建 W6 联网新闻调研 eino 工具。
func NewW6NewsResearchTool() tool.InvokableTool {
	type Input struct {
		CompanyName string `json:"company_name" jsonschema_description:"公司名称，如 贵州茅台"`
		Symbol      string `json:"symbol" jsonschema_description:"股票代码，如 600519.SH"`
		CurrentDate string `json:"current_date" jsonschema_description:"当前日期 YYYY-MM-DD"`
	}

	t, err := utils.InferTool(
		"w6_news_research",
		"调用 W6 联网 agent 对指定 A 股标的做最近7日新闻/行业/宏观调研，返回 Markdown 素材",
		func(ctx context.Context, input *Input) (string, error) {
			return W6NewsResearch(ctx, input.CompanyName, input.Symbol, input.CurrentDate)
		},
	)
	if err != nil {
		log.Fatalf("创建 w6_news_research 工具失败: %v", err)
	}
	return t
}
