package tools

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net"
	"net/http"
	"net/url"
	"regexp"
	"strconv"
	"strings"
	"time"
	"unicode/utf8"

	"github.com/cloudwego/eino/components/tool"
	"github.com/cloudwego/eino/components/tool/utils"
	"github.com/google/uuid"
	"golang.org/x/text/encoding/simplifiedchinese"
)

// ipv4Transport 返回一个强制走 IPv4 的 http.Transport。
// 东财 push2/push2his 等接口在 IPv6 链路上会返回 Empty reply
// （服务器 IPv6 路由不稳定），强制 IPv4 可稳定访问。
func ipv4Transport() *http.Transport {
	dialer := &net.Dialer{
		Timeout:   10 * time.Second,
		KeepAlive: 30 * time.Second,
	}
	return &http.Transport{
		DialContext: func(ctx context.Context, network, addr string) (net.Conn, error) {
			// 仅解析 IPv4 地址
			return dialer.DialContext(ctx, "tcp4", addr)
		},
		MaxIdleConns:          20,
		IdleConnTimeout:       60 * time.Second,
		TLSHandshakeTimeout:   10 * time.Second,
		ExpectContinueTimeout: 1 * time.Second,
	}
}

// EastMoneyTools 东财数据工具集
type EastMoneyTools struct {
	client      *http.Client
	lastCall    time.Time
	minInterval time.Duration
}

// NewEastMoneyTools 创建东财工具集
func NewEastMoneyTools() *EastMoneyTools {
	return &EastMoneyTools{
		client: &http.Client{
			Timeout:   15 * time.Second,
			Transport: ipv4Transport(),
		},
		minInterval: 1 * time.Second,
	}
}

// doGet 执行GET请求（带重试，应对东财 push2/push2his 连接级间歇风控）
func (e *EastMoneyTools) doGet(url string, params map[string]string) ([]byte, error) {
	return e.doGetWithHeaders(url, params, map[string]string{"Referer": "https://quote.eastmoney.com/"})
}

// isTransientErr 判断是否为可重试的瞬态错误（连接重置/EOF/超时）
func isTransientErr(err error) bool {
	if err == nil {
		return false
	}
	msg := strings.ToLower(err.Error())
	return strings.Contains(msg, "eof") ||
		strings.Contains(msg, "connection reset") ||
		strings.Contains(msg, "broken pipe") ||
		strings.Contains(msg, "timeout") ||
		strings.Contains(msg, "no such host") ||
		strings.Contains(msg, "refused") ||
		strings.Contains(msg, "empty reply")
}

// throttle 限流控制
func (e *EastMoneyTools) throttle() {
	elapsed := time.Since(e.lastCall)
	if elapsed < e.minInterval {
		time.Sleep(e.minInterval - elapsed + time.Duration(500)*time.Millisecond)
	}
	e.lastCall = time.Now()
}

// ============ 龙虎榜 ============

// DragonTigerRecord 龙虎榜记录
type DragonTigerRecord struct {
	Date         string  `json:"date"`
	Code         string  `json:"code"`
	Name         string  `json:"name"`
	Reason       string  `json:"reason"`
	NetBuy       float64 `json:"net_buy"` // 净买入(亿)
	TurnoverRate float64 `json:"turnover_rate"`
}

// GetDragonTigerBoard 获取龙虎榜数据
func (e *EastMoneyTools) GetDragonTigerBoard(ctx context.Context, code string, lookBackDays int) (string, error) {
	// 计算日期范围
	endDate := time.Now().Format("2006-01-02")
	startDate := time.Now().AddDate(0, 0, -lookBackDays).Format("2006-01-02")

	url := "https://datacenter-web.eastmoney.com/api/data/v1/get"
	params := map[string]string{
		"reportName":  "RPT_DAILYBILLBOARD_DETAILSNEW",
		"columns":     "ALL",
		"filter":      fmt.Sprintf("(TRADE_DATE>='%s')(TRADE_DATE<='%s')(SECURITY_CODE=\"%s\")", startDate, endDate, code),
		"pageNumber":  "1",
		"pageSize":    "50",
		"sortColumns": "TRADE_DATE",
		"sortTypes":   "-1",
		"source":      "WEB",
		"client":      "WEB",
	}

	body, err := e.doGet(url, params)
	if err != nil {
		return "", fmt.Errorf("request dragon tiger failed: %w", err)
	}

	var result struct {
		Result struct {
			Data []struct {
				TradeDate       string  `json:"TRADE_DATE"`
				SecurityCode    string  `json:"SECURITY_CODE"`
				SecurityName    string  `json:"SECURITY_NAME_ABBR"`
				Explanation     string  `json:"EXPLANATION"`
				BillboardNetAmt float64 `json:"BILLBOARD_NET_AMT"`
				TurnoverRate    float64 `json:"TURNOVERRATE"`
			} `json:"data"`
		} `json:"result"`
	}

	if err := json.Unmarshal(body, &result); err != nil {
		return "", fmt.Errorf("parse dragon tiger failed: %w", err)
	}

	if len(result.Result.Data) == 0 {
		return fmt.Sprintf("# 龙虎榜 | %s\n\n近%d日无龙虎榜记录", code, lookBackDays), nil
	}

	var sb strings.Builder
	sb.WriteString(fmt.Sprintf("# 龙虎榜 | %s\n\n", code))
	sb.WriteString(fmt.Sprintf("近%d日上榜 %d 次\n\n", lookBackDays, len(result.Result.Data)))
	sb.WriteString("日期 | 原因 | 净买入(亿)\n")
	sb.WriteString("--- | --- | ---\n")

	for _, rec := range result.Result.Data {
		date := datePrefix(rec.TradeDate)
		netBuy := rec.BillboardNetAmt / 1e8
		reason := rec.Explanation
		if len(reason) > 30 {
			reason = reason[:30] + "..."
		}
		sb.WriteString(fmt.Sprintf("%s | %s | %.2f\n", date, reason, netBuy))
	}

	return sb.String(), nil
}

// NewGetDragonTigerBoardTool 创建龙虎榜工具
func (e *EastMoneyTools) NewGetDragonTigerBoardTool() tool.InvokableTool {
	type Input struct {
		Code         string `json:"code" jsonschema_description:"股票代码，如 600519"`
		LookBackDays int    `json:"look_back_days" jsonschema_description:"回看天数，默认30"`
	}

	t, err := utils.InferTool(
		"get_dragon_tiger_board",
		"获取龙虎榜数据，显示个股上榜记录、买卖席位和机构动向",
		func(ctx context.Context, input *Input) (string, error) {
			days := input.LookBackDays
			if days <= 0 {
				days = 30
			}
			return e.GetDragonTigerBoard(ctx, input.Code, days)
		},
	)
	if err != nil {
		log.Fatalf("创建 get_dragon_tiger_board 工具失败: %v", err)
	}
	return t
}

// ============ 全市场龙虎榜 ============

