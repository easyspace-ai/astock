// Package prompts manages TradingAgents system-prompt defaults and runtime overrides.
// Overrides persist under the analysis data directory as prompts_override.json so edits
// survive restarts without rewriting Go source.
package prompts

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"sync"
)

const overrideFileName = "prompts_override.json"

// Entry is one agent prompt as returned by the admin API.
type Entry struct {
	ID         string `json:"id"`
	Name       string `json:"name"`
	Label      string `json:"label"`
	Prompt     string `json:"prompt"`
	Overridden bool   `json:"overridden"`
}

type meta struct {
	ID    string
	Name  string
	Label string
}

var (
	mu        sync.RWMutex
	registry  []meta
	defaults  = map[string]string{}
	overrides = map[string]string{}
	filePath  string
)

// Register adds a compiled-in default. Safe to call from package init.
func Register(id, name, label, defaultPrompt string) {
	mu.Lock()
	defer mu.Unlock()
	if _, exists := defaults[id]; exists {
		defaults[id] = defaultPrompt
		for i := range registry {
			if registry[i].ID == id {
				registry[i] = meta{ID: id, Name: name, Label: label}
				return
			}
		}
	}
	defaults[id] = defaultPrompt
	registry = append(registry, meta{ID: id, Name: name, Label: label})
}

// Init loads overrides from dataDir/prompts_override.json.
// dataDir empty falls back to ./data.
func Init(dataDir string) error {
	if dataDir == "" {
		dataDir = "./data"
	}
	path := filepath.Join(dataDir, overrideFileName)
	mu.Lock()
	defer mu.Unlock()
	filePath = path

	data, err := os.ReadFile(path)
	if err != nil {
		if os.IsNotExist(err) {
			overrides = map[string]string{}
			return nil
		}
		return fmt.Errorf("read prompt overrides: %w", err)
	}
	var loaded map[string]string
	if err := json.Unmarshal(data, &loaded); err != nil {
		return fmt.Errorf("parse prompt overrides: %w", err)
	}
	if loaded == nil {
		loaded = map[string]string{}
	}
	overrides = loaded
	return nil
}

// Resolve returns the override for id if present, otherwise fallback (usually the package const).
func Resolve(id, fallback string) string {
	mu.RLock()
	defer mu.RUnlock()
	if v, ok := overrides[id]; ok && v != "" {
		return v
	}
	if fallback != "" {
		return fallback
	}
	return defaults[id]
}

// List returns all registered agents with effective prompt text.
func List() []Entry {
	mu.RLock()
	defer mu.RUnlock()
	out := make([]Entry, 0, len(registry))
	for _, m := range registry {
		prompt, overridden := effectiveLocked(m.ID)
		out = append(out, Entry{
			ID:         m.ID,
			Name:       m.Name,
			Label:      m.Label,
			Prompt:     prompt,
			Overridden: overridden,
		})
	}
	return out
}

// Get returns one agent prompt by id.
func Get(id string) (Entry, bool) {
	mu.RLock()
	defer mu.RUnlock()
	for _, m := range registry {
		if m.ID != id {
			continue
		}
		prompt, overridden := effectiveLocked(m.ID)
		return Entry{
			ID:         m.ID,
			Name:       m.Name,
			Label:      m.Label,
			Prompt:     prompt,
			Overridden: overridden,
		}, true
	}
	return Entry{}, false
}

// Set persists an override for id and returns the updated entry.
func Set(id, prompt string) (Entry, error) {
	mu.Lock()
	defer mu.Unlock()
	var found *meta
	for i := range registry {
		if registry[i].ID == id {
			found = &registry[i]
			break
		}
	}
	if found == nil {
		return Entry{}, fmt.Errorf("unknown prompt id: %s", id)
	}
	if overrides == nil {
		overrides = map[string]string{}
	}
	overrides[id] = prompt
	if err := saveLocked(); err != nil {
		return Entry{}, err
	}
	return Entry{
		ID:         found.ID,
		Name:       found.Name,
		Label:      found.Label,
		Prompt:     prompt,
		Overridden: true,
	}, nil
}

func effectiveLocked(id string) (prompt string, overridden bool) {
	if v, ok := overrides[id]; ok && v != "" {
		return v, true
	}
	return defaults[id], false
}

func saveLocked() error {
	if filePath == "" {
		return fmt.Errorf("prompt store not initialized")
	}
	if err := os.MkdirAll(filepath.Dir(filePath), 0o755); err != nil {
		return fmt.Errorf("create prompt override dir: %w", err)
	}
	data, err := json.MarshalIndent(overrides, "", "  ")
	if err != nil {
		return err
	}
	tmp := filePath + ".tmp"
	if err := os.WriteFile(tmp, data, 0o644); err != nil {
		return fmt.Errorf("write prompt overrides: %w", err)
	}
	if err := os.Rename(tmp, filePath); err != nil {
		_ = os.Remove(tmp)
		return fmt.Errorf("replace prompt overrides: %w", err)
	}
	return nil
}
