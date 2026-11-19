package tools

import "testing"

func TestDatePrefix(t *testing.T) {
	t.Parallel()

	tests := []struct {
		name string
		in   string
		want string
	}{
		{name: "empty", in: "", want: ""},
		{name: "short", in: "2026-07", want: "2026-07"},
		{name: "exact", in: "2026-07-07", want: "2026-07-07"},
		{name: "datetime", in: "2026-07-07T15:04:05", want: "2026-07-07"},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			t.Parallel()
			if got := datePrefix(tt.in); got != tt.want {
				t.Fatalf("datePrefix(%q) = %q, want %q", tt.in, got, tt.want)
			}
		})
	}
}

func TestDatePrefixEmptyDoesNotPanic(t *testing.T) {
	t.Parallel()
	defer func() {
		if r := recover(); r != nil {
			t.Fatalf("datePrefix panicked on empty input: %v", r)
		}
	}()
	_ = datePrefix("")
}