// GetDailyDragonTiger 获取全市场龙虎榜
func (e *EastMoneyTools) GetDailyDragonTiger(ctx context.Context, tradeDate string, minNetBuy float64) (string, error) {
	url := "https://datacenter-web.eastmoney.com/api/data/v1/get"
	params := map[string]string{
		"reportName":  "RPT_DAILYBILLBOARD_DETAILSNEW",
		"columns":     "ALL",
		"filter":      fmt.Sprintf("(TRADE_DATE='%s')", tradeDate),
		"pageNumber":  "1",
		"pageSize":    "50",
		"sortColumns": "BILLBOARD_NET_AMT",
		"sortTypes":   "-1",
		"source":      "WEB",
		"client":      "WEB",
	}

	body, err := e.doGet(url, params)
	if err != nil {
		return "", fmt.Errorf("request daily dragon tiger failed: %w", err)
	}

	var result struct {
		Result struct {
			Data []struct {
				SecurityCode    string  `json:"SECURITY_CODE"`
				SecurityName    string  `json:"SECURITY_NAME_ABBR"`
				Explanation     string  `json:"EXPLANATION"`
				BillboardNetAmt float64 `json:"BILLBOARD_NET_AMT"`
			} `json:"data"`
		} `json:"result"`
	}

	if err := json.Unmarshal(body, &result); err != nil {
		return "", fmt.Errorf("parse daily dragon tiger failed: %w", err)
	}

	var sb strings.Builder
	sb.WriteString(fmt.Sprintf("# 全市场龙虎榜 | %s\n\n", tradeDate))

	count := 0
	sb.WriteString("排名 | 代码 | 名称 | 净买额(亿) | 上榜原因\n")
	sb.WriteString("--- | --- | --- | --- | ---\n")

	for i, rec := range result.Result.Data {
		netBuy := rec.BillboardNetAmt / 1e8
		if minNetBuy > 0 && netBuy < minNetBuy {
			continue
		}
		count++
		reason := rec.Explanation
		if len(reason) > 25 {
			reason = reason[:25] + "..."
		}
		sb.WriteString(fmt.Sprintf("%d | %s | %s | %.2f | %s\n",
			i+1, rec.SecurityCode, rec.SecurityName, netBuy, reason))
	}

	sb.WriteString(fmt.Sprintf("\n共 %d 只上榜", count))
	return sb.String(), nil
}

// NewGetDailyDragonTigerTool 创建全市场龙虎榜工具
func (e *EastMoneyTools) NewGetDailyDragonTigerTool() tool.InvokableTool {
	type Input struct {
		TradeDate string  `json:"trade_date" jsonschema_description:"交易日期，格式 YYYY-MM-DD"`
		MinNetBuy float64 `json:"min_net_buy" jsonschema_description:"最小净买入额(亿)，默认0"`
	}

	t, err := utils.InferTool(
		"get_daily_dragon_tiger",
		"获取全市场龙虎榜，显示当日所有上榜股票和净买入排名",
		func(ctx context.Context, input *Input) (string, error) {
			return e.GetDailyDragonTiger(ctx, input.TradeDate, input.MinNetBuy)
		},
	)
	if err != nil {
		log.Fatalf("创建 get_daily_dragon_tiger 工具失败: %v", err)
	}
	return t
}

// ============ 板块归属 ============

// ConceptBlock 板块信息
type ConceptBlock struct {
	Name      string  `json:"name"`
	Code      string  `json:"code"`
	ChangePct float64 `json:"change_pct"`
	LeadStock string  `json:"lead_stock"`
}

// GetConceptBlocks 获取个股所属板块
func (e *EastMoneyTools) GetConceptBlocks(ctx context.Context, code string) (string, error) {
	marketCode := 1
	if !strings.HasPrefix(code, "6") {
		marketCode = 0
	}

	url := "https://push2.eastmoney.com/api/qt/slist/get"
	params := map[string]string{
		"fltt":   "2",
		"invt":   "2",
		"secid":  fmt.Sprintf("%d.%s", marketCode, code),
		"spt":    "3",
		"pi":     "0",
		"pz":     "200",
		"po":     "1",
		"fields": "f12,f14,f3,f128",
	}

	body, err := e.doGet(url, params)
	if err != nil {
		return "", fmt.Errorf("request concept blocks failed: %w", err)
	}

	var result struct {
		Data struct {
			Diff map[string]struct {
				F12  string  `json:"f12"`  // 板块代码
				F14  string  `json:"f14"`  // 板块名
				F3   float64 `json:"f3"`   // 涨跌幅
				F128 string  `json:"f128"` // 龙头股
			} `json:"diff"`
		} `json:"data"`
	}

	if err := json.Unmarshal(body, &result); err != nil {
		return "", fmt.Errorf("parse concept blocks failed: %w", err)
	}

	var sb strings.Builder
	sb.WriteString(fmt.Sprintf("# 板块归属 | %s\n\n", code))

	tags := []string{}
	for _, block := range result.Data.Diff {
		tags = append(tags, block.F14)
	}

	if len(tags) == 0 {
		sb.WriteString("无板块数据\n")
	} else {
		sb.WriteString(fmt.Sprintf("共 %d 个板块:\n\n", len(tags)))
		sb.WriteString("板块名 | 涨跌幅 | 龙头股\n")
		sb.WriteString("--- | --- | ---\n")
		for _, block := range result.Data.Diff {
			sb.WriteString(fmt.Sprintf("%s | %.2f%% | %s\n", block.F14, block.F3, block.F128))
		}
	}

	return sb.String(), nil
}

// NewGetConceptBlocksTool 创建板块归属工具
func (e *EastMoneyTools) NewGetConceptBlocksTool() tool.InvokableTool {
	type Input struct {
		Code string `json:"code" jsonschema_description:"股票代码，如 600519"`
	}

	t, err := utils.InferTool(
		"get_concept_blocks",
		"获取个股所属板块/概念归属，包括行业、概念、地域分类",
		func(ctx context.Context, input *Input) (string, error) {
			return e.GetConceptBlocks(ctx, input.Code)
		},
	)
	if err != nil {
		log.Fatalf("创建 get_concept_blocks 工具失败: %v", err)
	}
	return t
}

// ============ 资金流向(分钟级) ============

// FundFlowItem 资金流向记录
type FundFlowItem struct {
	Time     string  `json:"time"`
	MainNet  float64 `json:"main_net"`  // 主力净流入
	SuperNet float64 `json:"super_net"` // 超大单净流入
	LargeNet float64 `json:"large_net"` // 大单净流入
	MidNet   float64 `json:"mid_net"`   // 中单净流入
	SmallNet float64 `json:"small_net"` // 小单净流入
}

// GetFundFlow 获取个股资金流向(分钟级)
func (e *EastMoneyTools) GetFundFlow(ctx context.Context, code string) (string, error) {
	marketCode := 1
	if !strings.HasPrefix(code, "6") {
		marketCode = 0
	}

	url := "https://push2.eastmoney.com/api/qt/stock/fflow/kline/get"
	params := map[string]string{
		"secid":   fmt.Sprintf("%d.%s", marketCode, code),
		"klt":     "1",
		"fields1": "f1,f2,f3,f7",
		"fields2": "f51,f52,f53,f54,f55,f56,f57",
	}

	body, err := e.doGet(url, params)
	if err != nil {
		return fmt.Sprintf("# 资金流向(分钟级) | %s\n\n⚠️ 东财 push2 资金流接口对当前网络环境临时不可用（连接被拒/风控）：%v。\n建议结合融资融券、龙虎榜、北向资金等其他资金面指标综合判断主力动向。", code, err), nil
	}

	var result struct {
		Data struct {
			Klines []string `json:"klines"`
		} `json:"data"`
	}

	if err := json.Unmarshal(body, &result); err != nil {
		return "", fmt.Errorf("parse fund flow failed: %w", err)
	}

	if len(result.Data.Klines) == 0 {
		return fmt.Sprintf("# 资金流向 | %s\n\n无分钟级资金流数据", code), nil
	}

	// 解析并汇总
	totalMain := 0.0
	totalSuper := 0.0
	var recentItems []string

	for _, line := range result.Data.Klines {
		parts := strings.Split(line, ",")
		if len(parts) < 6 {
			continue
		}
		mainNet, _ := strconv.ParseFloat(parts[1], 64)
		superNet, _ := strconv.ParseFloat(parts[5], 64)

		totalMain += mainNet
		totalSuper += superNet

		// 最近5条
		if len(recentItems) < 5 {
			recentItems = append(recentItems, fmt.Sprintf("  %s: 主力=%s 超大单=%s",
				parts[0], formatAmount(mainNet), formatAmount(superNet)))
		}
	}

	var sb strings.Builder
	sb.WriteString(fmt.Sprintf("# 资金流向(分钟级) | %s\n\n", code))
	sb.WriteString("今日分钟级资金流向:\n")
	for _, item := range recentItems {
		sb.WriteString(item + "\n")
	}

	sb.WriteString(fmt.Sprintf("\n**主力累计净流入**: %s\n", formatAmount(totalMain)))
	sb.WriteString(fmt.Sprintf("**超大单累计净流入**: %s\n", formatAmount(totalSuper)))

	if totalMain > 0 {
		sb.WriteString("**判断**: 主力资金净流入，看多\n")
	} else {
		sb.WriteString("**判断**: 主力资金净流出，看空\n")
	}

	return sb.String(), nil
}

