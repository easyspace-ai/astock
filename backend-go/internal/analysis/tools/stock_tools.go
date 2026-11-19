package tools

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net/http"
	"net/url"
	"os"
	"strconv"
	"strings"
	"time"

	"github.com/cloudwego/eino/components/tool"
	"github.com/cloudwego/eino/components/tool/utils"
	"github.com/easyspace-ai/bystock/pkg/tsdb"
	"golang.org/x/text/encoding/simplifiedchinese"
)

// StockTools 封装 stockdb 为 eino Tools。
//
// 数据来源策略：TickFlow/Panel（短超时 fail-fast）→ 腾讯前复权 → 新浪 →
// 本地 tsdb（只读，不触发东财 live sync）。本地无数据时快速失败。
type StockTools struct {
	client *tsdb.UnifiedClient

	httpClient  *http.Client
	lastCall    time.Time
	minInterval time.Duration
}

// panelAPIURL 返回 Panel FastAPI 根地址（默认本机 7300）。
func panelAPIURL() string {
	if v := strings.TrimSpace(os.Getenv("PANEL_API_URL")); v != "" {
		return strings.TrimRight(v, "/")
	}
	return "http://127.0.0.1:7300"
}

// panelAPIToken 回源 Panel 的可选 Bearer Token（PANEL_API_TOKEN）；为空则不带鉴权头。
func panelAPIToken() string {
	return strings.TrimSpace(os.Getenv("PANEL_API_TOKEN"))
}

// panelKlineTimeout Panel /api/kline/daily 超时（默认 4s，可用 PANEL_KLINE_TIMEOUT_MS 覆盖）。
func panelKlineTimeout() time.Duration {
	const def = 4 * time.Second
	v := strings.TrimSpace(os.Getenv("PANEL_KLINE_TIMEOUT_MS"))
	if v == "" {
		return def
	}
	ms, err := strconv.Atoi(v)
	if err != nil || ms <= 0 {
		return def
	}
	return time.Duration(ms) * time.Millisecond
}

// tsdbFallbackTimeout 末级 tsdb 查询超时（默认 5s，避免卡死）。
func tsdbFallbackTimeout() time.Duration {
	const def = 5 * time.Second
	v := strings.TrimSpace(os.Getenv("TSDB_FALLBACK_TIMEOUT_MS"))
	if v == "" {
		return def
	}
	ms, err := strconv.Atoi(v)
	if err != nil || ms <= 0 {
		return def
	}
	return time.Duration(ms) * time.Millisecond
}

// toTSCodeSymbol 规范为 Panel 使用的 ts_code（如 601555.SH）。
func toTSCodeSymbol(tsCode string) (string, error) {
	code6, prefix, ok := splitTSCode(tsCode)
	if !ok {
		return "", fmt.Errorf("无效的股票代码: %s", tsCode)
	}
	return code6 + "." + strings.ToUpper(prefix), nil
}

// jsonFloat 从 JSON number/string 容错解析 float64。
func jsonFloat(v any) float64 {
	switch x := v.(type) {
	case float64:
		return x
	case json.Number:
		f, _ := x.Float64()
		return f
	case string:
		return atof(x)
	case int:
		return float64(x)
	case int64:
		return float64(x)
	default:
		return 0
	}
}

// jsonDate 将 date 字段规范为 YYYY-MM-DD。
func jsonDate(v any) string {
	switch x := v.(type) {
	case string:
		return datePrefix(normalizeDate(x))
	default:
		return datePrefix(normalizeDate(fmt.Sprint(x)))
	}
}

// NewStockTools 创建股票数据工具集
func NewStockTools(dataDir string) (*StockTools, error) {
	// readonly：末级兜底只读本地 lake，不触发 StockSDK/东财 live sync（旧 lake 上 sync 易 EOF 拖死）。
	client, err := tsdb.NewUnifiedClient(tsdb.UnifiedConfig{
		PrimaryDataSource: tsdb.DataSourceStockSDK,
		DataDir:           dataDir,
		CacheMode:         tsdb.CacheModeReadOnly,
	})
	if err != nil {
		return nil, fmt.Errorf("create stockdb client: %w", err)
	}
	return &StockTools{
		client:      client,
		httpClient:  &http.Client{Timeout: 15 * time.Second, Transport: ipv4Transport()},
		minInterval: 800 * time.Millisecond,
	}, nil
}

// Close 关闭客户端
func (s *StockTools) Close() error {
	if s.client != nil {
		return s.client.Close()
	}
	return nil
}

