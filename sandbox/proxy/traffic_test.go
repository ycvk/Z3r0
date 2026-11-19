package main

import (
	"bytes"
	"compress/flate"
	"compress/gzip"
	"compress/zlib"
	"crypto/tls"
	"crypto/x509"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"net/url"
	"path/filepath"
	"testing"
	"time"
)

// The proxy records an exchange after it has written the response, so a client can finish reading
// before the entry exists. waitForTrafficEntries returns once the ring holds want entries, or what
// it holds after five seconds.
func waitForTrafficEntries(want int) ([]proxyTrafficEntry, int64) {
	deadline := time.Now().Add(5 * time.Second)
	for {
		entries, lastSeq := sandboxTraffic.Since(0)
		if len(entries) >= want || time.Now().After(deadline) {
			return entries, lastSeq
		}
		time.Sleep(5 * time.Millisecond)
	}
}

func TestDecodeCapturedBodyDecodesSupportedCodingsOnly(t *testing.T) {
	text := []byte(`{"flag":"FLAG{decoded}"}`)

	var gz bytes.Buffer
	gw := gzip.NewWriter(&gz)
	_, _ = gw.Write(text)
	_ = gw.Close()

	var zl bytes.Buffer
	zw := zlib.NewWriter(&zl)
	_, _ = zw.Write(text)
	_ = zw.Close()

	var raw bytes.Buffer
	fw, _ := flate.NewWriter(&raw, flate.DefaultCompression)
	_, _ = fw.Write(text)
	_ = fw.Close()

	binary := []byte{0x08, 0x96, 0x01, 0x00, 0x12, 0x00, 0xff}
	var gzBinary bytes.Buffer
	bw := gzip.NewWriter(&gzBinary)
	_, _ = bw.Write(binary)
	_ = bw.Close()

	cases := []struct {
		name     string
		body     []byte
		encoding string
		want     string
	}{
		{"identity", text, "", string(text)},
		{"gzip", gz.Bytes(), "gzip", string(text)},
		{"gzip mixed case", gz.Bytes(), " GZip ", string(text)},
		{"zlib deflate", zl.Bytes(), "deflate", string(text)},
		{"raw deflate", raw.Bytes(), "deflate", string(text)},
		{"binary identity", binary, "", ""},
		{"binary inside gzip", gzBinary.Bytes(), "gzip", ""},
		{"brotli is not decodable", []byte{0x1b, 0x00, 0x00, 0xf8, 0x25}, "br", ""},
		{"zstd is not decodable", []byte{0x28, 0xb5, 0x2f, 0xfd, 0x00}, "zstd", ""},
		{"corrupt gzip", []byte("not a gzip stream"), "gzip", ""},
		{"stacked codings", gz.Bytes(), "gzip, br", ""},
	}
	for _, tc := range cases {
		if got := decodeCapturedBody(tc.body, tc.encoding); got != tc.want {
			t.Errorf("%s: decodeCapturedBody = %q, want %q", tc.name, got, tc.want)
		}
	}
}

func TestNarrowAcceptEncoding(t *testing.T) {
	cases := []struct {
		in      []string
		want    string
		present bool
	}{
		{[]string{"gzip, deflate, br, zstd"}, "gzip, deflate", true},
		{[]string{"br;q=1.0, gzip;q=0.8, *;q=0.1"}, "gzip;q=0.8", true},
		{[]string{"br", "zstd"}, "identity", true},
		{[]string{"GZIP"}, "GZIP", true},
		{nil, "", false},
	}
	for _, tc := range cases {
		h := http.Header{}
		for _, v := range tc.in {
			h.Add("Accept-Encoding", v)
		}
		narrowAcceptEncoding(h)
		_, present := h["Accept-Encoding"]
		if present != tc.present || h.Get("Accept-Encoding") != tc.want {
			t.Errorf("narrowAcceptEncoding(%q) = %q (present=%v), want %q (present=%v)",
				tc.in, h.Get("Accept-Encoding"), present, tc.want, tc.present)
		}
	}
}

