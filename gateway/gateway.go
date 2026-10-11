// symphony-gateway: the one endpoint OpenCode on the Jetson sends every prompt to.
// Each OpenCode session (a Symphony job) has a route the app decided when the job
// started: Soloist (the local model) or Orchestra (the private cloud). The gateway
// enforces that route on every request, streams the answer back, and records the
// tokens and cost of the model that answered. Standard library only.
package main

import (
	"bufio"
	"bytes"
	"context"
	"crypto/subtle"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"time"
)

type Config struct {
	Listen       string
	SoloistURL   string // OpenAI-compatible local server (llama.cpp)
	SoloistModel string
	CloudURL     string // https://api.<tenant>.symphony-edge.link
	CloudClient  *http.Client
	AppTokenURL  string // the app's /api/internal/token
	LocalKey     string // OpenCode's provider apiKey
	Secret       string // shared with the app for /internal/*
	DataDir      string
	Prices       map[string]Price
	Now          func() time.Time
}

// Route is where one OpenCode session's prompts go.
type Route struct {
	Session string `json:"session"`
	Place   string `json:"place"` // "device" (Soloist) or "private" (Orchestra)
	UserSub string `json:"user_sub,omitempty"`
	Model   string `json:"model,omitempty"` // "orchestra" or "orchestra-step-up"
	Tier    string `json:"tier,omitempty"`  // "private-cloud" or "frontier-eligible"
	Paused  bool   `json:"paused,omitempty"`
}

type Routes struct {
	mu   sync.RWMutex
	path string
	m    map[string]Route
}

func loadRoutes(path string) (*Routes, error) {
	r := &Routes{path: path, m: map[string]Route{}}
	raw, err := os.ReadFile(path)
	if err == nil {
		if err := json.Unmarshal(raw, &r.m); err != nil {
			return nil, fmt.Errorf("routes file %s: %w", path, err)
		}
	} else if !errors.Is(err, os.ErrNotExist) {
		return nil, err
	}
	return r, nil
}

func (r *Routes) Set(route Route) error {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.m[route.Session] = route
	raw, _ := json.Marshal(r.m)
	tmp := r.path + ".tmp"
	if err := os.WriteFile(tmp, raw, 0o600); err != nil {
		return err
	}
	return os.Rename(tmp, r.path)
}

// Lookup returns the session's route, else its parent's (subagents), else Soloist.
func (r *Routes) Lookup(session, parent string) Route {
	r.mu.RLock()
	defer r.mu.RUnlock()
	if route, ok := r.m[session]; ok {
		return route
	}
	if route, ok := r.m[parent]; ok && parent != "" {
		route.Session = session
		return route
	}
	return Route{Session: session, Place: "device"}
}

type token struct {
	value   string
	expires time.Time
}

type Gateway struct {
	cfg    Config
	routes *Routes
	ledger *Ledger
	local  *http.Client
	mu     sync.Mutex
	tokens map[string]token
}

func New(cfg Config) (*Gateway, error) {
	if cfg.Now == nil {
		cfg.Now = time.Now
	}
	if cfg.Prices == nil {
		cfg.Prices = DefaultPrices()
	}
	if err := os.MkdirAll(cfg.DataDir, 0o700); err != nil {
		return nil, err
	}
	routes, err := loadRoutes(filepath.Join(cfg.DataDir, "routes.json"))
	if err != nil {
		return nil, err
	}
	dial := &net.Dialer{Timeout: 5 * time.Second}
	local := &http.Client{Transport: &http.Transport{DialContext: dial.DialContext}}
	if cfg.CloudClient == nil {
		cfg.CloudClient = &http.Client{}
	}
	return &Gateway{
		cfg:    cfg,
		routes: routes,
		ledger: &Ledger{path: filepath.Join(cfg.DataDir, "usage.jsonl"), prices: cfg.Prices},
		local:  local,
		tokens: map[string]token{},
	}, nil
}

func (g *Gateway) Handler() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("POST /v1/chat/completions", g.requireKey(g.chat))
	mux.HandleFunc("GET /v1/models", g.requireKey(func(w http.ResponseWriter, r *http.Request) {
		writeJSON(w, 200, map[string]any{"object": "list", "data": []any{map[string]any{"id": "auto", "object": "model", "owned_by": "symphony"}}})
	}))
	mux.HandleFunc("POST /internal/sessions", g.requireSecret(g.register))
	mux.HandleFunc("GET /internal/usage", g.requireSecret(g.usage))
	mux.HandleFunc("/internal/cloud/", g.requireSecret(g.cloudPassthrough))
	mux.HandleFunc("GET /healthz", func(w http.ResponseWriter, r *http.Request) { io.WriteString(w, "ok") })
	return mux
}