// formatDataFrame 将 DataFrame 格式化为字符串供 LLM 使用
func formatDataFrame(df *tsdb.DataFrame) string {
	if df == nil || len(df.Rows) == 0 {
		return "无数据"
	}

	var sb strings.Builder
	sb.WriteString(fmt.Sprintf("共 %d 条数据，列：%v\n\n", len(df.Rows), df.Columns))

	// 最多显示 20 行
	limit := len(df.Rows)
	if limit > 20 {
		limit = 20
	}

	for i, row := range df.Rows {
		if i >= limit {
			sb.WriteString(fmt.Sprintf("...（省略 %d 条）\n", len(df.Rows)-limit))
			break
		}
		sb.WriteString(fmt.Sprintf("%d. %v\n", i+1, row))
	}

	return sb.String()
}

// ============================================================================
// HTTP 实时数据获取（腾讯财经 / 新浪财经）
// ============================================================================

// httpGet 执行 GET 请求，带轻量限流与默认 UA。
func (s *StockTools) httpGet(url string) ([]byte, error) {
	s.throttle()

	req, err := http.NewRequestWithContext(context.Background(), "GET", url, nil)
	if err != nil {
		return nil, err
	}
	req.Header.Set("User-Agent", "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36")

	resp, err := s.httpClient.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()

	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, err
	}
	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("http %d: %s", resp.StatusCode, truncate(string(body), 120))
	}
	return body, nil
}

// throttle 串行限流，避免高频触发源站防封
func (s *StockTools) throttle() {
	elapsed := time.Since(s.lastCall)
	if elapsed < s.minInterval {
		time.Sleep(s.minInterval - elapsed + 200*time.Millisecond)
	}
	s.lastCall = time.Now()
}

func truncate(s string, n int) string {
	if len(s) <= n {
		return s
	}
	return s[:n] + "..."
}

// datePrefix returns the first 10 bytes of s (YYYY-MM-DD) when long enough.
// Empty or short strings are returned unchanged to avoid slice panics.
func datePrefix(s string) string {
	if len(s) >= 10 {
		return s[:10]
	}
	return s
}

// sinaMarketPrefix 返回新浪/腾讯市场前缀（sh / sz / bj）
func sinaMarketPrefix(code6 string) string {
	switch {
	case strings.HasPrefix(code6, "6"), strings.HasPrefix(code6, "9"):
		return "sh"
	case strings.HasPrefix(code6, "8"):
		return "bj"
	default:
		return "sz"
	}
}

// splitTSCode 将 ts_code (如 600519.SH) 拆分为纯 6 位代码与市场前缀。
func splitTSCode(tsCode string) (code6, prefix string, ok bool) {
	c := strings.TrimSpace(strings.ToUpper(tsCode))
	if c == "" {
		return "", "", false
	}
	for _, suffix := range []string{".SH", ".SZ", ".BJ"} {
		if strings.HasSuffix(c, suffix) {
			c = strings.TrimSuffix(c, suffix)
			break
		}
	}
	// 去掉可能的前缀 sh/sz/bj
	for _, p := range []string{"SH", "SZ", "BJ"} {
		if strings.HasPrefix(c, p) {
			c = strings.TrimPrefix(c, p)
			break
		}
	}
	if len(c) != 6 {
		return "", "", false
	}
	return c, sinaMarketPrefix(c), true
}

// normalizeDate 将 YYYYMMDD 转为 YYYY-MM-DD；若已是 YYYY-MM-DD 则原样返回。
func normalizeDate(d string) string {
	d = strings.TrimSpace(d)
	if len(d) == 8 && strings.Count(d, "-") == 0 {
		return d[:4] + "-" + d[4:6] + "-" + d[6:8]
	}
	return d
}

// ---- K 线（日线，前复权）----

// klineRow 一条日线数据
type klineRow struct {
	Date   string
	Open   float64
	Close  float64
	High   float64
	Low    float64
	Volume float64
}

