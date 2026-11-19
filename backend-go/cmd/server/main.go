// 统一后端入口：组装限界上下文并暴露 /api/v1/*。
//
// 代码布局见 internal/doc.go：internal/analysis 与 internal/workbench 两个限界上下文（标准 Go 工程 + DDD 分层）。
//
// Sidecar 模式（AISTOCK_SIDECAR=1）：作为 tickflow-stock-panel 旁路 API，
// 保留 analysis / picker / market；关闭内嵌前端；workspace / hotmoney 默认不启。
package main

import (
	"context"
	"fmt"
	"log"
	"net/http"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"time"

	mdlib "github.com/easyspace-ai/bystock/pkg/marketdata"
	stocklib "github.com/easyspace-ai/bystock/pkg/stockapi"
	"github.com/gin-contrib/cors"
	"github.com/gin-gonic/gin"
	"github.com/joho/godotenv"

	analysisapi "aistock/backend/internal/analysis/interfaces/http"
	"aistock/backend/internal/analysis/tools"
	"aistock/backend/internal/appenv"
	"aistock/backend/internal/workbench/application/ai"
	"aistock/backend/internal/workbench/auth"
	"aistock/backend/internal/workbench/filesystem"
	"aistock/backend/internal/workbench/infrastructure/cache"
	"aistock/backend/internal/workbench/infrastructure/clients"
	"aistock/backend/internal/workbench/infrastructure/marketadapter"
	"aistock/backend/internal/workbench/infrastructure/persistence/sqlite"
	"aistock/backend/internal/workbench/infrastructure/persistence/stockdb"
	wbhttp "aistock/backend/internal/workbench/interfaces/http"
	"aistock/backend/internal/workbench/plugins"
	"aistock/backend/internal/workbench/proxy"
	"aistock/backend/internal/workbench/tags"
	"aistock/backend/internal/workbench/ws"

	"github.com/easyspace-ai/bystock/pkg/tdxapi"
)

