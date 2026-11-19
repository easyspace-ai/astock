package tools

import (
	"context"
	"sync"
	"testing"
)

// TestIsolatedSchemaCopies 保证每个 agent 绑定到的 schema 是独立副本：
// 修改其中一个不会影响其它，且 required 始终唯一。
func TestIsolatedSchemaCopies(t *testing.T) {
	if err := InitGlobalTools(t.TempDir()); err != nil {
		t.Fatalf("InitGlobalTools: %v", err)
	}
	ctx := context.Background()

	const agents = 8
	infos := make([][]string, agents)
	var wg sync.WaitGroup
	for i := 0; i < agents; i++ {
		wg.Add(1)
		go func(idx int) {
			defer wg.Done()
			for _, bt := range GetGlobalTools() {
				info, err := bt.Info(ctx)
				if err != nil || info == nil || info.ParamsOneOf == nil {
					continue
				}
				js, err := info.ParamsOneOf.ToJSONSchema()
				if err != nil || js == nil {
					continue
				}
				// 模拟客户端绑定时的原地排序/改写
				for j := len(js.Required) - 1; j > 0; j-- {
					js.Required[j], js.Required[j-1] = js.Required[j-1], js.Required[j]
				}
				if info.Name == "get_stock_data" {
					infos[idx] = append([]string(nil), js.Required...)
				}
			}
		}(i)
	}
	wg.Wait()

	for i, req := range infos {
		seen := map[string]bool{}
		for _, r := range req {
			if seen[r] {
				t.Fatalf("agent %d: duplicate required %v", i, req)
			}
			seen[r] = true
		}
		if len(req) != 3 {
			t.Fatalf("agent %d: required=%v, want 3 fields", i, req)
		}
	}
}