// formatAmount 格式化金额
func formatAmount(amount float64) string {
	abs := amount
	if abs < 0 {
		abs = -abs
	}
	if abs >= 1e8 {
		return fmt.Sprintf("%.2f亿", amount/1e8)
	} else if abs >= 1e4 {
		return fmt.Sprintf("%.0f万", amount/1e4)
	}
	return fmt.Sprintf("%.0f元", amount)
}

// NewGetFundFlowTool 创建资金流向工具
func (e *EastMoneyTools) NewGetFundFlowTool() tool.InvokableTool {
	type Input struct {
		Code string `json:"code" jsonschema_description:"股票代码，如 600519"`
	}

	t, err := utils.InferTool(
		"get_fund_flow",
		"获取个股资金流向(分钟级)，显示主力/超大单/大单/中单/小单净流入",
		func(ctx context.Context, input *Input) (string, error) {
			return e.GetFundFlow(ctx, input.Code)
		},
	)
	if err != nil {
		log.Fatalf("创建 get_fund_flow 工具失败: %v", err)
	}
	return t
}

// ============ 资金流向(120日) ============

// GetFundFlow120d 获取个股120日资金流向
func (e *EastMoneyTools) GetFundFlow120d(ctx context.Context, code string) (string, error) {
	marketCode := 1
	if !strings.HasPrefix(code, "6") {
		marketCode = 0
	}

	url := "https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get"
	params := map[string]string{
		"secid":   fmt.Sprintf("%d.%s", marketCode, code),
		"lmt":     "0",
		"klt":     "101",
		"fields1": "f1,f2,f3,f7",
		"fields2": "f51,f52,f53,f54,f55,f56,f57",
	}

	body, err := e.doGet(url, params)
	if err != nil {
		return fmt.Sprintf("# 资金流向(120日) | %s\n\n⚠️ 东财 push2his 120日资金流接口对当前网络环境临时不可用（连接被拒/风控）：%v。\n建议结合融资融券、龙虎榜、北向资金等其他资金面指标综合判断主力中期动向。", code, err), nil
	}

	var result struct {
		Data struct {
			Klines []string `json:"klines"`
		} `json:"data"`
	}

	if err := json.Unmarshal(body, &result); err != nil {
		return "", fmt.Errorf("parse fund flow 120d failed: %w", err)
	}

	if len(result.Data.Klines) == 0 {
		return fmt.Sprintf("# 资金流向(120日) | %s\n\n无数据", code), nil
	}

	// 计算近20日累计
	totalMain20 := 0.0
	totalSuper20 := 0.0
	klines := result.Data.Klines
	start := 0
	if len(klines) > 20 {
		start = len(klines) - 20
	}

	for _, line := range klines[start:] {
		parts := strings.Split(line, ",")
		if len(parts) < 6 {
			continue
		}
		mainNet, _ := strconv.ParseFloat(parts[1], 64)
		superNet, _ := strconv.ParseFloat(parts[5], 64)
		totalMain20 += mainNet
		totalSuper20 += superNet
	}

	var sb strings.Builder
	sb.WriteString(fmt.Sprintf("# 资金流向(120日) | %s\n\n", code))
	sb.WriteString(fmt.Sprintf("共 %d 个交易日数据\n\n", len(klines)))
	sb.WriteString(fmt.Sprintf("**近20日主力累计净流入**: %s\n", formatAmount(totalMain20)))
	sb.WriteString(fmt.Sprintf("**近20日超大单累计净流入**: %s\n", formatAmount(totalSuper20)))

	if totalMain20 > 0 {
		sb.WriteString("**判断**: 主力资金净流入，看多\n")
	} else {
		sb.WriteString("**判断**: 主力资金净流出，看空\n")
	}

	return sb.String(), nil
}

// NewGetFundFlow120dTool 创建120日资金流向工具
func (e *EastMoneyTools) NewGetFundFlow120dTool() tool.InvokableTool {
	type Input struct {
		Code string `json:"code" jsonschema_description:"股票代码，如 600519"`
	}

	t, err := utils.InferTool(
		"get_fund_flow_120d",
		"获取个股120日资金流向历史，显示主力/超大单累计净流入趋势",
		func(ctx context.Context, input *Input) (string, error) {
			return e.GetFundFlow120d(ctx, input.Code)
		},
	)
	if err != nil {
		log.Fatalf("创建 get_fund_flow_120d 工具失败: %v", err)
	}
	return t
}

// ============ 行业排名 ============

// GetIndustryComparison 获取行业板块排名
func (e *EastMoneyTools) GetIndustryComparison(ctx context.Context, topN int) (string, error) {
	url := "https://push2.eastmoney.com/api/qt/clist/get"
	params := map[string]string{
		"pn":     "1",
		"pz":     "100",
		"po":     "1",
		"np":     "1",
		"fltt":   "2",
		"invt":   "2",
		"fs":     "m:90+t:2",
		"fields": "f2,f3,f4,f12,f13,f14,f104,f105,f128,f136,f140,f141,f207",
	}

	body, err := e.doGet(url, params)
	if err != nil {
		return fmt.Sprintf("# 行业板块排名\n\n⚠️ 东财行业排名接口（push2/clist）对当前网络环境临时不可用（连接被拒/风控）：%v。\n建议结合同花顺热点强势股（get_hot_stocks）、北向资金（get_northbound_flow）等指标判断板块轮动方向。", err), nil
	}

	var result struct {
		Data struct {
			Diff []struct {
				F14  string  `json:"f14"`  // 行业名
				F3   float64 `json:"f3"`   // 涨跌幅
				F104 int     `json:"f104"` // 上涨家数
				F105 int     `json:"f105"` // 下跌家数
				F140 string  `json:"f140"` // 领涨股
			} `json:"diff"`
		} `json:"data"`
	}

	if err := json.Unmarshal(body, &result); err != nil {
		return "", fmt.Errorf("parse industry comparison failed: %w", err)
	}

	var sb strings.Builder
	sb.WriteString("# 行业板块排名\n\n")
	sb.WriteString("排名 | 行业 | 涨跌幅 | 上涨 | 下跌 | 领涨股\n")
	sb.WriteString("--- | --- | --- | --- | --- | ---\n")

	limit := topN * 2
	if limit > len(result.Data.Diff) {
		limit = len(result.Data.Diff)
	}

	for i := 0; i < limit; i++ {
		rec := result.Data.Diff[i]
		sb.WriteString(fmt.Sprintf("%d | %s | %.2f%% | %d | %d | %s\n",
			i+1, rec.F14, rec.F3, rec.F104, rec.F105, rec.F140))
		if i >= topN-1 && i < limit-1 {
			sb.WriteString("--- | --- | --- | --- | --- | ---\n")
		}
	}

	return sb.String(), nil
}