// fetchKlinePanel 本机 Panel GET /api/kline/daily（enriched 前复权日 K）。
// 对齐 frontend/backend：symbol + start_date/end_date（YYYY-MM-DD），rows[].date/OHLCV。
func (s *StockTools) fetchKlinePanel(tsCode, startDate, endDate string) ([]klineRow, error) {
	symbol, err := toTSCodeSymbol(tsCode)
	if err != nil {
		return nil, err
	}
	start := normalizeDate(startDate)
	end := normalizeDate(endDate)

	q := url.Values{}
	q.Set("symbol", symbol)
	if start != "" {
		q.Set("start_date", start)
	}
	if end != "" {
		q.Set("end_date", end)
	} else if start == "" {
		q.Set("days", "320")
	}

	endpoint := panelAPIURL() + "/api/kline/daily?" + q.Encode()
	timeout := panelKlineTimeout()
	ctx, cancel := context.WithTimeout(context.Background(), timeout)
	defer cancel()

	req, err := http.NewRequestWithContext(ctx, http.MethodGet, endpoint, nil)
	if err != nil {
		return nil, err
	}
	req.Header.Set("User-Agent", "aistock-sidecar/get_stock_data")
	req.Header.Set("Accept", "application/json")
	if tok := panelAPIToken(); tok != "" {
		req.Header.Set("Authorization", "Bearer "+tok)
	}

	// 短超时 fail-fast：Panel/东财 sync 慢时立刻回退腾讯，不走外网 throttle。
	panelClient := &http.Client{
		Timeout:   timeout,
		Transport: ipv4Transport(),
	}
	if s.httpClient != nil && s.httpClient.Transport != nil {
		panelClient.Transport = s.httpClient.Transport
	}

	log.Printf("get_stock_data source=TickFlow/panel try tsCode=%s timeout=%s", tsCode, timeout)
	resp, err := panelClient.Do(req)
	if err != nil {
		return nil, fmt.Errorf("timeout=%s: %w", timeout, err)
	}
	defer resp.Body.Close()
	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, err
	}
	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("panel http %d: %s", resp.StatusCode, truncate(string(body), 160))
	}

	var raw struct {
		Symbol string           `json:"symbol"`
		Source string           `json:"source"`
		Rows   []map[string]any `json:"rows"`
	}
	if err := json.Unmarshal(body, &raw); err != nil {
		return nil, fmt.Errorf("解析 Panel K线响应: %w", err)
	}
	if len(raw.Rows) == 0 {
		return nil, fmt.Errorf("Panel K线返回空 rows (source=%s)", raw.Source)
	}

	rows := make([]klineRow, 0, len(raw.Rows))
	for _, item := range raw.Rows {
		d := jsonDate(item["date"])
		if d == "" {
			continue
		}
		rows = append(rows, klineRow{
			Date:   d,
			Open:   jsonFloat(item["open"]),
			High:   jsonFloat(item["high"]),
			Low:    jsonFloat(item["low"]),
			Close:  jsonFloat(item["close"]),
			Volume: jsonFloat(item["volume"]),
		})
	}
	if len(rows) == 0 {
		return nil, fmt.Errorf("Panel K线无可用条目")
	}
	return rows, nil
}

// fetchKlineTencent 腾讯前复权日 K 线。返回按日期升序的切片。
// 接口: https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param=<symbol>,day,<start>,<end>,<count>,qfq
// 响应: data.<symbol>.qfqday = [[date, open, close, high, low, volume], ...]
func (s *StockTools) fetchKlineTencent(prefix, code6, start, end string, count int) ([]klineRow, error) {
	if count <= 0 {
		count = 250
	}
	symbol := prefix + code6
	param := fmt.Sprintf("%s,day,%s,%s,%d,qfq", symbol, start, end, count)
	url := "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param=" + param

	body, err := s.httpGet(url)
	if err != nil {
		return nil, err
	}

	var raw struct {
		Code int `json:"code"`
		Data map[string]struct {
			QfqDay [][]string `json:"qfqday"`
			Day    [][]string `json:"day"`
		} `json:"data"`
	}
	if err := json.Unmarshal(body, &raw); err != nil {
		return nil, fmt.Errorf("解析腾讯K线响应: %w", err)
	}
	if raw.Code != 0 {
		return nil, fmt.Errorf("腾讯K线接口返回码 %d", raw.Code)
	}

	entry, ok := raw.Data[symbol]
	if !ok {
		return nil, fmt.Errorf("腾讯K线响应缺少 %s", symbol)
	}
	arr := entry.QfqDay
	if len(arr) == 0 {
		// 个别品种无前复权数据时退回到原始日 K
		arr = entry.Day
	}
	if len(arr) == 0 {
		return nil, fmt.Errorf("腾讯K线返回空数据")
	}

	rows := make([]klineRow, 0, len(arr))
	for _, item := range arr {
		if len(item) < 6 {
			continue
		}
		r := klineRow{Date: item[0]}
		r.Open, _ = strconv.ParseFloat(item[1], 64)
		r.Close, _ = strconv.ParseFloat(item[2], 64)
		r.High, _ = strconv.ParseFloat(item[3], 64)
		r.Low, _ = strconv.ParseFloat(item[4], 64)
		r.Volume, _ = strconv.ParseFloat(item[5], 64)
		rows = append(rows, r)
	}
	if len(rows) == 0 {
		return nil, fmt.Errorf("腾讯K线无可用条目")
	}
	return rows, nil
}

