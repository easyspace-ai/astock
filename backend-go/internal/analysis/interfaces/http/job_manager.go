package httpapi

import (
	"context"
	"fmt"
	"log"
	"sort"
	"sync"
	"time"

	"github.com/google/uuid"
)

// JobManager 任务管理器
type JobManager struct {
	jobs map[string]*Job
	mu   sync.RWMutex

	// SSE 广播
	eventListeners map[string]chan<- JobEvent
	listenerMu     sync.RWMutex

	// 可选：与 Python 一致将 job 结果落入 SQLite reports
	reportStore *ReportStore
}

// SetReportStore 设置报告持久化（由 Server 在启动时注入）。
func (jm *JobManager) SetReportStore(s *ReportStore) {
	jm.reportStore = s
}

func (jm *JobManager) persistJobReportCompleted(jobID string, req AnalysisRequest, result, payload map[string]any) {
	if jm.reportStore == nil {
		return
	}
	td := req.TradeDate
	if td == "" {
		td = todayCN()
	}
	if err := jm.reportStore.UpsertCompleted(jobID, "", req.Symbol, td, result, payload); err != nil {
		log.Printf("report store: persist completed job %s: %v", jobID, err)
	}
}

func (jm *JobManager) persistJobReportFailed(jobID string, req AnalysisRequest, errMsg string) {
	if jm.reportStore == nil || errMsg == "" {
		return
	}
	td := req.TradeDate
	if td == "" {
		td = todayCN()
	}
	if err := jm.reportStore.UpsertFailed(jobID, "", req.Symbol, td, errMsg); err != nil {
		log.Printf("report store: persist failed job %s: %v", jobID, err)
	}
}

// NewJobManager 创建任务管理器
func NewJobManager() *JobManager {
	return &JobManager{
		jobs:           make(map[string]*Job),
		eventListeners: make(map[string]chan<- JobEvent),
	}
}

// CreateJob 创建新任务
func (jm *JobManager) CreateJob(req AnalysisRequest) *Job {
	jobID := uuid.NewString()
	now := time.Now()

	job := &Job{
		ID:        jobID,
		Status:    JobStatusPending,
		Request:   req,
		CreatedAt: now,
		UpdatedAt: now,
		Events:    []JobEvent{},
	}

	jm.mu.Lock()
	jm.jobs[jobID] = job
	jm.mu.Unlock()

	jm.addEvent(jobID, JobEvent{
		Timestamp: now,
		Type:      "job.created",
		Message:   "任务已创建",
		Data: map[string]any{
			"job_id":     jobID,
			"symbol":     req.Symbol,
			"trade_date": req.TradeDate,
		},
	})

	return job
}

// GetJob 获取任务
func (jm *JobManager) GetJob(jobID string) (*Job, bool) {
	jm.mu.RLock()
	defer jm.mu.RUnlock()
	job, ok := jm.jobs[jobID]
	return job, ok
}

// UpdateJobStatus 更新任务状态
func (jm *JobManager) UpdateJobStatus(jobID, status string) {
	jm.mu.Lock()
	defer jm.mu.Unlock()

	if job, ok := jm.jobs[jobID]; ok {
		job.Status = status
		job.UpdatedAt = time.Now()

		jm.addEvent(jobID, JobEvent{
			Timestamp: time.Now(),
			Type:      "status_changed",
			Message:   fmtStatusMessage(status),
		})
	}
}

// SetJobResult 设置任务结果并广播 job.completed（供非工作流调用方使用）
func (jm *JobManager) SetJobResult(jobID string, result map[string]any) {
	jm.CommitJobResult(jobID, result)
	payload := completionPayload(result)
	mergePayloadExtras(result, payload)
	applyPayloadToResult(result, payload)
	jm.addEvent(jobID, JobEvent{
		Timestamp: time.Now(),
		Type:      "job.completed",
		Message:   "任务已完成",
		Data:      payload,
	})
}

// SetJobError 设置任务错误
func (jm *JobManager) SetJobError(jobID string, err error) {
	jm.CommitJobFailure(jobID)
	jm.addEvent(jobID, JobEvent{
		Timestamp: time.Now(),
		Type:      "job.failed",
		Message:   err.Error(),
		Data: map[string]any{
			"error": err.Error(),
		},
	})
}