// NewGetIndustryComparisonTool 创建行业排名工具
func (e *EastMoneyTools) NewGetIndustryComparisonTool() tool.InvokableTool {
	type Input struct {
		TopN int `json:"top_n" jsonschema_description:"显示前N个行业，默认10"`
	}

	t, err := utils.InferTool(
		"get_industry_comparison",
		"获取行业板块涨跌排名，显示哪些行业在涨/跌，资金流向",
		func(ctx context.Context, input *Input) (string, error) {
			topN := input.TopN
			if topN <= 0 {
				topN = 10
			}
			return e.GetIndustryComparison(ctx, topN)
		},
	)
	if err != nil {
		log.Fatalf("创建 get_industry_comparison 工具失败: %v", err)
	}
	return t
}

// ============ 限售解禁 ============

// GetLockupExpiry 获取限售解禁数据
func (e *EastMoneyTools) GetLockupExpiry(ctx context.Context, code string, forwardDays int) (string, error) {
	endDate := time.Now().AddDate(0, 0, forwardDays).Format("2006-01-02")

	url := "https://datacenter-web.eastmoney.com/api/data/v1/get"
	params := map[string]string{
		"reportName":  "RPT_LIFT_STAGE",
		"columns":     "ALL",
		"filter":      fmt.Sprintf("(SECURITY_CODE=\"%s\")(FREE_DATE>='%s')(FREE_DATE<='%s')", code, time.Now().Format("2006-01-02"), endDate),
		"pageNumber":  "1",
		"pageSize":    "20",
		"sortColumns": "FREE_DATE",
		"sortTypes":   "1",
		"source":      "WEB",
		"client":      "WEB",
	}

	body, err := e.doGet(url, params)
	if err != nil {
		return "", fmt.Errorf("request lockup expiry failed: %w", err)
	}

	var result struct {
		Result struct {
			Data []struct {
				FreeDate      string  `json:"FREE_DATE"`
				LimitedType   string  `json:"LIMITED_STOCK_TYPE"`
				FreeSharesNum float64 `json:"FREE_SHARES_NUM"`
				FreeRatio     float64 `json:"FREE_RATIO"`
			} `json:"data"`
		} `json:"result"`
	}

	if err := json.Unmarshal(body, &result); err != nil {
		return "", fmt.Errorf("parse lockup expiry failed: %w", err)
	}

	var sb strings.Builder
	sb.WriteString(fmt.Sprintf("# 限售解禁 | %s\n\n", code))

	if len(result.Result.Data) == 0 {
		sb.WriteString(fmt.Sprintf("未来%d天无限售解禁\n", forwardDays))
	} else {
		sb.WriteString(fmt.Sprintf("未来%d天有 %d 笔解禁:\n\n", forwardDays, len(result.Result.Data)))
		sb.WriteString("日期 | 类型 | 解禁数量(万股) | 占比\n")
		sb.WriteString("--- | --- | --- | ---\n")
		for _, rec := range result.Result.Data {
			date := datePrefix(rec.FreeDate)
			shares := rec.FreeSharesNum / 10000
			sb.WriteString(fmt.Sprintf("%s | %s | %.2f | %.2f%%\n",
				date, rec.LimitedType, shares, rec.FreeRatio))
		}
	}

	return sb.String(), nil
}

// NewGetLockupExpiryTool 创建限售解禁工具
func (e *EastMoneyTools) NewGetLockupExpiryTool() tool.InvokableTool {
	type Input struct {
		Code        string `json:"code" jsonschema_description:"股票代码，如 600519"`
		ForwardDays int    `json:"forward_days" jsonschema_description:"预测天数，默认90"`
	}

	t, err := utils.InferTool(
		"get_lockup_expiry",
		"获取限售解禁数据，显示未来解禁日期、数量和占比",
		func(ctx context.Context, input *Input) (string, error) {
			days := input.ForwardDays
			if days <= 0 {
				days = 90
			}
			return e.GetLockupExpiry(ctx, input.Code, days)
		},
	)
	if err != nil {
		log.Fatalf("创建 get_lockup_expiry 工具失败: %v", err)
	}
	return t
}

// ============ 融资融券 ============

// GetMarginTrading 获取融资融券数据
func (e *EastMoneyTools) GetMarginTrading(ctx context.Context, code string, days int) (string, error) {
	url := "https://datacenter-web.eastmoney.com/api/data/v1/get"
	params := map[string]string{
		"reportName":  "RPTA_WEB_RZRQ_GGMX",
		"columns":     "ALL",
		"filter":      fmt.Sprintf("(SCODE=\"%s\")", code),
		"pageNumber":  "1",
		"pageSize":    strconv.Itoa(days),
		"sortColumns": "DATE",
		"sortTypes":   "-1",
		"source":      "WEB",
		"client":      "WEB",
	}

	body, err := e.doGet(url, params)
	if err != nil {
		return "", fmt.Errorf("request margin trading failed: %w", err)
	}

	var result struct {
		Result struct {
			Data []struct {
				Date  string  `json:"DATE"`
				Rzye  float64 `json:"RZYE"`  // 融资余额
				Rzmre float64 `json:"RZMRE"` // 融资买入额
				Rqye  float64 `json:"RQYE"`  // 融券余额
			} `json:"data"`
		} `json:"result"`
	}

	if err := json.Unmarshal(body, &result); err != nil {
		return "", fmt.Errorf("parse margin trading failed: %w", err)
	}

	var sb strings.Builder
	sb.WriteString(fmt.Sprintf("# 融资融券 | %s\n\n", code))

	if len(result.Result.Data) == 0 {
		sb.WriteString("无融资融券数据\n")
	} else {
		sb.WriteString("日期 | 融资余额(亿) | 融资买入(亿) | 融券余额(亿)\n")
		sb.WriteString("--- | --- | --- | ---\n")
		for _, rec := range result.Result.Data {
			date := datePrefix(rec.Date)
			sb.WriteString(fmt.Sprintf("%s | %.2f | %.2f | %.2f\n",
				date, rec.Rzye/1e8, rec.Rzmre/1e8, rec.Rqye/1e8))
		}

		// 趋势
		if len(result.Result.Data) >= 2 {
			first := result.Result.Data[len(result.Result.Data)-1].Rzye
			last := result.Result.Data[0].Rzye
			change := last - first
			if change > 0 {
				sb.WriteString(fmt.Sprintf("\n**趋势**: 融资余额增加 %.2f亿\n", change/1e8))
			} else {
				sb.WriteString(fmt.Sprintf("\n**趋势**: 融资余额减少 %.2f亿\n", -change/1e8))
			}
		}
	}

	return sb.String(), nil
}

// NewGetMarginTradingTool 创建融资融券工具
func (e *EastMoneyTools) NewGetMarginTradingTool() tool.InvokableTool {
	type Input struct {
		Code string `json:"code" jsonschema_description:"股票代码，如 600519"`
		Days int    `json:"days" jsonschema_description:"查询天数，默认30"`
	}

	t, err := utils.InferTool(
		"get_margin_trading",
		"获取融资融券数据，显示融资余额、买入额、融券余额变化趋势",
		func(ctx context.Context, input *Input) (string, error) {
			days := input.Days
			if days <= 0 {
				days = 30
			}
			return e.GetMarginTrading(ctx, input.Code, days)
		},
	)
	if err != nil {
		log.Fatalf("创建 get_margin_trading 工具失败: %v", err)
	}
	return t
}

