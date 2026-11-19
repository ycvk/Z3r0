package main

import (
	"bufio"
	"crypto"
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/tls"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/pem"
	"fmt"
	"log"
	"math/big"
	"net"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"time"
)

const (
	defaultMITMCACertPath = "/etc/sandbox-proxy/ca.crt"
	defaultMITMCAKeyPath  = "/etc/sandbox-proxy/ca.key"
	mitmCACertEnv         = "SANDBOX_MITM_CA_CERT"
	mitmCAKeyEnv          = "SANDBOX_MITM_CA_KEY"
	maxLeafCertCache      = 1024
)

type mitmAuthority struct {
	caCert    *x509.Certificate
	caKey     crypto.Signer
	caPEM     []byte
	mu        sync.Mutex
	leafCache map[string]*tls.Certificate
}

var mitmCA *mitmAuthority

func resolveMITMCAPaths() (string, string) {
	certPath := strings.TrimSpace(os.Getenv(mitmCACertEnv))
	if certPath == "" {
		certPath = defaultMITMCACertPath
	}
	keyPath := strings.TrimSpace(os.Getenv(mitmCAKeyEnv))
	if keyPath == "" {
		keyPath = defaultMITMCAKeyPath
	}
	return certPath, keyPath
}

func loadOrCreateMITMAuthority(certPath string, keyPath string) (*mitmAuthority, error) {
	if certPEM, err := os.ReadFile(certPath); err == nil {
		if pair, err := tls.LoadX509KeyPair(certPath, keyPath); err == nil && len(pair.Certificate) > 0 {
			parsed, err := x509.ParseCertificate(pair.Certificate[0])
			if err == nil && parsed.IsCA && time.Now().Before(parsed.NotAfter) {
				signer, ok := pair.PrivateKey.(crypto.Signer)
				if ok {
					return &mitmAuthority{
						caCert:    parsed,
						caKey:     signer,
						caPEM:     certPEM,
						leafCache: make(map[string]*tls.Certificate),
					}, nil
				}
			}
		}
	}

	if err := os.MkdirAll(filepath.Dir(certPath), 0755); err != nil {
		return nil, fmt.Errorf("create mitm ca cert dir: %w", err)
	}
	if err := os.MkdirAll(filepath.Dir(keyPath), 0755); err != nil {
		return nil, fmt.Errorf("create mitm ca key dir: %w", err)
	}

	privKey, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		return nil, fmt.Errorf("generate mitm ca key: %w", err)
	}
	serial, err := randomSerialNumber()
	if err != nil {
		return nil, err
	}
	now := time.Now().UTC()
	tmpl := &x509.Certificate{
		SerialNumber: serial,
		Subject: pkix.Name{
			Organization: []string{"Z3r0 Sandbox"},
			CommonName:   "Z3r0 Sandbox MITM Root CA",
		},
		NotBefore:             now.Add(-1 * time.Hour),
		NotAfter:              now.AddDate(10, 0, 0),
		KeyUsage:              x509.KeyUsageCertSign | x509.KeyUsageCRLSign,
		BasicConstraintsValid: true,
		IsCA:                  true,
		MaxPathLen:            0,
		MaxPathLenZero:        true,
	}
	derBytes, err := x509.CreateCertificate(rand.Reader, tmpl, tmpl, &privKey.PublicKey, privKey)
	if err != nil {
		return nil, fmt.Errorf("sign mitm ca cert: %w", err)
	}
	parsedCA, err := x509.ParseCertificate(derBytes)
	if err != nil {
		return nil, fmt.Errorf("parse mitm ca cert: %w", err)
	}
	keyDER, err := x509.MarshalECPrivateKey(privKey)
	if err != nil {
		return nil, fmt.Errorf("marshal mitm ca key: %w", err)
	}

	certPEM := pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: derBytes})
	keyPEM := pem.EncodeToMemory(&pem.Block{Type: "EC PRIVATE KEY", Bytes: keyDER})

	if err := writeFileAtomic(keyPath, keyPEM, 0600); err != nil {
		return nil, fmt.Errorf("write mitm ca key: %w", err)
	}
	if err := writeFileAtomic(certPath, certPEM, 0644); err != nil {
		return nil, fmt.Errorf("write mitm ca cert: %w", err)
	}

	return &mitmAuthority{
		caCert:    parsedCA,
		caKey:     privKey,
		caPEM:     certPEM,
		leafCache: make(map[string]*tls.Certificate),
	}, nil
}

func writeFileAtomic(path string, data []byte, perm os.FileMode) error {
	tmp := fmt.Sprintf("%s.tmp.%d", path, os.Getpid())
	if err := os.WriteFile(tmp, data, perm); err != nil {
		return err
	}
	return os.Rename(tmp, path)
}

func randomSerialNumber() (*big.Int, error) {
	limit := new(big.Int).Lsh(big.NewInt(1), 128)
	serial, err := rand.Int(rand.Reader, limit)
	if err != nil {
		return nil, fmt.Errorf("generate cert serial: %w", err)
	}
	if serial.Sign() <= 0 {
		serial = big.NewInt(1)
	}
	return serial, nil
}