// fetchKlineSina 新浪日 K 线（不复权）。返回按日期升序的切片。
// 接口: https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData
// 响应: [{"day":"...","open":"...","high":"...","low":"...","close":"...","volume":"..."}, ...]
func (s *StockTools) fetchKlineSina(prefix, code6, start, end string, datalen int) ([]klineRow, error) {
	if datalen <= 0 {
		datalen = 250
	}
	symbol := prefix + code6
	base := "https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData"
	q := "?symbol=" + symbol + "&scale=240&ma=no&datalen=" + strconv.Itoa(datalen)
	if start != "" {
		q += "&start=" + start
	}
	if end != "" {
		q += "&end=" + end
	}

	body, err := s.httpGet(base + q)
	if err != nil {
		return nil, err
	}
	bodyStr := strings.TrimSpace(string(body))
	if bodyStr == "" || bodyStr == "[]" {
		return nil, fmt.Errorf("新浪K线返回空")
	}

	var items []struct {
		Day    string `json:"day"`
		Open   string `json:"open"`
		High   string `json:"high"`
		Low    string `json:"low"`
		Close  string `json:"close"`
		Volume string `json:"volume"`
	}
	if err := json.Unmarshal(body, &items); err != nil {
		return nil, fmt.Errorf("解析新浪K线响应: %w", err)
	}
	if len(items) == 0 {
		return nil, fmt.Errorf("新浪K线返回空数据")
	}

	rows := make([]klineRow, 0, len(items))
	for _, it := range items {
		r := klineRow{Date: it.Day}
		r.Open, _ = strconv.ParseFloat(it.Open, 64)
		r.High, _ = strconv.ParseFloat(it.High, 64)
		r.Low, _ = strconv.ParseFloat(it.Low, 64)
		r.Close, _ = strconv.ParseFloat(it.Close, 64)
		r.Volume, _ = strconv.ParseFloat(it.Volume, 64)
		rows = append(rows, r)
	}
	return rows, nil
}

// filterKlineByDate 按起止日期（YYYY-MM-DD）过滤，返回满足 start<=date<=end 的切片。
func filterKlineByDate(rows []klineRow, start, end string) []klineRow {
	if start == "" && end == "" {
		return rows
	}
	out := rows[:0]
	for _, r := range rows {
		if start != "" && r.Date < start {
			continue
		}
		if end != "" && r.Date > end {
			continue
		}
		out = append(out, r)
	}
	return out
}

// formatKlineTable 将 K 线渲染为 Markdown 表格，最多 maxRows 行（取最近若干条）。
func formatKlineTable(rows []klineRow, maxRows int) string {
	if len(rows) == 0 {
		return "无数据"
	}
	if maxRows <= 0 {
		maxRows = 60
	}

	var sb strings.Builder
	sb.WriteString(fmt.Sprintf("共 %d 条数据，列：[date, open, high, low, close, volume]\n\n", len(rows)))
	sb.WriteString("| 日期 | 开盘 | 最高 | 最低 | 收盘 | 成交量 |\n|------|------|------|------|------|--------|\n")

	start := 0
	omitted := 0
	if len(rows) > maxRows {
		start = len(rows) - maxRows
		omitted = start
	}
	for _, r := range rows[start:] {
		sb.WriteString(fmt.Sprintf("| %s | %.2f | %.2f | %.2f | %.2f | %.0f |\n",
			r.Date, r.Open, r.High, r.Low, r.Close, r.Volume))
	}
	if omitted > 0 {
		sb.WriteString(fmt.Sprintf("\n（已省略最早 %d 条，仅展示最近 %d 条）\n", omitted, maxRows))
	}
	return sb.String()
}