// ============ 大宗交易 ============

// GetBlockTrade 获取大宗交易数据
func (e *EastMoneyTools) GetBlockTrade(ctx context.Context, code string, days int) (string, error) {
	url := "https://datacenter-web.eastmoney.com/api/data/v1/get"
	params := map[string]string{
		"reportName":  "RPT_DATA_BLOCKTRADE",
		"columns":     "ALL",
		"filter":      fmt.Sprintf("(SECURITY_CODE=\"%s\")", code),
		"pageNumber":  "1",
		"pageSize":    strconv.Itoa(days),
		"sortColumns": "TRADE_DATE",
		"sortTypes":   "-1",
		"source":      "WEB",
		"client":      "WEB",
	}

	body, err := e.doGet(url, params)
	if err != nil {
		return "", fmt.Errorf("request block trade failed: %w", err)
	}

	var result struct {
		Result struct {
			Data []struct {
				TradeDate  string  `json:"TRADE_DATE"`
				DealPrice  float64 `json:"DEAL_PRICE"`
				ClosePrice float64 `json:"CLOSE_PRICE"`
				DealVolume float64 `json:"DEAL_VOLUME"`
				BuyerName  string  `json:"BUYER_NAME"`
				SellerName string  `json:"SELLER_NAME"`
			} `json:"data"`
		} `json:"result"`
	}

	if err := json.Unmarshal(body, &result); err != nil {
		return "", fmt.Errorf("parse block trade failed: %w", err)
	}

	var sb strings.Builder
	sb.WriteString(fmt.Sprintf("# 大宗交易 | %s\n\n", code))

	if len(result.Result.Data) == 0 {
		sb.WriteString("无大宗交易记录\n")
	} else {
		sb.WriteString("日期 | 价格 | 溢价 | 量(万股) | 买方 | 卖方\n")
		sb.WriteString("--- | --- | --- | --- | --- | ---\n")
		for _, rec := range result.Result.Data {
			date := datePrefix(rec.TradeDate)
			premium := 0.0
			if rec.ClosePrice > 0 {
				premium = (rec.DealPrice/rec.ClosePrice - 1) * 100
			}
			vol := rec.DealVolume / 10000
			buyer := rec.BuyerName
			if len(buyer) > 15 {
				buyer = buyer[:15] + "..."
			}
			seller := rec.SellerName
			if len(seller) > 15 {
				seller = seller[:15] + "..."
			}
			sb.WriteString(fmt.Sprintf("%s | %.2f | %+.1f%% | %.0f | %s | %s\n",
				date, rec.DealPrice, premium, vol, buyer, seller))
		}
	}

	return sb.String(), nil
}

// NewGetBlockTradeTool 创建大宗交易工具
func (e *EastMoneyTools) NewGetBlockTradeTool() tool.InvokableTool {
	type Input struct {
		Code string `json:"code" jsonschema_description:"股票代码，如 600519"`
		Days int    `json:"days" jsonschema_description:"查询天数，默认30"`
	}

	t, err := utils.InferTool(
		"get_block_trade",
		"获取大宗交易记录，显示成交价格、溢价率、买卖方营业部",
		func(ctx context.Context, input *Input) (string, error) {
			days := input.Days
			if days <= 0 {
				days = 30
			}
			return e.GetBlockTrade(ctx, input.Code, days)
		},
	)
	if err != nil {
		log.Fatalf("创建 get_block_trade 工具失败: %v", err)
	}
	return t
}

// ============ 股东户数 ============

// GetHolderCount 获取股东户数变化
func (e *EastMoneyTools) GetHolderCount(ctx context.Context, code string) (string, error) {
	url := "https://datacenter-web.eastmoney.com/api/data/v1/get"
	params := map[string]string{
		"reportName":  "RPT_HOLDERNUMLATEST",
		"columns":     "ALL",
		"filter":      fmt.Sprintf("(SECURITY_CODE=\"%s\")", code),
		"pageNumber":  "1",
		"pageSize":    "8",
		"sortColumns": "END_DATE",
		"sortTypes":   "-1",
		"source":      "WEB",
		"client":      "WEB",
	}

	body, err := e.doGet(url, params)
	if err != nil {
		return "", fmt.Errorf("request holder count failed: %w", err)
	}

	var result struct {
		Result struct {
			Data []struct {
				EndDate         string  `json:"END_DATE"`
				HolderNum       int     `json:"HOLDER_NUM"`
				HolderNumChange int     `json:"HOLDER_NUM_CHANGE"`
				HolderNumRatio  float64 `json:"HOLDER_NUM_RATIO"`
				AvgFreeShares   float64 `json:"AVG_FREE_SHARES"`
			} `json:"data"`
		} `json:"result"`
	}

	if err := json.Unmarshal(body, &result); err != nil {
		return "", fmt.Errorf("parse holder count failed: %w", err)
	}

	var sb strings.Builder
	sb.WriteString(fmt.Sprintf("# 股东户数 | %s\n\n", code))

	if len(result.Result.Data) == 0 {
		sb.WriteString("无股东户数数据\n")
	} else {
		sb.WriteString("日期 | 股东数 | 变化 | 变化率 | 户均持股\n")
		sb.WriteString("--- | --- | --- | --- | ---\n")
		for _, rec := range result.Result.Data {
			date := datePrefix(rec.EndDate)
			trend := "减少"
			if rec.HolderNumChange > 0 {
				trend = "增加"
			}
			sb.WriteString(fmt.Sprintf("%s | %d | %s%d | %.2f%% | %.0f\n",
				date, rec.HolderNum, trend, absInt(rec.HolderNumChange),
				rec.HolderNumRatio, rec.AvgFreeShares))
		}

		// 集中度信号
		if len(result.Result.Data) >= 2 {
			first := result.Result.Data[len(result.Result.Data)-1].HolderNum
			last := result.Result.Data[0].HolderNum
			if last < first*9/10 {
				sb.WriteString("\n**信号**: 股东户数持续减少，筹码集中度提升\n")
			} else if last > first*11/10 {
				sb.WriteString("\n**信号**: 股东户数增加，筹码趋于分散\n")
			}
		}
	}

	return sb.String(), nil
}

func absInt(n int) int {
	if n < 0 {
		return -n
	}
	return n
}

// NewGetHolderCountTool 创建股东户数工具
func (e *EastMoneyTools) NewGetHolderCountTool() tool.InvokableTool {
	type Input struct {
		Code string `json:"code" jsonschema_description:"股票代码，如 600519"`
	}

	t, err := utils.InferTool(
		"get_holder_count",
		"获取股东户数变化，分析筹码集中度趋势",
		func(ctx context.Context, input *Input) (string, error) {
			return e.GetHolderCount(ctx, input.Code)
		},
	)
	if err != nil {
		log.Fatalf("创建 get_holder_count 工具失败: %v", err)
	}
	return t
}

// ============ 机构持仓汇总 ============

