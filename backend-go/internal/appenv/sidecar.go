package appenv

import (
	"os"
	"strings"
)

// SidecarMode reports whether the process should run as a tickflow-stock-panel
// sidecar API (analysis + picker + market) rather than a standalone SaaS.
//
// Enabled when AISTOCK_SIDECAR is 1/true/yes/on (case-insensitive).
// Default is off so legacy standalone deployments keep full behavior.
func SidecarMode() bool {
	return EnvEnabled("AISTOCK_SIDECAR")
}

// EnvEnabled is true for common truthy env values: 1, true, yes, on.
func EnvEnabled(key string) bool {
	v := strings.ToLower(strings.TrimSpace(os.Getenv(key)))
	switch v {
	case "1", "true", "yes", "on":
		return true
	default:
		return false
	}
}

// EnvDisabled is true when the env is explicitly 0/false/no/off.
func EnvDisabled(key string) bool {
	v := strings.ToLower(strings.TrimSpace(os.Getenv(key)))
	switch v {
	case "0", "false", "no", "off":
		return true
	default:
		return false
	}
}