func TestProxyForwardsOnlyDecodableAcceptEncodings(t *testing.T) {
	dir := t.TempDir()
	authority, err := loadOrCreateMITMAuthority(filepath.Join(dir, "ca.crt"), filepath.Join(dir, "ca.key"))
	if err != nil {
		t.Fatalf("ca: %v", err)
	}
	prevCA := mitmCA
	mitmCA = authority
	defer func() { mitmCA = prevCA }()
	sandboxTraffic.Clear()

	echo := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/plain")
		_, _ = w.Write([]byte("upstream-saw:" + r.Header.Get("Accept-Encoding")))
	})
	plainUpstream := httptest.NewServer(echo)
	defer plainUpstream.Close()
	tlsUpstream := httptest.NewTLSServer(echo)
	defer tlsUpstream.Close()

	egressProxy := httptest.NewServer(http.HandlerFunc(handleEgressProxyRequest))
	defer egressProxy.Close()
	proxyURL, err := url.Parse(egressProxy.URL)
	if err != nil {
		t.Fatalf("parse proxy url: %v", err)
	}
	pool := x509.NewCertPool()
	pool.AppendCertsFromPEM(authority.caPEM)
	client := &http.Client{
		Timeout: 10 * time.Second,
		Transport: &http.Transport{
			Proxy:           http.ProxyURL(proxyURL),
			TLSClientConfig: &tls.Config{RootCAs: pool},
		},
	}

	for _, target := range []string{plainUpstream.URL, tlsUpstream.URL} {
		req, _ := http.NewRequest(http.MethodGet, target+"/page", nil)
		req.Header.Set("Accept-Encoding", "gzip, deflate, br, zstd")
		resp, err := client.Do(req)
		if err != nil {
			t.Fatalf("GET %s via proxy: %v", target, err)
		}
		body, _ := io.ReadAll(resp.Body)
		_ = resp.Body.Close()
		if string(body) != "upstream-saw:gzip, deflate" {
			t.Fatalf("%s: upstream received Accept-Encoding %q", target, string(body))
		}
	}

	entries, _ := waitForTrafficEntries(2)
	if len(entries) != 2 {
		t.Fatalf("expected 2 captured entries, got %d", len(entries))
	}
	for _, entry := range entries {
		if entry.RequestHeaders["Accept-Encoding"] != "gzip, deflate" {
			t.Errorf("%s: recorded Accept-Encoding %q", entry.URL, entry.RequestHeaders["Accept-Encoding"])
		}
		if entry.ResponseBody != "upstream-saw:gzip, deflate" {
			t.Errorf("%s: recorded response body %q", entry.URL, entry.ResponseBody)
		}
	}
}

func TestTrafficEndpointScopesCursorToProxyBoot(t *testing.T) {
	ring := &trafficRing{bootID: "boot-current", entries: make([]proxyTrafficEntry, 0, maxTrafficEntries)}
	prev := sandboxTraffic
	sandboxTraffic = ring
	defer func() { sandboxTraffic = prev }()

	for _, path := range []string{"/one", "/two", "/three"} {
		ring.Record(proxyTrafficEntry{Method: "GET", URL: "http://t" + path, Path: path, StatusCode: 200})
	}

	fetch := func(query string) (string, []proxyTrafficEntry) {
		rec := httptest.NewRecorder()
		handleTraffic(rec, httptest.NewRequest(http.MethodGet, "/traffic?"+query, nil))
		var payload struct {
			BootID string              `json:"boot_id"`
			Items  []proxyTrafficEntry `json:"items"`
		}
		if err := json.Unmarshal(rec.Body.Bytes(), &payload); err != nil {
			t.Fatalf("decode /traffic payload: %v", err)
		}
		return payload.BootID, payload.Items
	}

	bootID, items := fetch("after_seq=2&boot_id=boot-current")
	if bootID != "boot-current" || len(items) != 1 || items[0].Path != "/three" {
		t.Fatalf("same boot must honour the cursor, got boot=%q items=%+v", bootID, items)
	}

	// A cursor taken from an earlier proxy process must not hide this process's entries.
	bootID, items = fetch("after_seq=2&boot_id=boot-previous")
	if bootID != "boot-current" || len(items) != 3 {
		t.Fatalf("stale boot cursor must return every entry, got boot=%q items=%d", bootID, len(items))
	}

	if a, b := newTrafficBootID(), newTrafficBootID(); a == "" || a == b {
		t.Fatalf("boot ids must be non-empty and unique, got %q and %q", a, b)
	}
}