// GetOrgHoldSummary 获取机构持仓汇总（含公募基金家数、持股占比等）。
func (e *EastMoneyTools) GetOrgHoldSummary(ctx context.Context, code string) (string, error) {
	url := "https://datacenter-web.eastmoney.com/api/data/v1/get"
	params := map[string]string{
		"reportName":  "RPT_F10_MAIN_ORGHOLD",
		"columns":     "SECURITY_CODE,REPORT_DATE,TOTAL_ORG_NUM,TOTAL_FREE_SHARES,TOTAL_MARKET_CAP,TOTAL_SHARES_RATIO,CHANGE_RATIO,NOTICE_DATE",
		"filter":      fmt.Sprintf("(SECURITY_CODE=\"%s\")", code),
		"pageNumber":  "1",
		"pageSize":    "4",
		"sortColumns": "REPORT_DATE",
		"sortTypes":   "-1",
		"source":      "WEB",
		"client":      "WEB",
	}

	body, err := e.doGet(url, params)
	if err != nil {
		return "", fmt.Errorf("request org hold summary failed: %w", err)
	}

	var result struct {
		Result struct {
			Data []struct {
				ReportDate       string  `json:"REPORT_DATE"`
				TotalOrgNum      int     `json:"TOTAL_ORG_NUM"`
				TotalFreeShares  float64 `json:"TOTAL_FREE_SHARES"`
				TotalMarketCap   float64 `json:"TOTAL_MARKET_CAP"`
				TotalSharesRatio float64 `json:"TOTAL_SHARES_RATIO"`
				ChangeRatio      float64 `json:"CHANGE_RATIO"`
				NoticeDate       string  `json:"NOTICE_DATE"`
			} `json:"data"`
		} `json:"result"`
	}

	if err := json.Unmarshal(body, &result); err != nil {
		return "", fmt.Errorf("parse org hold summary failed: %w", err)
	}

	var sb strings.Builder
	sb.WriteString(fmt.Sprintf("# 机构持仓汇总 | %s\n\n", code))

	if len(result.Result.Data) == 0 {
		sb.WriteString("无数据\n")
		return sb.String(), nil
	}

	latest := result.Result.Data[0]
	reportDate := datePrefix(latest.ReportDate)
	sb.WriteString(fmt.Sprintf("报告期: %s\n", reportDate))
	sb.WriteString(fmt.Sprintf("持仓机构家数: %d\n", latest.TotalOrgNum))
	sb.WriteString(fmt.Sprintf("机构持股占流通比: %.2f%%\n", latest.TotalSharesRatio))
	sb.WriteString(fmt.Sprintf("机构持股市值: %.2f 亿\n", latest.TotalMarketCap/1e8))
	sb.WriteString(fmt.Sprintf("较上期变动: %.2f%%\n", latest.ChangeRatio))

	if len(result.Result.Data) > 1 {
		sb.WriteString("\n## 历史报告期\n\n")
		sb.WriteString("报告期 | 机构家数 | 占流通比 | 变动\n")
		sb.WriteString("--- | --- | --- | ---\n")
		for _, rec := range result.Result.Data {
			d := datePrefix(rec.ReportDate)
			sb.WriteString(fmt.Sprintf("%s | %d | %.2f%% | %.2f%%\n",
				d, rec.TotalOrgNum, rec.TotalSharesRatio, rec.ChangeRatio))
		}
	}

	return sb.String(), nil
}

// ============ 分红送转 ============

// GetDividend 获取分红送转历史
func (e *EastMoneyTools) GetDividend(ctx context.Context, code string) (string, error) {
	url := "https://datacenter-web.eastmoney.com/api/data/v1/get"
	params := map[string]string{
		"reportName":  "RPT_SHAREBONUS_DET",
		"columns":     "ALL",
		"filter":      fmt.Sprintf("(SECURITY_CODE=\"%s\")", code),
		"pageNumber":  "1",
		"pageSize":    "10",
		"sortColumns": "EX_DIVIDEND_DATE",
		"sortTypes":   "-1",
		"source":      "WEB",
		"client":      "WEB",
	}

	body, err := e.doGet(url, params)
	if err != nil {
		return "", fmt.Errorf("request dividend failed: %w", err)
	}

	var result struct {
		Result struct {
			Data []struct {
				ExDividendDate string  `json:"EX_DIVIDEND_DATE"`
				BonusRmb       float64 `json:"PRETAX_BONUS_RMB"`
				TransferRatio  float64 `json:"TRANSFER_RATIO"`
				BonusRatio     float64 `json:"BONUS_RATIO"`
				Progress       string  `json:"ASSIGN_PROGRESS"`
			} `json:"data"`
		} `json:"result"`
	}

	if err := json.Unmarshal(body, &result); err != nil {
		return "", fmt.Errorf("parse dividend failed: %w", err)
	}

	var sb strings.Builder
	sb.WriteString(fmt.Sprintf("# 分红送转 | %s\n\n", code))

	if len(result.Result.Data) == 0 {
		sb.WriteString("无分红记录\n")
	} else {
		sb.WriteString("日期 | 派息(元/股) | 送股 | 转增 | 进度\n")
		sb.WriteString("--- | --- | --- | --- | ---\n")
		for _, rec := range result.Result.Data {
			date := datePrefix(rec.ExDividendDate)
			sb.WriteString(fmt.Sprintf("%s | %.2f | %.0f | %.0f | %s\n",
				date, rec.BonusRmb, rec.BonusRatio, rec.TransferRatio, rec.Progress))
		}
	}

	return sb.String(), nil
}

// NewGetDividendTool 创建分红送转工具
func (e *EastMoneyTools) NewGetDividendTool() tool.InvokableTool {
	type Input struct {
		Code string `json:"code" jsonschema_description:"股票代码，如 600519"`
	}

	t, err := utils.InferTool(
		"get_dividend",
		"获取分红送转历史，显示每股派息、送股、转增记录",
		func(ctx context.Context, input *Input) (string, error) {
			return e.GetDividend(ctx, input.Code)
		},
	)
	if err != nil {
		log.Fatalf("创建 get_dividend 工具失败: %v", err)
	}
	return t
}

// ============ 个股新闻 ============

var htmlTagRe = regexp.MustCompile(`<[^>]*>`)

// stripHTML 移除字符串中的 HTML 标签，并将常见的 HTML 实体做最小化处理。
func stripHTML(s string) string {
	if s == "" {
		return ""
	}
	s = htmlTagRe.ReplaceAllString(s, "")
	s = strings.ReplaceAll(s, "&nbsp;", " ")
	s = strings.ReplaceAll(s, "&amp;", "&")
	s = strings.ReplaceAll(s, "&lt;", "<")
	s = strings.ReplaceAll(s, "&gt;", ">")
	s = strings.ReplaceAll(s, "&quot;", "\"")
	s = strings.ReplaceAll(s, "&#39;", "'")
	return strings.TrimSpace(s)
}

// truncateRunes 按 rune 数量截断字符串，避免在多字节字符中间截断。
func truncateRunes(s string, n int) string {
	if utf8.RuneCountInString(s) <= n {
		return s
	}
	r := []rune(s)
	return string(r[:n]) + "..."
}