// fetchKlineHTTP 顺序：TickFlow/Panel（短超时）→ 腾讯前复权 → 新浪。返回 (rows, source, error)。
func (s *StockTools) fetchKlineHTTP(tsCode, startDate, endDate string) ([]klineRow, string, error) {
	code6, prefix, ok := splitTSCode(tsCode)
	if !ok {
		return nil, "", fmt.Errorf("无效的股票代码: %s", tsCode)
	}
	start := normalizeDate(startDate)
	end := normalizeDate(endDate)

	var errs []string

	// 1) TickFlow/Panel enriched 日 K（前复权，短超时 fail-fast）
	if rows, err := s.fetchKlinePanel(tsCode, startDate, endDate); err == nil {
		filtered := filterKlineByDate(rows, start, end)
		if len(filtered) > 0 {
			log.Printf("get_stock_data source=TickFlow/panel ok tsCode=%s rows=%d", tsCode, len(filtered))
			return filtered, "TickFlow/Panel(前复权)", nil
		}
		errs = append(errs, "TickFlow/panel: empty after date filter")
		log.Printf("get_stock_data source=TickFlow/panel fail tsCode=%s: empty after date filter，回退 tencent", tsCode)
	} else {
		errs = append(errs, fmt.Sprintf("TickFlow/panel: %v", err))
		log.Printf("get_stock_data source=TickFlow/panel fail tsCode=%s: %v，回退 tencent", tsCode, err)
	}

	// 2) 腾讯前复权日 K（原 newstock 主路径）
	log.Printf("get_stock_data source=tencent try tsCode=%s", tsCode)
	if rows, err := s.fetchKlineTencent(prefix, code6, start, end, 320); err == nil {
		filtered := filterKlineByDate(rows, start, end)
		if len(filtered) > 0 {
			log.Printf("get_stock_data source=tencent ok tsCode=%s rows=%d", tsCode, len(filtered))
			return filtered, "腾讯财经(前复权)", nil
		}
		errs = append(errs, "tencent: empty after date filter")
		log.Printf("get_stock_data source=tencent fail tsCode=%s: empty after date filter，回退 sina", tsCode)
	} else {
		errs = append(errs, fmt.Sprintf("tencent: %v", err))
		log.Printf("get_stock_data source=tencent fail tsCode=%s: %v，回退 sina", tsCode, err)
	}

	// 3) 新浪不复权日 K
	log.Printf("get_stock_data source=sina try tsCode=%s", tsCode)
	if rows, err := s.fetchKlineSina(prefix, code6, start, end, 320); err == nil {
		filtered := filterKlineByDate(rows, start, end)
		if len(filtered) > 0 {
			log.Printf("get_stock_data source=sina ok tsCode=%s rows=%d", tsCode, len(filtered))
			return filtered, "新浪财经(不复权)", nil
		}
		errs = append(errs, "sina: empty after date filter")
		log.Printf("get_stock_data source=sina fail tsCode=%s: empty after date filter", tsCode)
	} else {
		errs = append(errs, fmt.Sprintf("sina: %v", err))
		log.Printf("get_stock_data source=sina fail tsCode=%s: %v", tsCode, err)
	}

	return nil, "", fmt.Errorf("HTTP K线接口均失败: %s", strings.Join(errs, "; "))
}

// ---- 基本面快照（PE/PB/市值/换手率）----

// tencentQuote 腾讯实时行情快照字段
type tencentQuote struct {
	Name        string
	Price       float64
	LastClose   float64
	Open        float64
	High        float64
	Low         float64
	TurnoverPct float64
	PETTM       float64
	MCapYi      float64 // 总市值(亿)
	FloatMCapYi float64 // 流通市值(亿)
	PB          float64
	PEStatic    float64
	Valid       bool
}

// fetchTencentQuote 拉取腾讯实时行情。响应为 GBK 编码的 v_sh600519="...~..."。
func (s *StockTools) fetchTencentQuote(prefix, code6 string) (*tencentQuote, error) {
	symbol := prefix + code6
	url := "https://qt.gtimg.cn/q=" + symbol
	body, err := s.httpGet(url)
	if err != nil {
		return nil, err
	}
	// 腾讯接口返回 GBK 编码
	decoded, err := simplifiedchinese.GBK.NewDecoder().Bytes(body)
	if err != nil {
		decoded = body // 解码失败则原样使用
	}

	text := strings.TrimSpace(string(decoded))
	if text == "" {
		return nil, fmt.Errorf("腾讯行情返回空")
	}
	// 形如: v_sh600519="1~贵州茅台~600519~...";
	quoteIdx := strings.Index(text, "\"")
	if quoteIdx < 0 {
		return nil, fmt.Errorf("腾讯行情响应格式异常")
	}
	endIdx := strings.LastIndex(text, "\"")
	if endIdx <= quoteIdx {
		return nil, fmt.Errorf("腾讯行情响应格式异常")
	}
	payload := text[quoteIdx+1 : endIdx]
	vals := strings.Split(payload, "~")
	if len(vals) < 53 {
		return nil, fmt.Errorf("腾讯行情字段数不足: %d", len(vals))
	}

	q := &tencentQuote{Valid: true}
	q.Name = vals[1]
	q.Price = atof(vals[3])
	q.LastClose = atof(vals[4])
	q.Open = atof(vals[5])
	q.High = atof(vals[33])
	q.Low = atof(vals[34])
	q.TurnoverPct = atof(vals[38])
	q.PETTM = atof(vals[39])
	q.MCapYi = atof(vals[44])
	q.FloatMCapYi = atof(vals[45])
	q.PB = atof(vals[46])
	q.PEStatic = atof(vals[52])
	return q, nil
}

// atof 容错字符串转 float64
func atof(s string) float64 {
	if s == "" {
		return 0
	}
	v, _ := strconv.ParseFloat(s, 64)
	return v
}

