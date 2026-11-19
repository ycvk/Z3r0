package main

import (
	"bufio"
	"bytes"
	"compress/gzip"
	"crypto/tls"
	"crypto/x509"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"net/url"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func TestHTTPSMITMDecryptAndRecord(t *testing.T) {
	dir := t.TempDir()
	certPath := filepath.Join(dir, "ca.crt")
	keyPath := filepath.Join(dir, "ca.key")

	authority, err := loadOrCreateMITMAuthority(certPath, keyPath)
	if err != nil {
		t.Fatalf("loadOrCreateMITMAuthority: %v", err)
	}
	reloaded, err := loadOrCreateMITMAuthority(certPath, keyPath)
	if err != nil {
		t.Fatalf("reload MITM CA: %v", err)
	}
	if !bytes.Equal(authority.caPEM, reloaded.caPEM) {
		t.Fatalf("expected persisted CA PEM to remain identical across restarts")
	}

	prevCA := mitmCA
	mitmCA = authority
	defer func() { mitmCA = prevCA }()
	sandboxTraffic.Clear()

	// Upstream HTTPS target with its own untrusted self-signed cert
	upstream := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, _ := io.ReadAll(r.Body)
		if r.URL.Path == "/gzip-secret" {
			w.Header().Set("Content-Type", "application/json")
			w.Header().Set("Content-Encoding", "gzip")
			w.WriteHeader(http.StatusOK)
			gz := gzip.NewWriter(w)
			_, _ = gz.Write([]byte(`{"flag":"FLAG{https_mitm_ok}","echo":"` + string(body) + `"}`))
			_ = gz.Close()
			return
		}
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusCreated)
		_, _ = w.Write([]byte(`{"status":"created","user":"operator"}`))
	}))
	defer upstream.Close()

	egressProxy := httptest.NewServer(http.HandlerFunc(handleEgressProxyRequest))
	defer egressProxy.Close()

	proxyURL, err := url.Parse(egressProxy.URL)
	if err != nil {
		t.Fatalf("parse proxy url: %v", err)
	}

	// Client ONLY trusts the sandbox MITM Root CA (not upstream's self-signed cert)
	pool := x509.NewCertPool()
	if !pool.AppendCertsFromPEM(authority.caPEM) {
		t.Fatalf("failed to append MITM CA to pool")
	}
	client := &http.Client{
		Timeout: 10 * time.Second,
		Transport: &http.Transport{
			Proxy:           http.ProxyURL(proxyURL),
			TLSClientConfig: &tls.Config{RootCAs: pool},
		},
	}

	// Request 1: POST with body over HTTPS (IP target SAN + self-signed upstream)
	resp1, err := client.Post(upstream.URL+"/api/tokens?scope=admin", "application/json", strings.NewReader(`{"secret":"hunter2"}`))
	if err != nil {
		t.Fatalf("HTTPS POST via MITM proxy failed: %v", err)
	}
	raw1, _ := io.ReadAll(resp1.Body)
	_ = resp1.Body.Close()
	if resp1.StatusCode != http.StatusCreated || !strings.Contains(string(raw1), `"created"`) {
		t.Fatalf("unexpected response 1: status=%d body=%s", resp1.StatusCode, string(raw1))
	}

	// Request 2: GET gzip-compressed JSON over the same keep-alive TLS session
	req2, _ := http.NewRequest(http.MethodGet, upstream.URL+"/gzip-secret", nil)
	req2.Header.Set("Accept-Encoding", "gzip")
	req2.Header.Set("X-Trace", "keep-alive-2")
	resp2, err := client.Do(req2)
	if err != nil {
		t.Fatalf("HTTPS GET gzip via MITM proxy failed: %v", err)
	}
	gzReader, err := gzip.NewReader(resp2.Body)
	if err != nil {
		t.Fatalf("expected gzip stream on wire to client: %v", err)
	}
	decodedWire, _ := io.ReadAll(gzReader)
	_ = gzReader.Close()
	_ = resp2.Body.Close()
	if !strings.Contains(string(decodedWire), "FLAG{https_mitm_ok}") {
		t.Fatalf("unexpected wire body 2: %s", string(decodedWire))
	}

	entries, lastSeq := sandboxTraffic.Since(0)
	if len(entries) != 2 || lastSeq < 2 {
		t.Fatalf("expected 2 captured HTTPS entries, got %d (lastSeq=%d)", len(entries), lastSeq)
	}
	if entries[0].Method != "POST" || !strings.HasPrefix(entries[0].URL, "https://") || entries[0].Path != "/api/tokens?scope=admin" {
		t.Fatalf("unexpected entry[0] metadata: %+v", entries[0])
	}
	if !strings.Contains(entries[0].RequestBody, "hunter2") || !strings.Contains(entries[0].ResponseBody, "operator") {
		t.Fatalf("expected decrypted bodies on entry[0], got req=%q resp=%q", entries[0].RequestBody, entries[0].ResponseBody)
	}
	if entries[1].Method != "GET" || entries[1].RequestHeaders["X-Trace"] != "keep-alive-2" {
		t.Fatalf("unexpected entry[1] headers: %+v", entries[1])
	}
	if !strings.Contains(entries[1].ResponseBody, "FLAG{https_mitm_ok}") {
		t.Fatalf("expected gunzipped response body in captured entry[1], got %q", entries[1].ResponseBody)
	}
}

