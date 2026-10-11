package main

import (
	"crypto/rand"
	"crypto/tls"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"net"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"time"
)

func env(name, fallback string) string {
	if v := strings.TrimSpace(os.Getenv(name)); v != "" {
		return v
	}
	return fallback
}

// secretFile returns the file's contents, creating a random secret (mode 0600) the first time.
func secretFile(path string) (string, error) {
	if raw, err := os.ReadFile(path); err == nil {
		return strings.TrimSpace(string(raw)), nil
	} else if !errors.Is(err, os.ErrNotExist) {
		return "", err
	}
	buf := make([]byte, 32)
	if _, err := rand.Read(buf); err != nil {
		return "", err
	}
	value := hex.EncodeToString(buf)
	if err := os.WriteFile(path, []byte(value+"\n"), 0o600); err != nil {
		return "", err
	}
	return value, nil
}

func run() error {
	home, _ := os.UserHomeDir()
	dataDir := env("SYMPHONY_GATEWAY_DIR", filepath.Join(home, ".symphony", "gateway"))
	if err := os.MkdirAll(dataDir, 0o700); err != nil {
		return err
	}
	localKey, err := secretFile(env("SYMPHONY_LOCAL_KEY_FILE", filepath.Join(dataDir, "local.key")))
	if err != nil {
		return err
	}
	secret, err := secretFile(env("SYMPHONY_INTERNAL_SECRET_FILE", filepath.Join(dataDir, "internal.secret")))
	if err != nil {
		return err
	}
	prices := DefaultPrices()
	if path := os.Getenv("SYMPHONY_PRICES_FILE"); path != "" {
		raw, err := os.ReadFile(path)
		if err != nil {
			return err
		}
		if err := json.Unmarshal(raw, &prices); err != nil {
			return fmt.Errorf("prices file: %w", err)
		}
	}

	cloudClient := &http.Client{}
	cert, key := os.Getenv("SYMPHONY_DEVICE_CERT"), os.Getenv("SYMPHONY_DEVICE_KEY")
	if cert != "" && key != "" {
		pair, err := tls.LoadX509KeyPair(cert, key)
		if err != nil {
			return fmt.Errorf("device certificate: %w", err)
		}
		cloudClient.Transport = &http.Transport{
			TLSClientConfig:       &tls.Config{Certificates: []tls.Certificate{pair}, MinVersion: tls.VersionTLS12},
			DialContext:           (&net.Dialer{Timeout: 5 * time.Second}).DialContext,
			TLSHandshakeTimeout:   5 * time.Second,
			ResponseHeaderTimeout: 300 * time.Second,
			MaxIdleConns:          8,
		}
	} else {
		fmt.Fprintln(os.Stderr, "warning: SYMPHONY_DEVICE_CERT/KEY not set; Orchestra calls will be refused by the cloud")
	}

	g, err := New(Config{
		Listen:       env("SYMPHONY_GATEWAY_LISTEN", "127.0.0.1:8088"),
		SoloistURL:   env("SOLOIST_URL", "http://127.0.0.1:8080"),
		SoloistModel: env("SOLOIST_MODEL", "qwen3-4b"),
		CloudURL:     env("SYMPHONY_CLOUD_URL", "https://api.demo.symphony-edge.link"),
		CloudClient:  cloudClient,
		AppTokenURL:  env("SYMPHONY_APP_TOKEN_URL", "http://127.0.0.1:3000/api/internal/token"),
		LocalKey:     localKey,
		Secret:       secret,
		DataDir:      dataDir,
		Prices:       prices,
	})
	if err != nil {
		return err
	}
	server := &http.Server{Addr: g.cfg.Listen, Handler: g.Handler(), ReadHeaderTimeout: 10 * time.Second}
	fmt.Printf("symphony-gateway on http://%s (Soloist %s, Orchestra %s)\n", g.cfg.Listen, g.cfg.SoloistURL, g.cfg.CloudURL)
	return server.ListenAndServe()
}

func main() {
	if err := run(); err != nil {
		fmt.Fprintln(os.Stderr, "symphony-gateway:", err)
		os.Exit(1)
	}
}