// ============================================================================
// 对外业务方法
// ============================================================================

// GetStockData 获取股票日线数据（K线，前复权）。
// 顺序：TickFlow/Panel → tencent → sina → tsdb(readonly)。
func (s *StockTools) GetStockData(ctx context.Context, tsCode string, startDate string, endDate string) (string, error) {
	// 主路径：HTTP 实时（Panel 短超时 → 腾讯 → 新浪）
	if rows, src, err := s.fetchKlineHTTP(tsCode, startDate, endDate); err == nil {
		return fmt.Sprintf("股票 %s 日线数据（%s 至 %s，数据来源：%s）：\n%s",
			tsCode, startDate, endDate, src, formatKlineTable(rows, 60)), nil
	} else {
		log.Printf("get_stock_data HTTP 失败 tsCode=%s: %v，回退 tsdb", tsCode, err)
	}

	// 4) 末级：本地 tsdb（readonly，不 sync；带短超时）
	if s.client != nil {
		timeout := tsdbFallbackTimeout()
		log.Printf("get_stock_data source=tsdb try tsCode=%s timeout=%s (readonly, no live sync)", tsCode, timeout)
		tsdbCtx, cancel := context.WithTimeout(ctx, timeout)
		defer cancel()
		if df, err := s.client.GetStockDaily(tsdbCtx, tsCode, startDate, endDate, tsdb.AdjustQFQ); err == nil {
			log.Printf("get_stock_data source=tsdb ok tsCode=%s rows=%d", tsCode, len(df.Rows))
			return fmt.Sprintf("股票 %s 日线数据（%s 至 %s，前复权，本地库）：\n%s",
				tsCode, startDate, endDate, formatDataFrame(df)), nil
		} else {
			log.Printf("get_stock_data source=tsdb fail tsCode=%s: %v", tsCode, err)
		}
	}

	return "", fmt.Errorf("K线行情接口返回异常（表不存在或网络不可达）：无法获取 %s 的日线数据", tsCode)
}

// GetDailyBasic 获取每日基本面数据（PE/PB/市值/换手率）。优先腾讯实时快照，失败回退 tsdb。
func (s *StockTools) GetDailyBasic(ctx context.Context, tsCode string, startDate string, endDate string) (string, error) {
	code6, prefix, ok := splitTSCode(tsCode)
	if !ok {
		return "", fmt.Errorf("无效的股票代码: %s", tsCode)
	}

	// 主路径：腾讯实时行情快照
	if q, err := s.fetchTencentQuote(prefix, code6); err == nil && q.Valid {
		var sb strings.Builder
		sb.WriteString(fmt.Sprintf("股票 %s 实时基本面快照（%s）：\n", tsCode, q.Name))
		sb.WriteString(fmt.Sprintf("现价: %.2f\n", q.Price))
		sb.WriteString(fmt.Sprintf("昨收: %.2f\n", q.LastClose))
		sb.WriteString(fmt.Sprintf("今开: %.2f\n", q.Open))
		sb.WriteString(fmt.Sprintf("最高: %.2f / 最低: %.2f\n", q.High, q.Low))
		sb.WriteString(fmt.Sprintf("PE(动态TTM): %.2f\n", q.PETTM))
		sb.WriteString(fmt.Sprintf("PE(静态): %.2f\n", q.PEStatic))
		sb.WriteString(fmt.Sprintf("PB(市净率): %.2f\n", q.PB))
		sb.WriteString(fmt.Sprintf("总市值: %.2f亿\n", q.MCapYi))
		sb.WriteString(fmt.Sprintf("流通市值: %.2f亿\n", q.FloatMCapYi))
		sb.WriteString(fmt.Sprintf("换手率: %.2f%%\n", q.TurnoverPct))
		sb.WriteString("\n注：以上为腾讯财经实时快照，忽略 start_date/end_date 入参。")
		return sb.String(), nil
	} else {
		log.Printf("get_daily_basic 腾讯快照失败 tsCode=%s: %v，回退 tsdb", tsCode, err)
	}

	// 回退：本地 tsdb
	if s.client != nil {
		if df, err := s.client.GetDailyBasic(ctx, tsCode, startDate, endDate); err == nil {
			return fmt.Sprintf("股票 %s 每日基本面数据（%s 至 %s，本地库）：\n%s",
				tsCode, startDate, endDate, formatDataFrame(df)), nil
		} else {
			log.Printf("get_daily_basic tsdb 回退失败 tsCode=%s: %v", tsCode, err)
		}
	}

	return "", fmt.Errorf("PE/PB估值接口返回异常（表不存在或网络不可达）：无法获取 %s 的基本面数据", tsCode)
}