func normalizeCertHost(raw string) string {
	trimmed := strings.TrimSpace(raw)
	if host, _, err := net.SplitHostPort(trimmed); err == nil {
		trimmed = host
	}
	trimmed = strings.TrimPrefix(trimmed, "[")
	trimmed = strings.TrimSuffix(trimmed, "]")
	return strings.ToLower(trimmed)
}

func (a *mitmAuthority) certificateForHost(rawHost string) (*tls.Certificate, error) {
	host := normalizeCertHost(rawHost)
	if host == "" {
		return nil, fmt.Errorf("empty host for certificate")
	}

	now := time.Now().UTC()
	a.mu.Lock()
	if cached, ok := a.leafCache[host]; ok && cached.Leaf != nil && now.Add(time.Hour).Before(cached.Leaf.NotAfter) {
		a.mu.Unlock()
		return cached, nil
	}
	a.mu.Unlock()

	leafKey, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		return nil, err
	}
	serial, err := randomSerialNumber()
	if err != nil {
		return nil, err
	}
	tmpl := &x509.Certificate{
		SerialNumber: serial,
		Subject: pkix.Name{
			CommonName: host,
		},
		NotBefore:   now.Add(-1 * time.Hour),
		NotAfter:    now.Add(30 * 24 * time.Hour),
		KeyUsage:    x509.KeyUsageDigitalSignature | x509.KeyUsageKeyEncipherment,
		ExtKeyUsage: []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth},
	}
	if ip := net.ParseIP(host); ip != nil {
		tmpl.IPAddresses = []net.IP{ip}
	} else {
		tmpl.DNSNames = []string{host}
	}

	derBytes, err := x509.CreateCertificate(rand.Reader, tmpl, a.caCert, &leafKey.PublicKey, a.caKey)
	if err != nil {
		return nil, err
	}
	parsedLeaf, err := x509.ParseCertificate(derBytes)
	if err != nil {
		return nil, err
	}
	cert := &tls.Certificate{
		Certificate: [][]byte{derBytes, a.caCert.Raw},
		PrivateKey:  leafKey,
		Leaf:        parsedLeaf,
	}

	a.mu.Lock()
	if len(a.leafCache) >= maxLeafCertCache {
		a.leafCache = make(map[string]*tls.Certificate)
	}
	a.leafCache[host] = cert
	a.mu.Unlock()
	return cert, nil
}

func handleCACert(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	if mitmCA == nil || len(mitmCA.caPEM) == 0 {
		http.Error(w, "mitm ca unavailable", http.StatusServiceUnavailable)
		return
	}
	w.Header().Set("Content-Type", "application/x-pem-file")
	w.WriteHeader(http.StatusOK)
	_, _ = w.Write(mitmCA.caPEM)
}

type bufferedConn struct {
	net.Conn
	r *bufio.Reader
}

func (b *bufferedConn) Read(p []byte) (int, error) {
	return b.r.Read(p)
}

func serveConnectTunnel(clientRaw net.Conn, clientBuf *bufio.Reader, upstreamRaw net.Conn, connectAuthority string, authority *mitmAuthority) {
	if clientBuf == nil {
		clientBuf = bufio.NewReader(clientRaw)
	}
	clientPeek := &bufferedConn{Conn: clientRaw, r: clientBuf}

	if authority == nil {
		relay(clientPeek, upstreamRaw)
		return
	}

	_ = clientRaw.SetReadDeadline(time.Now().Add(15 * time.Second))
	head, err := clientBuf.Peek(1)
	_ = clientRaw.SetReadDeadline(time.Time{})
	if err != nil || len(head) == 0 || head[0] != 0x16 {
		if err == nil && len(head) > 0 {
			relay(clientPeek, upstreamRaw)
		}
		return
	}

	defaultHost, defaultPort, splitErr := splitHostPortDefault(connectAuthority, "443")
	if splitErr != nil {
		defaultHost = normalizeCertHost(connectAuthority)
		defaultPort = "443"
	}

	var negotiatedSNI string
	var sniMu sync.Mutex

	serverTLS := tls.Server(clientPeek, &tls.Config{
		NextProtos: []string{"http/1.1"},
		GetCertificate: func(hello *tls.ClientHelloInfo) (*tls.Certificate, error) {
			host := strings.TrimSpace(hello.ServerName)
			if host == "" {
				host = defaultHost
			}
			sniMu.Lock()
			negotiatedSNI = host
			sniMu.Unlock()
			return authority.certificateForHost(host)
		},
	})
	if err := serverTLS.Handshake(); err != nil {
		log.Printf("egress MITM client TLS handshake failed target=%s: %v", connectAuthority, err)
		return
	}
	defer serverTLS.Close()

	sniMu.Lock()
	targetHost := negotiatedSNI
	sniMu.Unlock()
	if targetHost == "" {
		targetHost = defaultHost
	}

	upstreamTLSConfig := &tls.Config{
		NextProtos:         []string{"http/1.1"},
		InsecureSkipVerify: true, // Authorized assessment targets frequently present self-signed or IP certificates.
	}
	if cleanHost := normalizeCertHost(targetHost); cleanHost != "" && net.ParseIP(cleanHost) == nil {
		upstreamTLSConfig.ServerName = cleanHost
	}
	upstreamTLS := tls.Client(upstreamRaw, upstreamTLSConfig)
	if err := upstreamTLS.Handshake(); err != nil {
		log.Printf("egress MITM upstream TLS handshake failed target=%s: %v", connectAuthority, err)
		return
	}
	defer upstreamTLS.Close()

	serveMITMHTTPStream(serverTLS, upstreamTLS, defaultHost, defaultPort)
}

