package httpapi

import (
	"encoding/json"
	"fmt"
	"net/http"
)

// writeSSE 写出与 Python _sse_pack 一致的 SSE 帧：event + JSON data。
// 客户端断开后 Flush 可能 panic（gin ResponseWriter 底层已关闭），必须 recover，
// 否则会拖垮整个 aistock 进程，浏览器表现为 Network Error。
func writeSSE(w http.ResponseWriter, flusher http.Flusher, event string, data any) (err error) {
	defer func() {
		if r := recover(); r != nil {
			err = fmt.Errorf("sse write aborted: %v", r)
		}
	}()
	b, err := json.Marshal(data)
	if err != nil {
		return err
	}
	if _, err := fmt.Fprintf(w, "event: %s\ndata: %s\n\n", event, b); err != nil {
		return err
	}
	if flusher != nil {
		flusher.Flush()
	}
	return nil
}

// writeSSEKeepalive 写 SSE 注释行，刷新空闲超时（代理/LB 常见 60–180s 无字节即断连）。
// 浏览器 EventSource/fetch 解析器会忽略 ":" 开头的注释。
func writeSSEKeepalive(w http.ResponseWriter, flusher http.Flusher) (err error) {
	defer func() {
		if r := recover(); r != nil {
			err = fmt.Errorf("sse keepalive aborted: %v", r)
		}
	}()
	if _, err := fmt.Fprintf(w, ": keepalive\n\n"); err != nil {
		return err
	}
	if flusher != nil {
		flusher.Flush()
	}
	return nil
}

// writeSSEDone 结束流：与主站 chat_completions 一致
func writeSSEDone(w http.ResponseWriter, flusher http.Flusher) {
	defer func() { _ = recover() }()
	_, _ = fmt.Fprintf(w, "event: done\ndata: [DONE]\n\n")
	if flusher != nil {
		flusher.Flush()
	}
}