// GetStockBasic 获取股票基础信息。优先腾讯快照取名称，失败回退 tsdb。
func (s *StockTools) GetStockBasic(ctx context.Context, tsCode string) (string, error) {
	code6, prefix, ok := splitTSCode(tsCode)
	if !ok {
		return "", fmt.Errorf("无效的股票代码: %s", tsCode)
	}

	// 腾讯快照可提供名称与实时行情，但不含行业/上市日期，仅作为轻量补充
	if q, err := s.fetchTencentQuote(prefix, code6); err == nil && q.Valid {
		var sb strings.Builder
		sb.WriteString(fmt.Sprintf("股票 %s 基础信息（腾讯快照）：\n", tsCode))
		sb.WriteString(fmt.Sprintf("名称: %s\n", q.Name))
		sb.WriteString(fmt.Sprintf("代码: %s\n", code6))
		sb.WriteString(fmt.Sprintf("现价: %.2f\n", q.Price))
		sb.WriteString(fmt.Sprintf("总市值: %.2f亿\n", q.MCapYi))
		sb.WriteString(fmt.Sprintf("PE(TTM): %.2f / PB: %.2f\n", q.PETTM, q.PB))
		sb.WriteString("\n注：腾讯快照不含行业/上市日期；如需完整基础信息请确保本地数据库已初始化。")
		return sb.String(), nil
	} else {
		log.Printf("get_stock_basic 腾讯快照失败 tsCode=%s: %v，回退 tsdb", tsCode, err)
	}

	// 回退：本地 tsdb
	if s.client != nil {
		filter := tsdb.StockBasicFilter{TSCode: tsCode}
		if df, err := s.client.GetStockBasic(ctx, filter); err == nil {
			return fmt.Sprintf("股票 %s 基础信息：\n%s", tsCode, formatDataFrame(df)), nil
		} else {
			log.Printf("get_stock_basic tsdb 回退失败 tsCode=%s: %v", tsCode, err)
		}
	}

	return "", fmt.Errorf("股票基础信息接口返回异常（表不存在或网络不可达）：无法获取 %s 的基础信息", tsCode)
}

// GetAllStockBasic 获取所有上市股票列表
func (s *StockTools) GetAllStockBasic(ctx context.Context) (string, error) {
	filter := tsdb.StockBasicFilter{ListStatus: "L"}
	df, err := s.client.GetStockBasic(ctx, filter)
	if err != nil {
		return "", fmt.Errorf("get all stock basic: %w", err)
	}
	return fmt.Sprintf("所有上市股票（%d 只）：\n%s", len(df.Rows), formatDataFrame(df)), nil
}

// GetTradeCalendar 获取交易日历。HTTP 优先：以上证指数 (sh000001) 的日 K 线
// 中出现的日期作为交易日集合；HTTP 失败时回退本地 tsdb（未初始化时会失败）。
func (s *StockTools) GetTradeCalendar(ctx context.Context, startDate string, endDate string) (string, error) {
	start := normalizeDate(startDate)
	end := normalizeDate(endDate)

	// 主路径：腾讯上证指数日 K（其日期序列即交易日序列）。
	if rows, err := s.fetchKlineTencent("sh", "000001", start, end, 120); err == nil {
		filtered := filterKlineByDate(rows, start, end)
		if len(filtered) > 0 {
			var sb strings.Builder
			sb.WriteString(fmt.Sprintf("交易日历（%s 至 %s，来源：腾讯财经上证指数日K）：\n\n", startDate, endDate))
			sb.WriteString(fmt.Sprintf("共 %d 个交易日\n\n", len(filtered)))
			sb.WriteString("| 序号 | 日期 |\n|------|------|\n")
			for i, r := range filtered {
				sb.WriteString(fmt.Sprintf("| %d | %s |\n", i+1, r.Date))
			}
			return sb.String(), nil
		}
	} else {
		log.Printf("get_trade_calendar 腾讯K线失败: %v，回退 tsdb", err)
	}

	// 回退：本地 tsdb
	if s.client != nil {
		filter := tsdb.TradeCalendarFilter{
			StartDate: startDate,
			EndDate:   endDate,
		}
		if df, err := s.client.GetTradeCalendar(ctx, filter); err == nil && df != nil && len(df.Rows) > 0 {
			return fmt.Sprintf("交易日历（%s 至 %s，本地库）：\n%s", startDate, endDate, formatDataFrame(df)), nil
		} else if err != nil {
			log.Printf("get_trade_calendar tsdb 回退失败: %v", err)
		}
	}

	return "", fmt.Errorf("交易日历接口返回异常（表不存在或网络不可达）：无法获取 %s 至 %s 的交易日历", startDate, endDate)
}