func formatHTTPSAuthority(reqHost string, fallbackHost string, fallbackPort string) string {
	host := strings.TrimSpace(reqHost)
	if host == "" {
		if fallbackPort != "" && fallbackPort != "443" {
			return net.JoinHostPort(fallbackHost, fallbackPort)
		}
		return fallbackHost
	}
	if h, p, err := net.SplitHostPort(host); err == nil && p == "443" {
		if strings.Contains(h, ":") {
			return "[" + h + "]"
		}
		return h
	}
	return host
}

func serveMITMHTTPStream(clientTLS net.Conn, upstreamTLS net.Conn, fallbackHost string, fallbackPort string) {
	clientReader := bufio.NewReader(clientTLS)
	upstreamReader := bufio.NewReader(upstreamTLS)

	for {
		req, err := http.ReadRequest(clientReader)
		if err != nil {
			return
		}
		startedAt := time.Now()
		authority := formatHTTPSAuthority(req.Host, fallbackHost, fallbackPort)
		path := req.URL.Path
		if path == "" {
			path = "/"
		}
		if req.URL.RawQuery != "" {
			path += "?" + req.URL.RawQuery
		}
		fullURL := "https://" + authority + path
		narrowAcceptEncoding(req.Header)
		origReqHeaders := flattenHeader(req.Header)

		if isUpgradeRequest(req.Header) {
			if err := req.Write(upstreamTLS); err != nil {
				return
			}
			resp, err := http.ReadResponse(upstreamReader, req)
			if err != nil {
				return
			}
			statusCode := resp.StatusCode
			respHeaders := flattenHeader(resp.Header)
			_ = resp.Write(clientTLS)
			_ = resp.Body.Close()
			sandboxTraffic.Record(proxyTrafficEntry{
				Method:          req.Method,
				URL:             fullURL,
				Host:            authority,
				Path:            path,
				StatusCode:      statusCode,
				RequestHeaders:  origReqHeaders,
				ResponseHeaders: respHeaders,
				DurationMs:      time.Since(startedAt).Milliseconds(),
			})
			relay(&bufferedConn{Conn: clientTLS, r: clientReader}, &bufferedConn{Conn: upstreamTLS, r: upstreamReader})
			return
		}

		reqClose := req.Close
		req.RequestURI = ""
		req.URL.Scheme = ""
		req.URL.Host = ""
		req.Header.Del("Proxy-Authorization")
		req.Header.Del("Proxy-Connection")

		var reqTap *tapReadCloser
		reqContentEncoding := req.Header.Get("Content-Encoding")
		if req.Body != nil && !isBinaryContentType(req.Header.Get("Content-Type")) {
			reqTap = &tapReadCloser{rc: req.Body}
			req.Body = reqTap
		}

		if err := req.Write(upstreamTLS); err != nil {
			if req.Body != nil {
				_ = req.Body.Close()
			}
			return
		}
		reqBody := ""
		if reqTap != nil {
			reqBody = decodeCapturedBody(reqTap.buf.Bytes(), reqContentEncoding)
		}

		resp, err := http.ReadResponse(upstreamReader, req)
		if err != nil {
			return
		}
		respClose := resp.Close
		cleanRespHeaders := cloneHeaderWithoutHopByHop(resp.Header)
		respContentEncoding := resp.Header.Get("Content-Encoding")
		captureRespText := !isBinaryContentType(resp.Header.Get("Content-Type"))

		counter := &countingTapReadCloser{
			rc:      resp.Body,
			capture: captureRespText,
		}
		resp.Body = counter
		writeErr := resp.Write(clientTLS)
		_ = counter.Close()

		respBody := ""
		if captureRespText {
			respBody = decodeCapturedBody(counter.buf.Bytes(), respContentEncoding)
		}
		sandboxTraffic.Record(proxyTrafficEntry{
			Method:          req.Method,
			URL:             fullURL,
			Host:            authority,
			Path:            path,
			StatusCode:      resp.StatusCode,
			RequestHeaders:  origReqHeaders,
			RequestBody:     reqBody,
			ResponseHeaders: flattenHeader(cleanRespHeaders),
			ResponseBody:    respBody,
			ResponseBytes:   counter.totalBytes,
			DurationMs:      time.Since(startedAt).Milliseconds(),
		})

		if writeErr != nil || reqClose || respClose {
			return
		}
	}
}

func isUpgradeRequest(h http.Header) bool {
	return strings.TrimSpace(h.Get("Upgrade")) != "" &&
		strings.Contains(strings.ToLower(h.Get("Connection")), "upgrade")
}