func equal(a, b string) bool { return subtle.ConstantTimeCompare([]byte(a), []byte(b)) == 1 }

func (g *Gateway) requireKey(next http.HandlerFunc) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		if !equal(strings.TrimPrefix(r.Header.Get("Authorization"), "Bearer "), g.cfg.LocalKey) {
			apiError(w, 401, "UNAUTHORIZED", "Unknown API key")
			return
		}
		next(w, r)
	}
}

func (g *Gateway) requireSecret(next http.HandlerFunc) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		if g.cfg.Secret == "" || !equal(r.Header.Get("X-Symphony-Internal"), g.cfg.Secret) {
			apiError(w, 401, "UNAUTHORIZED", "Internal callers only")
			return
		}
		next(w, r)
	}
}

func (g *Gateway) register(w http.ResponseWriter, r *http.Request) {
	var route Route
	if err := json.NewDecoder(io.LimitReader(r.Body, 1<<16)).Decode(&route); err != nil || route.Session == "" {
		apiError(w, 400, "INVALID_REQUEST", "session and place are required")
		return
	}
	switch route.Place {
	case "device":
	case "private":
		if route.UserSub == "" {
			apiError(w, 400, "INVALID_REQUEST", "Orchestra sessions need user_sub")
			return
		}
		if route.Model == "" {
			route.Model = "orchestra"
		}
		if route.Tier == "" {
			route.Tier = "private-cloud"
		}
	default:
		apiError(w, 400, "INVALID_REQUEST", "place must be device or private")
		return
	}
	if err := g.routes.Set(route); err != nil {
		apiError(w, 500, "INTERNAL", "Could not save the route")
		return
	}
	w.WriteHeader(204)
}

func (g *Gateway) usage(w http.ResponseWriter, r *http.Request) {
	session, month := r.URL.Query().Get("session"), r.URL.Query().Get("month")
	writeJSON(w, 200, g.ledger.Summary(func(e Entry) bool {
		return (session == "" || e.Session == session) && (month == "" || strings.HasPrefix(e.Time, month))
	}))
}

// The app's Team page reaches the cloud's admin API through the gateway, which
// holds the device certificate. Only /v1/admin/ is forwarded.
func (g *Gateway) cloudPassthrough(w http.ResponseWriter, r *http.Request) {
	path := strings.TrimPrefix(r.URL.Path, "/internal/cloud")
	if !strings.HasPrefix(path, "/v1/admin/") {
		apiError(w, 404, "NOT_FOUND", "Only admin routes pass through")
		return
	}
	req, _ := http.NewRequestWithContext(r.Context(), r.Method, g.cfg.CloudURL+path, io.LimitReader(r.Body, 1<<16))
	req.Header.Set("Authorization", r.Header.Get("Authorization"))
	req.Header.Set("Content-Type", "application/json")
	res, err := g.cfg.CloudClient.Do(req)
	if err != nil {
		apiError(w, 502, "UPSTREAM_UNAVAILABLE", "Orchestra could not be reached")
		return
	}
	defer res.Body.Close()
	w.Header().Set("Content-Type", res.Header.Get("Content-Type"))
	w.WriteHeader(res.StatusCode)
	io.Copy(w, res.Body)
}

func (g *Gateway) userToken(ctx context.Context, sub string) (string, error) {
	g.mu.Lock()
	t, ok := g.tokens[sub]
	g.mu.Unlock()
	if ok && g.cfg.Now().Before(t.expires) {
		return t.value, nil
	}
	req, _ := http.NewRequestWithContext(ctx, "GET", g.cfg.AppTokenURL+"?sub="+sub, nil)
	req.Header.Set("X-Symphony-Internal", g.cfg.Secret)
	res, err := g.local.Do(req)
	if err != nil {
		return "", err
	}
	defer res.Body.Close()
	if res.StatusCode != 200 {
		return "", fmt.Errorf("token for user: status %d", res.StatusCode)
	}
	var body struct {
		AccessToken string `json:"access_token"`
		ExpiresIn   int    `json:"expires_in"`
	}
	if err := json.NewDecoder(res.Body).Decode(&body); err != nil || body.AccessToken == "" {
		return "", errors.New("token for user: empty")
	}
	g.mu.Lock()
	g.tokens[sub] = token{value: body.AccessToken, expires: g.cfg.Now().Add(time.Duration(body.ExpiresIn-60) * time.Second)}
	g.mu.Unlock()
	return body.AccessToken, nil
}