// SubscribeEvents 订阅任务事件
func (jm *JobManager) SubscribeEvents(jobID string) (<-chan JobEvent, func()) {
	ch := make(chan JobEvent, 100)

	jm.listenerMu.Lock()
	jm.eventListeners[jobID] = ch
	jm.listenerMu.Unlock()

	// 锁内只拷贝历史事件切片；持锁期间绝不向 channel 同步发送，
	// 否则消费侧阻塞会让读锁不释放，进而饿死写锁（死锁）。
	jm.mu.RLock()
	var history []JobEvent
	if job, ok := jm.jobs[jobID]; ok {
		history = append(history, job.Events...)
	}
	jm.mu.RUnlock()

	for _, event := range history {
		select {
		case ch <- event:
		default: // 缓冲区满则丢弃溢出的历史事件，不阻塞订阅者
		}
	}

	unsubscribe := func() {
		jm.listenerMu.Lock()
		delete(jm.eventListeners, jobID)
		jm.listenerMu.Unlock()
		close(ch)
	}

	return ch, unsubscribe
}

// addEvent 添加事件（内部方法，需要已持有锁）
func (jm *JobManager) addEvent(jobID string, event JobEvent) {
	if job, ok := jm.jobs[jobID]; ok {
		job.Events = append(job.Events, event)
	}

	// 广播给监听器
	jm.listenerMu.RLock()
	if ch, ok := jm.eventListeners[jobID]; ok {
		select {
		case ch <- event:
		default:
		}
	}
	jm.listenerMu.RUnlock()
}

// fmtStatusMessage 格式化状态消息
func fmtStatusMessage(status string) string {
	switch status {
	case JobStatusPending:
		return "任务等待中"
	case JobStatusRunning:
		return "任务运行中"
	case JobStatusCompleted:
		return "任务已完成"
	case JobStatusFailed:
		return "任务失败"
	default:
		return "未知状态"
	}
}

// StartJob 开始执行任务（与 Chat SSE 共用 RunTradingWorkflow）。
// 任务在后台 goroutine 常驻运行：剥除 parentCtx 的请求取消信号，
// 客户端断连不会中断研判，仅 StopJob 或服务关闭才终止。
func (jm *JobManager) StartJob(parentCtx context.Context, jobID string) {
	jm.mu.RLock()
	job, ok := jm.jobs[jobID]
	jm.mu.RUnlock()
	if !ok {
		return
	}

	ctx, cancel := context.WithCancel(context.WithoutCancel(parentCtx))
	jm.mu.Lock()
	job.cancel = cancel
	jm.mu.Unlock()

	go func() {
		defer cancel()
		defer func() {
			if r := recover(); r != nil {
				jm.SetJobError(jobID, fmt.Errorf("analysis panic: %v", r))
			}
		}()
		emit := func(ev string, data map[string]any) {
			jm.addEvent(jobID, JobEvent{
				Timestamp: time.Now(),
				Type:      ev,
				Data:      data,
			})
		}
		_ = RunTradingWorkflow(ctx, jm, jobID, job.Request, emit)
	}()
}

// StopJob 手动停止在跑/等待中的任务；终态任务直接返回 nil。
func (jm *JobManager) StopJob(jobID string) error {
	jm.mu.RLock()
	job, ok := jm.jobs[jobID]
	jm.mu.RUnlock()
	if !ok {
		return fmt.Errorf("任务不存在")
	}

	jm.mu.RLock()
	active := job.Status == JobStatusPending || job.Status == JobStatusRunning
	cancel := job.cancel
	jm.mu.RUnlock()

	if !active {
		return nil
	}
	if cancel != nil {
		cancel()
	}

	jm.CommitJobFailure(jobID)
	jm.mu.Lock()
	if j, ok := jm.jobs[jobID]; ok {
		j.Error = "已手动停止"
	}
	jm.mu.Unlock()
	jm.addEvent(jobID, JobEvent{
		Timestamp: time.Now(),
		Type:      "job.failed",
		Message:   "任务已手动停止",
		Data:      map[string]any{"error": "已手动停止"},
	})
	return nil
}

// ListJobs 返回全部任务摘要，按创建时间倒序（最新在前）。
func (jm *JobManager) ListJobs() []JobSummary {
	jm.mu.RLock()
	defer jm.mu.RUnlock()
	out := make([]JobSummary, 0, len(jm.jobs))
	for _, job := range jm.jobs {
		out = append(out, JobSummary{
			JobID:     job.ID,
			Status:    job.Status,
			Symbol:    job.Request.Symbol,
			TradeDate: job.Request.TradeDate,
			Error:     job.Error,
			CreatedAt: job.CreatedAt,
			UpdatedAt: job.UpdatedAt,
		})
	}
	sort.Slice(out, func(i, j int) bool {
		return out[i].CreatedAt.After(out[j].CreatedAt)
	})
	return out
}