// doGetWithHeaders 执行 GET 请求并允许覆盖默认 Header。
func (e *EastMoneyTools) doGetWithHeaders(url string, params map[string]string, headers map[string]string) ([]byte, error) {
	const maxRetries = 3
	var lastErr error
	for attempt := 0; attempt < maxRetries; attempt++ {
		if attempt > 0 {
			// 指数退避 + 抖动：1s, 2s + 随机
			backoff := time.Duration(1<<attempt) * time.Second
			jitter := time.Duration(200+attempt*200) * time.Millisecond
			time.Sleep(backoff + jitter)
		}
		e.throttle()

		req, err := http.NewRequest("GET", url, nil)
		if err != nil {
			return nil, err
		}

		q := req.URL.Query()
		for k, v := range params {
			q.Add(k, v)
		}
		req.URL.RawQuery = q.Encode()

		req.Header.Set("User-Agent", "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
		req.Header.Set("Accept", "*/*")
		req.Header.Set("Accept-Language", "zh-CN,zh;q=0.9")
		req.Header.Set("Connection", "keep-alive")
		for k, v := range headers {
			req.Header.Set(k, v)
		}

		resp, err := e.client.Do(req)
		if err != nil {
			lastErr = err
			if isTransientErr(err) && attempt < maxRetries-1 {
				continue
			}
			return nil, err
		}
		body, readErr := io.ReadAll(resp.Body)
		resp.Body.Close()
		if readErr != nil {
			lastErr = readErr
			if isTransientErr(readErr) && attempt < maxRetries-1 {
				continue
			}
			return nil, readErr
		}
		// 非 2xx 视为失败
		if resp.StatusCode >= 500 {
			lastErr = fmt.Errorf("http %d from %s", resp.StatusCode, url)
			if attempt < maxRetries-1 {
				continue
			}
			return nil, lastErr
		}
		return body, nil
	}
	return nil, lastErr
}

// NewsArticle 个股新闻条目
type NewsArticle struct {
	Title   string `json:"title"`
	Content string `json:"content"`
	Date    string `json:"date"`
	Source  string `json:"source"`
	URL     string `json:"url"`
}

// parseJSONP 从 JSONP 响应中提取括号内的 JSON 并解析。
func parseJSONP(body []byte, target any) error {
	s := string(body)
	start := strings.Index(s, "(")
	end := strings.LastIndex(s, ")")
	if start < 0 || end < 0 || end <= start {
		return fmt.Errorf("invalid jsonp response")
	}
	jsonStr := s[start+1 : end]
	return json.Unmarshal([]byte(jsonStr), target)
}

// fetchSinaNews 新浪财经新闻兜底（gb2312 编码）。
func (e *EastMoneyTools) fetchSinaNews(ctx context.Context, code string, pageSize int) ([]NewsArticle, error) {
	symbol := "sh" + code
	if strings.HasPrefix(code, "0") || strings.HasPrefix(code, "3") {
		symbol = "sz" + code
	}
	urlStr := fmt.Sprintf("https://vip.stock.finance.sina.com.cn/corp/view/vCB_AllNewsStock.php?symbol=%s&Page=1", symbol)

	e.throttle()
	req, err := http.NewRequestWithContext(ctx, "GET", urlStr, nil)
	if err != nil {
		return nil, err
	}
	req.Header.Set("User-Agent", "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36")

	resp, err := e.client.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()

	// 新浪页面为 gb2312 编码，使用 GB18030 解码（兼容 gb2312）
	decoder := simplifiedchinese.GB18030.NewDecoder().Reader(resp.Body)
	body, err := io.ReadAll(decoder)
	if err != nil {
		return nil, err
	}

	re := regexp.MustCompile(`(\d{4}-\d{2}-\d{2})\s*(?:&nbsp;)*(\d{2}:\d{2})\s*(?:&nbsp;)*<a[^>]+href='([^']+)'[^>]*>([^<]+)</a>`)
	matches := re.FindAllSubmatch(body, -1)

	articles := make([]NewsArticle, 0, pageSize)
	for i, m := range matches {
		if i >= pageSize {
			break
		}
		articles = append(articles, NewsArticle{
			Title:   stripHTML(string(m[4])),
			Date:    fmt.Sprintf("%s %s", string(m[1]), string(m[2])),
			URL:     string(m[3]),
			Source:  "新浪财经",
			Content: "",
		})
	}
	return articles, nil
}

// fetchPanelNews 回源本机 TSP Panel /api/info/news（info_hub 个股新闻）。
// 优先于东财/新浪；失败或空列表返回 error，由调用方走外部兜底链。
func (e *EastMoneyTools) fetchPanelNews(ctx context.Context, code string, pageSize int) ([]NewsArticle, error) {
	code6, _, ok := splitTSCode(code)
	if !ok {
		return nil, fmt.Errorf("invalid code: %s", code)
	}

	q := url.Values{}
	q.Set("code", code6)
	q.Set("size", strconv.Itoa(pageSize))
	endpoint := panelAPIURL() + "/api/info/news?" + q.Encode()

	timeout := panelKlineTimeout()
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, endpoint, nil)
	if err != nil {
		return nil, err
	}
	req.Header.Set("User-Agent", "aistock-sidecar/get_news")
	req.Header.Set("Accept", "application/json")
	if tok := panelAPIToken(); tok != "" {
		req.Header.Set("Authorization", "Bearer "+tok)
	}

	client := &http.Client{Timeout: timeout, Transport: ipv4Transport()}
	resp, err := client.Do(req)
	if err != nil {
		return nil, err
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
		Items []struct {
			Title       string `json:"title"`
			Summary     string `json:"summary"`
			Content     string `json:"content"`
			URL         string `json:"url"`
			PublishTime string `json:"publish_time"`
			PublishDate string `json:"publish_date"`
			Source      string `json:"source_primary"`
		} `json:"items"`
	}
	if err := json.Unmarshal(body, &raw); err != nil {
		return nil, err
	}

	articles := make([]NewsArticle, 0, len(raw.Items))
	for _, it := range raw.Items {
		content := it.Summary
		if content == "" {
			content = it.Content
		}
		date := it.PublishTime
		if date == "" {
			date = it.PublishDate
		}
		articles = append(articles, NewsArticle{
			Title:   stripHTML(it.Title),
			Content: stripHTML(content),
			Date:    date,
			Source:  it.Source,
			URL:     it.URL,
		})
	}
	if len(articles) == 0 {
		return nil, fmt.Errorf("panel news empty")
	}
	return articles, nil
}

// GetNews 获取个股相关新闻
func (e *EastMoneyTools) GetNews(ctx context.Context, code string, pageSize int) (string, error) {
	if pageSize <= 0 {
		pageSize = 20
	}

	// 首选：回源本机 TSP Panel info_hub 个股新闻；失败/空再走外部东财→新浪兜底。
	if panelArticles, pErr := e.fetchPanelNews(ctx, code, pageSize); pErr == nil && len(panelArticles) > 0 {
		log.Printf("get_news source=panel/info_hub code=%s rows=%d", code, len(panelArticles))
		return e.formatNews(code, panelArticles), nil
	} else if pErr != nil {
		log.Printf("get_news panel/info_hub fail code=%s: %v", code, pErr)
	}

	paramJSON := fmt.Sprintf(`{"uid":"","keyword":"%s","type":["cmsArticleWebOld"],"client":"web","clientType":"web","clientVersion":"curr","param":{"cmsArticleWebOld":{"searchScope":"default","sort":"default","pageIndex":1,"pageSize":%d,"preTag":"","postTag":""}}}`, code, pageSize)

	urlStr := "https://search-api-web.eastmoney.com/search/jsonp"
	params := map[string]string{
		"cb":    "jQuery_news",
		"param": paramJSON,
	}
	headers := map[string]string{
		"Referer": "https://so.eastmoney.com/",
	}

	body, err := e.doGetWithHeaders(urlStr, params, headers)
	if err != nil {
		// 东财失败，尝试新浪兜底
		articles, sErr := e.fetchSinaNews(ctx, code, pageSize)
		if sErr != nil || len(articles) == 0 {
			return fmt.Sprintf("# 个股新闻 | %s\n\n数据获取失败：东财接口错误(%v)，新浪兜底亦失败。请稍后重试或换用其他数据源。", code, err), nil
		}
		return e.formatNews(code, articles), nil
	}

	var result struct {
		Data struct {
			Result struct {
				CmsArticleWebOld []struct {
					Title     string `json:"title"`
					Content   string `json:"content"`
					Date      string `json:"date"`
					MediaName string `json:"mediaName"`
					URL       string `json:"url"`
				} `json:"cmsArticleWebOld"`
			} `json:"result"`
		} `json:"data"`
	}

	if err := parseJSONP(body, &result); err != nil {
		articles, sErr := e.fetchSinaNews(ctx, code, pageSize)
		if sErr != nil || len(articles) == 0 {
			return fmt.Sprintf("# 个股新闻 | %s\n\n东财响应解析失败：%v", code, err), nil
		}
		return e.formatNews(code, articles), nil
	}

	articles := make([]NewsArticle, 0, len(result.Data.Result.CmsArticleWebOld))
	for _, item := range result.Data.Result.CmsArticleWebOld {
		articles = append(articles, NewsArticle{
			Title:   stripHTML(item.Title),
			Content: stripHTML(item.Content),
			Date:    item.Date,
			Source:  item.MediaName,
			URL:     item.URL,
		})
	}

	if len(articles) == 0 {
		// 东财返回空，尝试新浪兜底
		sinaArticles, sErr := e.fetchSinaNews(ctx, code, pageSize)
		if sErr == nil && len(sinaArticles) > 0 {
			return e.formatNews(code, sinaArticles), nil
		}
		return fmt.Sprintf("# 个股新闻 | %s\n\n东财与新浪均未返回新闻数据，可能该公司近期无重大新闻。", code), nil
	}

	return e.formatNews(code, articles), nil
}