func (g *Gateway) chat(w http.ResponseWriter, r *http.Request) {
	raw, err := io.ReadAll(io.LimitReader(r.Body, 32<<20))
	if err != nil {
		apiError(w, 400, "INVALID_REQUEST", "Could not read the request")
		return
	}
	var body map[string]any
	if err := json.Unmarshal(raw, &body); err != nil {
		apiError(w, 400, "INVALID_REQUEST", "Body must be JSON")
		return
	}
	route := g.routes.Lookup(r.Header.Get("X-Opencode-Session-Id"), r.Header.Get("X-Opencode-Parent-Session-Id"))
	if route.Paused {
		apiError(w, 402, "SPENDING_LIMIT", "This month's spending limit is reached; Orchestra is paused")
		return
	}
	stream, _ := body["stream"].(bool)
	var req *http.Request
	var client *http.Client
	if route.Place == "private" {
		tok, err := g.userToken(r.Context(), route.UserSub)
		if err != nil {
			apiError(w, 401, "USER_UNAUTHORIZED", "Sign in to Symphony again")
			return
		}
		body["model"] = route.Model
		out, _ := json.Marshal(body)
		req, _ = http.NewRequestWithContext(r.Context(), "POST", g.cfg.CloudURL+"/v1/chat/completions", bytes.NewReader(out))
		req.Header.Set("Authorization", "Bearer "+tok)
		req.Header.Set("X-Symphony-Data-Tier", route.Tier)
		req.Header.Set("X-Opencode-Session-Id", route.Session)
		client = g.cfg.CloudClient
	} else {
		body["model"] = g.cfg.SoloistModel
		if stream {
			body["stream_options"] = map[string]any{"include_usage": true}
		}
		out, _ := json.Marshal(body)
		req, _ = http.NewRequestWithContext(r.Context(), "POST", g.cfg.SoloistURL+"/v1/chat/completions", bytes.NewReader(out))
		client = g.local
	}
	req.Header.Set("Content-Type", "application/json")
	res, err := client.Do(req)
	if err != nil {
		apiError(w, 502, "UPSTREAM_UNAVAILABLE", placeName(route.Place)+" could not be reached")
		return
	}
	defer res.Body.Close()

	entry := Entry{Session: route.Session, User: route.UserSub, Place: route.Place}
	rewrite := func(chunk map[string]any) {
		if m, ok := chunk["model"].(string); ok {
			if route.Place == "device" {
				m = "soloist/" + m
				chunk["model"] = m
			}
			entry.Model = m
		}
		if u, ok := chunk["usage"].(map[string]any); ok {
			entry.Input += num(u["prompt_tokens"])
			entry.Output += num(u["completion_tokens"])
			if d, ok := u["prompt_tokens_details"].(map[string]any); ok {
				entry.CacheRead += num(d["cached_tokens"])
			}
		}
	}

	if !strings.HasPrefix(res.Header.Get("Content-Type"), "text/event-stream") {
		data, _ := io.ReadAll(io.LimitReader(res.Body, 32<<20))
		var chunk map[string]any
		if res.StatusCode == 200 && json.Unmarshal(data, &chunk) == nil {
			rewrite(chunk)
			data, _ = json.Marshal(chunk)
			g.record(entry)
		}
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(res.StatusCode)
		w.Write(data)
		return
	}

	w.Header().Set("Content-Type", "text/event-stream")
	w.Header().Set("Cache-Control", "no-cache")
	w.WriteHeader(res.StatusCode)
	flusher, _ := w.(http.Flusher)
	reader := bufio.NewReaderSize(res.Body, 64<<10)
	for {
		line, err := reader.ReadBytes('\n')
		if len(line) > 0 {
			if bytes.HasPrefix(line, []byte("data: {")) {
				var chunk map[string]any
				if json.Unmarshal(bytes.TrimSpace(line[6:]), &chunk) == nil {
					rewrite(chunk)
					out, _ := json.Marshal(chunk)
					line = append(append([]byte("data: "), out...), '\n')
				}
			}
			w.Write(line)
			if flusher != nil && bytes.Equal(bytes.TrimSpace(line), nil) {
				flusher.Flush()
			}
		}
		if err != nil {
			break
		}
	}
	if flusher != nil {
		flusher.Flush()
	}
	g.record(entry)
}

func (g *Gateway) record(e Entry) {
	e.Time = g.cfg.Now().UTC().Format(time.RFC3339)
	if err := g.ledger.Append(e); err != nil {
		fmt.Fprintln(os.Stderr, "ledger:", err)
	}
}

func placeName(place string) string {
	if place == "private" {
		return "Orchestra"
	}
	return "Soloist"
}

func num(v any) int {
	f, _ := v.(float64)
	return int(f)
}

func writeJSON(w http.ResponseWriter, status int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	json.NewEncoder(w).Encode(v)
}

func apiError(w http.ResponseWriter, status int, code, message string) {
	writeJSON(w, status, map[string]any{"error": map[string]any{"message": message, "type": code, "code": code}})
}
