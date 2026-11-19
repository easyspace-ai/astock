package appenv

import (
	"testing"
)

func TestSidecarMode(t *testing.T) {
	t.Setenv("AISTOCK_SIDECAR", "")
	if SidecarMode() {
		t.Fatal("expected sidecar off when unset")
	}
	t.Setenv("AISTOCK_SIDECAR", "1")
	if !SidecarMode() {
		t.Fatal("expected sidecar on for 1")
	}
	t.Setenv("AISTOCK_SIDECAR", "true")
	if !SidecarMode() {
		t.Fatal("expected sidecar on for true")
	}
	t.Setenv("AISTOCK_SIDECAR", "0")
	if SidecarMode() {
		t.Fatal("expected sidecar off for 0")
	}
}