// formatNews 将新闻列表格式化为 Markdown。
func (e *EastMoneyTools) formatNews(code string, articles []NewsArticle) string {
	var sb strings.Builder
	sb.WriteString(fmt.Sprintf("# 个股新闻 | %s\n\n", code))
	sb.WriteString(fmt.Sprintf("共获取 %d 条新闻\n\n", len(articles)))
	sb.WriteString("时间 | 来源 | 标题\n")
	sb.WriteString("--- | --- | ---\n")

	limit := len(articles)
	if limit > 20 {
		limit = 20
	}
	for i := 0; i < limit; i++ {
		a := articles[i]
		date := a.Date
		if len(date) > 16 {
			date = date[:16]
		}
		title := truncateRunes(a.Title, 40)
		source := a.Source
		if source == "" {
			source = "-"
		}
		sb.WriteString(fmt.Sprintf("%s | %s | %s\n", date, source, title))
	}

	// 追加前 5 条的内容摘要
	sb.WriteString("\n## 内容摘要（前5条）\n\n")
	for i := 0; i < 5 && i < len(articles); i++ {
		a := articles[i]
		summary := truncateRunes(a.Content, 120)
		if summary == "" {
			summary = "(无摘要)"
		}
		sb.WriteString(fmt.Sprintf("**%s** - %s\n> %s\n\n", a.Date, truncateRunes(a.Title, 50), summary))
	}

	return sb.String()
}

// NewGetNewsTool 创建个股新闻工具
func (e *EastMoneyTools) NewGetNewsTool() tool.InvokableTool {
	type Input struct {
		Code     string `json:"code" jsonschema_description:"股票代码，如 600519"`
		PageSize int    `json:"page_size" jsonschema_description:"每页新闻数量，默认20，最大20"`
	}

	t, err := utils.InferTool(
		"get_news",
		"获取个股相关新闻列表，包含标题、内容摘要、发布时间和来源",
		func(ctx context.Context, input *Input) (string, error) {
			ps := input.PageSize
			if ps <= 0 || ps > 20 {
				ps = 20
			}
			return e.GetNews(ctx, input.Code, ps)
		},
	)
	if err != nil {
		log.Fatalf("创建 get_news 工具失败: %v", err)
	}
	return t
}

// ============ 全球资讯 (7×24) ============

// GlobalNewsItem 全球资讯条目
type GlobalNewsItem struct {
	Title   string `json:"title"`
	Summary string `json:"summary"`
	Time    string `json:"showTime"`
}

// GetGlobalNews 获取 7×24 全球资讯
func (e *EastMoneyTools) GetGlobalNews(ctx context.Context, pageSize int) (string, error) {
	if pageSize <= 0 {
		pageSize = 50
	}

	urlStr := "https://np-weblist.eastmoney.com/comm/web/getFastNewsList"
	params := map[string]string{
		"client":     "web",
		"biz":        "web_724",
		"fastColumn": "102",
		"sortEnd":    "",
		"pageSize":   strconv.Itoa(pageSize),
		"req_trace":  uuid.NewString(),
	}
	headers := map[string]string{
		"Referer": "https://kuaixun.eastmoney.com/",
	}

	body, err := e.doGetWithHeaders(urlStr, params, headers)
	if err != nil {
		return fmt.Sprintf("# 全球资讯 (7×24)\n\n数据获取失败：%v", err), nil
	}

	var result struct {
		Data struct {
			FastNewsList []struct {
				Title    string `json:"title"`
				Summary  string `json:"summary"`
				ShowTime string `json:"showTime"`
			} `json:"fastNewsList"`
		} `json:"data"`
	}

	if err := json.Unmarshal(body, &result); err != nil {
		return fmt.Sprintf("# 全球资讯 (7×24)\n\n响应解析失败：%v", err), nil
	}

	if len(result.Data.FastNewsList) == 0 {
		return "# 全球资讯 (7×24)\n\n暂无资讯数据", nil
	}

	var sb strings.Builder
	sb.WriteString("# 全球资讯 (7×24)\n\n")
	sb.WriteString(fmt.Sprintf("共获取 %d 条快讯\n\n", len(result.Data.FastNewsList)))
	sb.WriteString("时间 | 标题 | 摘要\n")
	sb.WriteString("--- | --- | ---\n")

	limit := len(result.Data.FastNewsList)
	if limit > 20 {
		limit = 20
	}
	for i := 0; i < limit; i++ {
		item := result.Data.FastNewsList[i]
		title := truncateRunes(stripHTML(item.Title), 40)
		summary := truncateRunes(stripHTML(item.Summary), 80)
		sb.WriteString(fmt.Sprintf("%s | %s | %s\n", item.ShowTime, title, summary))
	}

	return sb.String(), nil
}

// NewGetGlobalNewsTool 创建全球资讯工具
func (e *EastMoneyTools) NewGetGlobalNewsTool() tool.InvokableTool {
	type Input struct {
		PageSize int `json:"page_size" jsonschema_description:"获取条数，默认50，最大50"`
	}

	t, err := utils.InferTool(
		"get_global_news",
		"获取7×24小时全球宏观/行业快讯，用于宏观与情绪分析",
		func(ctx context.Context, input *Input) (string, error) {
			ps := input.PageSize
			if ps <= 0 || ps > 50 {
				ps = 50
			}
			return e.GetGlobalNews(ctx, ps)
		},
	)
	if err != nil {
		log.Fatalf("创建 get_global_news 工具失败: %v", err)
	}
	return t
}

// GetAllTools 获取所有东财工具
func (e *EastMoneyTools) GetAllTools() []tool.BaseTool {
	return []tool.BaseTool{
		e.NewGetDragonTigerBoardTool(),
		e.NewGetDailyDragonTigerTool(),
		e.NewGetConceptBlocksTool(),
		e.NewGetFundFlowTool(),
		e.NewGetFundFlow120dTool(),
		e.NewGetIndustryComparisonTool(),
		e.NewGetLockupExpiryTool(),
		e.NewGetMarginTradingTool(),
		e.NewGetBlockTradeTool(),
		e.NewGetHolderCountTool(),
		e.NewGetDividendTool(),
		e.NewGetNewsTool(),
		e.NewGetGlobalNewsTool(),
	}
}