func main() {
	_ = godotenv.Load()
	ctx := context.Background()
	sidecar := appenv.SidecarMode()
	if sidecar {
		log.Printf("aistock mode: sidecar (panel API; set AISTOCK_SIDECAR=0 for full SaaS)")
	}

	stockDbDir := appenv.StockDatabaseDir()
	if stockDbDir == "" {
		log.Fatal("AI_DATA_DIR is required: set it in backend/.env — unified data root; stock.db lives in this directory")
	}
	if err := os.MkdirAll(stockDbDir, 0o755); err != nil {
		log.Fatalf("stock database dir (%s): %v", stockDbDir, err)
	}

	// ========== AI 分析：数据工具（Parquet / DuckDB 等）==========
	analysisDataDir := appenv.AnalysisDataDir("./data")
	if err := os.MkdirAll(analysisDataDir, 0o755); err != nil {
		log.Fatalf("analysis data dir: %v", err)
	}
	if err := tools.InitGlobalTools(analysisDataDir); err != nil {
		log.Fatalf("init analysis tools: %v", err)
	}

	analysisHandler, analysisReports, err := analysisapi.BootstrapAnalysis(ctx)
	if err != nil {
		log.Fatalf("bootstrap analysis: %v", err)
	}
	defer func() {
		if analysisReports != nil {
			_ = analysisReports.Close()
		}
	}()

	// ========== 可选：工作区文件树（sidecar 默认关闭，避免 /data/workspace 强依赖）==========
	enableWorkspace := !sidecar || strings.TrimSpace(os.Getenv("OBSIDIAN_FS_ROOT")) != ""
	var (
		fsService     *filesystem.Service
		indexer       *tags.Indexer
		hub           *ws.Hub
		pluginService *plugins.Service
		root          string
	)
	if enableWorkspace {
		root = appenv.WorkspaceRoot()
		fmt.Println("root", root)
		if err := os.MkdirAll(root, 0o755); err != nil {
			if sidecar {
				log.Printf("sidecar: workspace root unavailable (%v); workspace routes disabled", err)
				enableWorkspace = false
			} else {
				log.Fatalf("failed to ensure data root: %v", err)
			}
		}
	}
	if enableWorkspace {
		docPath := filepath.Join(root, "data")
		var fsErr error
		fsService, fsErr = filesystem.NewService(docPath)
		if fsErr != nil {
			log.Fatalf("failed to init filesystem service: %v", fsErr)
		}

		indexer = tags.NewIndexer(root)
		if err := indexer.ReindexAll(); err != nil {
			log.Printf("tag indexer initial build failed: %v", err)
		}

		hub = ws.NewHub()
		go hub.Run()

		pluginsDir := filepath.Join(root, ".plugins")
		pluginDbPath := filepath.Join(root, ".plugins.db")
		pluginService, err = plugins.NewService(pluginDbPath, pluginsDir)
		if err != nil {
			log.Printf("plugin system disabled: %v", err)
			pluginService = nil
		}
		defer func() {
			if pluginService != nil {
				_ = pluginService.Close()
			}
		}()

		watcher, err := filesystem.NewWatcher(root, hub, indexer)
		if err != nil {
			log.Printf("fs watcher disabled: %v", err)
		} else {
			go watcher.Run()
			defer func() { _ = watcher.Close() }()
		}
	} else {
		log.Printf("workspace file tree disabled (sidecar or OBSIDIAN_FS_ROOT unset)")
		root = stockDbDir
	}

	config := clients.DefaultConfig()
	if token := os.Getenv("TUSHARE_TOKEN"); token != "" {
		config.TushareToken = token
	}

	dataDir := filepath.Join(root, "data")
	if !enableWorkspace {
		dataDir = filepath.Join(stockDbDir, "cache")
		_ = os.MkdirAll(dataDir, 0o755)
	}
	cacheLayer, err := cache.NewMultiLayerCache(dataDir)
	if err != nil {
		log.Printf("cache layer disabled: %v", err)
	}
	defer func() {
		if cacheLayer != nil {
			_ = cacheLayer.Close()
		}
	}()

	log.Printf("stock.db path: %s", filepath.Join(stockDbDir, "stock.db"))
	stockDB, err := stockdb.InitStockDatabase(stockDbDir)
	if err != nil {
		log.Printf("stock database disabled: %v", err)
	}

	stockRepo := sqlite.NewStockRepository(stockDB, cacheLayer, config)
	analysisHandler.SetStockRepository(stockRepo)
	mdCfg := mdlib.DefaultConfig()
	if config.CrawlTimeOut > 0 {
		mdCfg.Timeout = time.Duration(config.CrawlTimeOut) * time.Second
	}
	marketRepo := marketadapter.NewRepository(mdlib.NewClient(mdCfg))
	aiService := ai.NewAIService()

	stockHandler := wbhttp.NewStockHandler(stockRepo)
	marketHandler := wbhttp.NewMarketHandler(marketRepo)
	legacyAIHandler := wbhttp.NewAIHandler(aiService)

	// Hotmoney / UZI：sidecar 默认不启；非 sidecar 失败也不再 Fatal（可懒加载）
	var hotMoneyHandler *wbhttp.HotMoneyHandler
	enableHotmoney := !sidecar || appenv.EnvEnabled("AISTOCK_ENABLE_HOTMONEY")
	if enableHotmoney {
		log.Printf("hotmoney.db path: %s", filepath.Join(stockDbDir, "hotmoney.db"))
		hm, hmErr := wbhttp.NewHotMoneyHandler(wbhttp.HotMoneyHandlerConfig{
			DataDir:   analysisDataDir,
			SessionDB: filepath.Join(stockDbDir, "hotmoney.db"),
			StockRepo: stockRepo,
		})
		if hmErr != nil {
			log.Printf("hotmoney pipeline disabled: %v", hmErr)
		} else {
			hotMoneyHandler = hm
		}
	} else {
		log.Printf("sidecar: hotmoney skipped (set AISTOCK_ENABLE_HOTMONEY=1 to enable)")
	}

	var stockV1Handler *wbhttp.StockV1Handler
	stockV1Client, err := stocklib.NewClientWithConfig(buildStockAPIConfig(stockDbDir))
	if err != nil {
		log.Printf("stockapi client disabled: %v", err)
	} else {
		stockV1Handler = wbhttp.NewStockV1Handler(stockV1Client)
	}

	r := gin.Default()

	r.Use(cors.New(cors.Config{
		AllowAllOrigins:  true,
		AllowMethods:     []string{"GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"},
		AllowHeaders:     []string{"Origin", "Content-Type", "Accept", "Authorization", "X-Request-Id", "X-User-Id", "X-User-Email", "X-User-Name", "X-User-Role"},
		ExposeHeaders:    []string{"Content-Length"},
		AllowCredentials: false,
		MaxAge:           12 * time.Hour,
	}))

	// Auth：sidecar 下可选（analysis/picker/market 无需登录）；仍挂 /auth 供兼容
	authDBPath := filepath.Join(stockDbDir, "auth.db")
	if p := strings.TrimSpace(os.Getenv("AUTH_DB_PATH")); p != "" {
		authDBPath = filepath.Clean(p)
	}
	if err := os.MkdirAll(filepath.Dir(authDBPath), 0o755); err != nil {
		log.Fatalf("auth database dir (%s): %v", filepath.Dir(authDBPath), err)
	}
	log.Printf("auth.db path: %s", authDBPath)
	jwtSecret := strings.TrimSpace(os.Getenv("AUTH_JWT_SECRET"))
	if jwtSecret == "" {
		jwtSecret = "dev-insecure-jwt-secret-change-me"
		log.Println("WARNING: AUTH_JWT_SECRET is empty; set it for production")
	}
	authSvc, err := auth.NewService(authDBPath, jwtSecret)
	if err != nil {
		if sidecar {
			log.Printf("sidecar: auth service disabled (%v); APIs remain open", err)
			authSvc = nil
		} else {
			log.Fatalf("auth: %v", err)
		}
	}
	if authSvc != nil {
		defer func() { _ = authSvc.Close() }()
	}

	// ---------- 对外统一版本化 API：/api/v1/* ----------
	// Sidecar：analysis / picker / market 无需鉴权中间件（内网信任或由 panel 会话把关）。
	v1 := r.Group("/api/v1")
	v1.GET("/health", wbhttp.Health)
	v1.GET("/vip-status", wbhttp.GetVipStatus)
	if authSvc != nil {
		auth.RegisterRoutes(v1.Group("/auth"), authSvc)
	}

	if enableWorkspace && fsService != nil && hub != nil && indexer != nil {
		wbhttp.RegisterWorkspaceRoutes(v1.Group("/workspace"), fsService, hub, indexer, root)
	}
	stockHandler.RegisterRoutes(v1) // includes /picker/*
	if hotMoneyHandler != nil {
		hotMoneyHandler.RegisterRoutes(v1)
	}
	marketHandler.RegisterRoutes(v1)
	legacyAIHandler.RegisterRoutes(v1)
	if stockV1Handler != nil {
		stockV1Handler.RegisterRoutes(v1.Group("/market-data"))
	}
	if pluginService != nil {
		plugins.RegisterPluginRoutes(v1, pluginService)
	}

	if !sidecar || appenv.EnvEnabled("AISTOCK_ENABLE_BACKTEST_PROXY") {
		v1BacktestPrefix := "/api/v1/backtest"
		r.Any(v1BacktestPrefix, proxy.BacktestReverseProxyHandlerWithPrefix(v1BacktestPrefix))
		r.Any(v1BacktestPrefix+"/*path", proxy.BacktestReverseProxyHandlerWithPrefix(v1BacktestPrefix))
	}

	if !sidecar || appenv.EnvEnabled("AISTOCK_ENABLE_DAILYAPI_PROXY") {
		dailyAPIPrefix := "/daily-api"
		r.Any(dailyAPIPrefix, proxy.DailyAPIReverseProxyHandlerWithPrefix(dailyAPIPrefix))
		r.Any(dailyAPIPrefix+"/*path", proxy.DailyAPIReverseProxyHandlerWithPrefix(dailyAPIPrefix))
	}

	tdxEnabled := strings.TrimSpace(os.Getenv("TDX_ENABLED")) != "0"
	if sidecar && !appenv.EnvEnabled("AISTOCK_ENABLE_TDX") && strings.TrimSpace(os.Getenv("TDX_ENABLED")) == "" {
		// sidecar 默认不启 TDX（避免后台连库拖慢启动）；显式 TDX_ENABLED=1 或 AISTOCK_ENABLE_TDX=1 可开
		tdxEnabled = false
	}
	if tdxEnabled {
		tdxLazy := tdxapi.NewLazyHandler()
		mountTDX := func(prefix string) {
			h := http.StripPrefix(prefix, tdxLazy)
			r.Any(prefix, gin.WrapH(h))
			r.Any(prefix+"/*path", gin.WrapH(h))
		}
		mountTDX("/api/v1/tdx")
	}

	if enableWorkspace && hub != nil {
		r.GET("/ws", func(c *gin.Context) {
			ws.ServeWS(hub, c.Writer, c.Request)
		})
	}

	// /healthz 仅在 RegisterAnalysisRoutes 内注册一份，避免重复 panic
	analysisapi.RegisterAnalysisRoutes(r, analysisHandler)

	// 内嵌前端：sidecar 默认关闭；设 AISTOCK_SERVE_WEB=1 可强制开启
	serveWeb := !sidecar && !appenv.EnvDisabled("AISTOCK_SERVE_WEB")
	if appenv.EnvEnabled("AISTOCK_SERVE_WEB") {
		serveWeb = true
	}
	if serveWeb {
		analysisapi.RegisterAnalysisStatic(r)
	} else {
		log.Printf("embedded web UI disabled (sidecar or AISTOCK_SERVE_WEB=0)")
	}

	addr := ":8787"
	if env := os.Getenv("PORT"); env != "" {
		addr = ":" + env
	}
	log.Printf("aistock backend listening on %s, root=%s sidecar=%v", addr, root, sidecar)
	log.Printf("HTTP API: /api/v1/analysis, /api/v1/picker, /api/v1/market (+ optional extras); /healthz")
	if err := r.Run(addr); err != nil {
		log.Fatal(err)
	}
}