// GetAdjFactor 获取复权因子
func (s *StockTools) GetAdjFactor(ctx context.Context, tsCode string, startDate string, endDate string) (string, error) {
	df, err := s.client.GetAdjFactor(ctx, tsCode, startDate, endDate)
	if err != nil {
		return "", fmt.Errorf("get adj factor: %w", err)
	}
	return fmt.Sprintf("股票 %s 复权因子（%s 至 %s）：\n%s",
		tsCode, startDate, endDate, formatDataFrame(df)), nil
}

// ========== eino Tool 封装 ==========

// NewGetStockDataTool 创建获取股票数据的 Tool
func (s *StockTools) NewGetStockDataTool() tool.InvokableTool {
	type Input struct {
		TSCode    string `json:"ts_code" jsonschema_description:"股票代码，如 000001.SZ 或 600519.SH"`
		StartDate string `json:"start_date" jsonschema_description:"开始日期，格式 YYYYMMDD，如 20240101"`
		EndDate   string `json:"end_date" jsonschema_description:"结束日期，格式 YYYYMMDD，如 20241231"`
	}

	t, err := utils.InferTool(
		"get_stock_data",
		"获取股票日线 K 线数据（前复权），包含 OHLCV。顺序：TickFlow/Panel → 腾讯前复权 → 新浪 → 本地库",
		func(ctx context.Context, input *Input) (string, error) {
			return s.GetStockData(ctx, input.TSCode, input.StartDate, input.EndDate)
		},
	)
	if err != nil {
		log.Fatalf("创建 get_stock_data 工具失败: %v", err)
	}
	return t
}

// NewGetStockBasicTool 创建获取股票基础信息的 Tool
func (s *StockTools) NewGetStockBasicTool() tool.InvokableTool {
	type Input struct {
		TSCode string `json:"ts_code" jsonschema_description:"股票代码，如 000001.SZ 或 600519.SH；留空则获取所有上市股票"`
	}

	t, err := utils.InferTool(
		"get_stock_basic",
		"获取股票基础信息，包括名称、行业、上市日期等；如果 ts_code 留空则获取所有上市股票列表",
		func(ctx context.Context, input *Input) (string, error) {
			if input.TSCode == "" {
				return s.GetAllStockBasic(ctx)
			}
			return s.GetStockBasic(ctx, input.TSCode)
		},
	)
	if err != nil {
		log.Fatalf("创建 get_stock_basic 工具失败: %v", err)
	}
	return t
}

// NewGetTradeCalendarTool 创建获取交易日历的 Tool
func (s *StockTools) NewGetTradeCalendarTool() tool.InvokableTool {
	type Input struct {
		StartDate string `json:"start_date" jsonschema_description:"开始日期，格式 YYYYMMDD"`
		EndDate   string `json:"end_date" jsonschema_description:"结束日期，格式 YYYYMMDD"`
	}

	t, err := utils.InferTool(
		"get_trade_calendar",
		"获取交易日历，查询指定日期范围内哪些是交易日",
		func(ctx context.Context, input *Input) (string, error) {
			return s.GetTradeCalendar(ctx, input.StartDate, input.EndDate)
		},
	)
	if err != nil {
		log.Fatalf("创建 get_trade_calendar 工具失败: %v", err)
	}
	return t
}

// NewGetDailyBasicTool 创建获取每日基本面的 Tool
func (s *StockTools) NewGetDailyBasicTool() tool.InvokableTool {
	type Input struct {
		TSCode    string `json:"ts_code" jsonschema_description:"股票代码，如 000001.SZ 或 600519.SH"`
		StartDate string `json:"start_date" jsonschema_description:"开始日期，格式 YYYYMMDD"`
		EndDate   string `json:"end_date" jsonschema_description:"结束日期，格式 YYYYMMDD"`
	}

	t, err := utils.InferTool(
		"get_daily_basic",
		"获取股票每日基本面数据，包括 PE、PB、市值、换手率等指标（腾讯实时快照）",
		func(ctx context.Context, input *Input) (string, error) {
			return s.GetDailyBasic(ctx, input.TSCode, input.StartDate, input.EndDate)
		},
	)
	if err != nil {
		log.Fatalf("创建 get_daily_basic 工具失败: %v", err)
	}
	return t
}

// GetAllTools 获取所有股票数据 Tools
func (s *StockTools) GetAllTools() []tool.BaseTool {
	return []tool.BaseTool{
		s.NewGetStockDataTool(),
		s.NewGetStockBasicTool(),
		s.NewGetTradeCalendarTool(),
		s.NewGetDailyBasicTool(),
	}
}