func TestNonTLSConnectFallsBackToRawRelay(t *testing.T) {
	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatalf("listen: %v", err)
	}
	defer ln.Close()

	go func() {
		conn, err := ln.Accept()
		if err != nil {
			return
		}
		defer conn.Close()
		line, err := bufio.NewReader(conn).ReadString('\n')
		if err == nil {
			_, _ = conn.Write([]byte("ECHO:" + line))
		}
	}()

	dir := t.TempDir()
	authority, err := loadOrCreateMITMAuthority(filepath.Join(dir, "ca.crt"), filepath.Join(dir, "ca.key"))
	if err != nil {
		t.Fatalf("ca: %v", err)
	}
	prevCA := mitmCA
	mitmCA = authority
	defer func() { mitmCA = prevCA }()

	egressProxy := httptest.NewServer(http.HandlerFunc(handleEgressProxyRequest))
	defer egressProxy.Close()

	proxyAddr := strings.TrimPrefix(egressProxy.URL, "http://")
	conn, err := net.DialTimeout("tcp", proxyAddr, 5*time.Second)
	if err != nil {
		t.Fatalf("dial proxy: %v", err)
	}
	defer conn.Close()

	target := ln.Addr().String()
	_, _ = fmt.Fprintf(conn, "CONNECT %s HTTP/1.1\r\nHost: %s\r\n\r\n", target, target)
	br := bufio.NewReader(conn)
	resp, err := http.ReadResponse(br, &http.Request{Method: http.MethodConnect})
	if err != nil || resp.StatusCode != 200 {
		t.Fatalf("connect response: err=%v resp=%v", err, resp)
	}
	_, _ = conn.Write([]byte("PLAIN_TCP_PING\n"))
	got, err := br.ReadString('\n')
	if err != nil || got != "ECHO:PLAIN_TCP_PING\n" {
		t.Fatalf("expected raw TCP echo through CONNECT fallback, got %q (err=%v)", got, err)
	}
}

func TestControlProxyCACertEndpointAuth(t *testing.T) {
	dir := t.TempDir()
	authority, err := loadOrCreateMITMAuthority(filepath.Join(dir, "ca.crt"), filepath.Join(dir, "ca.key"))
	if err != nil {
		t.Fatalf("ca: %v", err)
	}
	prevCA := mitmCA
	mitmCA = authority
	defer func() { mitmCA = prevCA }()

	mux := newServerMux("tok-123", http.NotFoundHandler(), http.NotFoundHandler())
	srv := httptest.NewServer(mux)
	defer srv.Close()

	unauth, err := http.Get(srv.URL + "/ca.crt")
	if err != nil {
		t.Fatalf("unauth get: %v", err)
	}
	_ = unauth.Body.Close()
	if unauth.StatusCode != http.StatusUnauthorized {
		t.Fatalf("expected 401 without token, got %d", unauth.StatusCode)
	}

	req, _ := http.NewRequest(http.MethodGet, srv.URL+"/ca.crt", nil)
	req.Header.Set("X-Sandbox-Token", "tok-123")
	authed, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatalf("authed get: %v", err)
	}
	pemBytes, _ := io.ReadAll(authed.Body)
	_ = authed.Body.Close()
	if authed.StatusCode != http.StatusOK || !bytes.Equal(pemBytes, authority.caPEM) {
		t.Fatalf("expected 200 with CA PEM, got status=%d", authed.StatusCode)
	}
}
