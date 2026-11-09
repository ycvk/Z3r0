package main

import (
	"bytes"
	"encoding/json"
	"io"
	"net/http"
	"strconv"
	"strings"
	"sync"
	"time"
)

const (
	maxTrafficEntries   = 500
	maxTrafficBodyBytes = 64 * 1024
)

type proxyTrafficEntry struct {
	Seq             int64             `json:"seq"`
	Method          string            `json:"method"`
	URL             string            `json:"url"`
	Host            string            `json:"host"`
	Path            string            `json:"path"`
	StatusCode      int               `json:"status_code"`
	RequestHeaders  map[string]string `json:"request_headers"`
	RequestBody     string            `json:"request_body"`
	ResponseHeaders map[string]string `json:"response_headers"`
	ResponseBody    string            `json:"response_body"`
	ResponseBytes   int64             `json:"response_bytes"`
	DurationMs      int64             `json:"duration_ms"`
	CreatedAt       string            `json:"created_at"`
}

type trafficRing struct {
	mu      sync.RWMutex
	nextSeq int64
	entries []proxyTrafficEntry
}

var sandboxTraffic = &trafficRing{
	entries: make([]proxyTrafficEntry, 0, maxTrafficEntries),
}

func (t *trafficRing) Record(entry proxyTrafficEntry) {
	t.mu.Lock()
	defer t.mu.Unlock()
	t.nextSeq++
	entry.Seq = t.nextSeq
	if entry.CreatedAt == "" {
		entry.CreatedAt = time.Now().UTC().Format(time.RFC3339Nano)
	}
	if len(t.entries) >= maxTrafficEntries {
		copy(t.entries, t.entries[1:])
		t.entries[len(t.entries)-1] = entry
		return
	}
	t.entries = append(t.entries, entry)
}

func (t *trafficRing) Since(afterSeq int64) ([]proxyTrafficEntry, int64) {
	t.mu.RLock()
	defer t.mu.RUnlock()
	out := make([]proxyTrafficEntry, 0, len(t.entries))
	for _, item := range t.entries {
		if item.Seq > afterSeq {
			out = append(out, item)
		}
	}
	return out, t.nextSeq
}

func (t *trafficRing) Clear() {
	t.mu.Lock()
	defer t.mu.Unlock()
	t.entries = t.entries[:0]
}

func handleTraffic(w http.ResponseWriter, r *http.Request) {
	switch r.Method {
	case http.MethodGet:
		afterSeq, _ := strconv.ParseInt(r.URL.Query().Get("after_seq"), 10, 64)
		items, lastSeq := sandboxTraffic.Since(afterSeq)
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(map[string]any{
			"items":    items,
			"last_seq": lastSeq,
		})
	case http.MethodDelete:
		sandboxTraffic.Clear()
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"ok":true}`))
	default:
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
	}
}

func flattenHeader(h http.Header) map[string]string {
	out := make(map[string]string, len(h))
	for k, vals := range h {
		out[k] = strings.Join(vals, ", ")
	}
	return out
}

func isBinaryContentType(contentType string) bool {
	ct := strings.ToLower(strings.TrimSpace(strings.Split(contentType, ";")[0]))
	if ct == "" {
		return false
	}
	for _, prefix := range []string{"image/", "font/", "audio/", "video/"} {
		if strings.HasPrefix(ct, prefix) {
			return true
		}
	}
	switch ct {
	case "application/octet-stream", "application/zip", "application/gzip", "application/pdf", "application/wasm":
		return true
	default:
		return false
	}
}

type tapReadCloser struct {
	rc  io.ReadCloser
	buf bytes.Buffer
}

func (t *tapReadCloser) Read(p []byte) (int, error) {
	n, err := t.rc.Read(p)
	if n > 0 && t.buf.Len() < maxTrafficBodyBytes {
		rem := maxTrafficBodyBytes - t.buf.Len()
		if n < rem {
			rem = n
		}
		t.buf.Write(p[:rem])
	}
	return n, err
}

func (t *tapReadCloser) Close() error {
	return t.rc.Close()
}
