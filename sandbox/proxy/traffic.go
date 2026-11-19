package main

import (
	"bytes"
	"compress/flate"
	"compress/gzip"
	"compress/zlib"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"io"
	"net/http"
	"strconv"
	"strings"
	"sync"
	"time"
)

const (
	maxTrafficEntries   = 2000
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
	bootID  string
	nextSeq int64
	entries []proxyTrafficEntry
}

var sandboxTraffic = &trafficRing{
	bootID:  newTrafficBootID(),
	entries: make([]proxyTrafficEntry, 0, maxTrafficEntries),
}

// newTrafficBootID identifies one proxy process: sequence numbers restart with the process,
// so a cursor is only meaningful together with the boot it was taken from.
func newTrafficBootID() string {
	raw := make([]byte, 16)
	_, _ = rand.Read(raw)
	return hex.EncodeToString(raw)
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
		if r.URL.Query().Get("boot_id") != sandboxTraffic.bootID {
			afterSeq = 0
		}
		items, lastSeq := sandboxTraffic.Since(afterSeq)
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(map[string]any{
			"boot_id":  sandboxTraffic.bootID,
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

// narrowAcceptEncoding limits the codings offered upstream to those decodeCapturedBody can read,
// so captured response bodies stay searchable text.
func narrowAcceptEncoding(h http.Header) {
	values, ok := h["Accept-Encoding"]
	if !ok {
		return
	}
	kept := make([]string, 0, 2)
	for _, value := range values {
		for _, token := range strings.Split(value, ",") {
			token = strings.TrimSpace(token)
			coding, _, _ := strings.Cut(token, ";")
			switch strings.ToLower(strings.TrimSpace(coding)) {
			case "gzip", "x-gzip", "deflate", "identity":
				kept = append(kept, token)
			}
		}
	}
	if len(kept) == 0 {
		kept = append(kept, "identity")
	}
	h.Set("Accept-Encoding", strings.Join(kept, ", "))
}

// decodeCapturedBody returns the captured payload as text, or "" when it cannot be decoded
// or is binary (contains NUL).
func decodeCapturedBody(raw []byte, contentEncoding string) string {
	if len(raw) == 0 {
		return ""
	}
	decoded, ok := decodeContentEncoding(raw, contentEncoding)
	if !ok || bytes.IndexByte(decoded, 0) >= 0 {
		return ""
	}
	return string(decoded)
}

func decodeContentEncoding(raw []byte, contentEncoding string) ([]byte, bool) {
	switch strings.ToLower(strings.TrimSpace(contentEncoding)) {
	case "", "identity":
		return raw, true
	case "gzip", "x-gzip":
		gr, err := gzip.NewReader(bytes.NewReader(raw))
		if err != nil {
			return nil, false
		}
		defer gr.Close()
		return readCapturedPrefix(gr)
	case "deflate":
		if zr, err := zlib.NewReader(bytes.NewReader(raw)); err == nil {
			defer zr.Close()
			return readCapturedPrefix(zr)
		}
		fr := flate.NewReader(bytes.NewReader(raw))
		defer fr.Close()
		return readCapturedPrefix(fr)
	default:
		return nil, false
	}
}

// readCapturedPrefix tolerates the unexpected EOF of a capture truncated at maxTrafficBodyBytes.
func readCapturedPrefix(r io.Reader) ([]byte, bool) {
	decoded, _ := io.ReadAll(io.LimitReader(r, maxTrafficBodyBytes))
	return decoded, len(decoded) > 0
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

type countingTapReadCloser struct {
	rc         io.ReadCloser
	capture    bool
	buf        bytes.Buffer
	totalBytes int64
}

func (c *countingTapReadCloser) Read(p []byte) (int, error) {
	n, err := c.rc.Read(p)
	if n > 0 {
		c.totalBytes += int64(n)
		if c.capture && c.buf.Len() < maxTrafficBodyBytes {
			rem := maxTrafficBodyBytes - c.buf.Len()
			if n < rem {
				rem = n
			}
			c.buf.Write(p[:rem])
		}
	}
	return n, err
}

func (c *countingTapReadCloser) Close() error {
	return c.rc.Close()
}
