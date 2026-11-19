package httpapi

import (
	"net/http"
	"strings"

	"aistock/backend/internal/analysis/prompts"

	"github.com/gin-gonic/gin"
)

// ListPrompts GET /api/v1/analysis/prompts
func (h *Handler) ListPrompts(c *gin.Context) {
	c.JSON(http.StatusOK, prompts.List())
}

// GetPrompt GET /api/v1/analysis/prompts/:id
func (h *Handler) GetPrompt(c *gin.Context) {
	id := strings.TrimSpace(c.Param("id"))
	entry, ok := prompts.Get(id)
	if !ok {
		c.JSON(http.StatusNotFound, gin.H{"detail": "提示词不存在"})
		return
	}
	c.JSON(http.StatusOK, entry)
}

type updatePromptRequest struct {
	Prompt string `json:"prompt"`
}

// UpdatePrompt PUT /api/v1/analysis/prompts/:id
func (h *Handler) UpdatePrompt(c *gin.Context) {
	id := strings.TrimSpace(c.Param("id"))
	var req updatePromptRequest
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"detail": "无效的请求参数"})
		return
	}
	if strings.TrimSpace(req.Prompt) == "" {
		c.JSON(http.StatusBadRequest, gin.H{"detail": "prompt 不能为空"})
		return
	}
	entry, err := prompts.Set(id, req.Prompt)
	if err != nil {
		if strings.Contains(err.Error(), "unknown prompt id") {
			c.JSON(http.StatusNotFound, gin.H{"detail": "提示词不存在"})
			return
		}
		c.JSON(http.StatusInternalServerError, gin.H{"detail": err.Error()})
		return
	}
	c.JSON(http.StatusOK, entry)
}