func buildStockAPIConfig(defaultDataDir string) stocklib.Config {
	cfg := stocklib.DefaultConfig()

	if mode := strings.TrimSpace(os.Getenv("STOCKAPI_CACHE_MODE")); mode != "" {
		switch strings.ToLower(mode) {
		case "disabled":
			cfg.CacheMode = stocklib.CacheModeDisabled
		case "readonly":
			cfg.CacheMode = stocklib.CacheModeReadOnly
		default:
			cfg.CacheMode = stocklib.CacheModeAuto
		}
	}

	if dir := strings.TrimSpace(os.Getenv("AI_DATA_DIR")); dir != "" {
		cfg.DataDir = dir
	} else {
		cfg.DataDir = defaultDataDir
	}

	if v := parseDurationEnv("STOCKAPI_QUOTES_TTL"); v > 0 {
		cfg.QuotesTTL = v
	}
	if v := parseDurationEnv("STOCKAPI_HISTORY_TTL"); v > 0 {
		cfg.HistoryTTL = v
	}
	if v := parseDurationEnv("STOCKAPI_TIMELINE_TTL"); v > 0 {
		cfg.TimelineTTL = v
	}
	if v := parseIntEnv("STOCKAPI_BATCH_SIZE"); v > 0 {
		cfg.BatchSize = v
	}
	if v := parseIntEnv("STOCKAPI_CONCURRENCY"); v > 0 {
		cfg.Concurrency = v
	}

	return cfg
}

func parseDurationEnv(key string) time.Duration {
	raw := strings.TrimSpace(os.Getenv(key))
	if raw == "" {
		return 0
	}
	v, err := time.ParseDuration(raw)
	if err != nil {
		return 0
	}
	return v
}

func parseIntEnv(key string) int {
	raw := strings.TrimSpace(os.Getenv(key))
	if raw == "" {
		return 0
	}
	v, err := strconv.Atoi(raw)
	if err != nil {
		return 0
	}
	return v
}
