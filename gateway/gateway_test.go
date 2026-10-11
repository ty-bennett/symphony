package main

import (
	"bufio"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

const localKey = "local-key"
const secret = "internal-secret"

type fakes struct {
	soloist, cloud, app *httptest.Server
	soloistHits         atomic.Int32
	cloudHits           atomic.Int32
	tokenHits           atomic.Int32
	cloudHeaders        atomic.Value
	cloudBody           atomic.Value
}

func sse(w http.ResponseWriter, model string, in, out int) {
	w.Header().Set("Content-Type", "text/event-stream")
	f := w.(http.Flusher)
	for _, line := range []string{
		`{"id":"c1","object":"chat.completion.chunk","model":"` + model + `","choices":[{"index":0,"delta":{"role":"assistant","content":""}}]}`,
		`{"id":"c1","object":"chat.completion.chunk","model":"` + model + `","choices":[{"index":0,"delta":{"content":"Hello"}}]}`,
		`{"id":"c1","object":"chat.completion.chunk","model":"` + model + `","choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}`,
		`{"id":"c1","object":"chat.completion.chunk","model":"` + model + `","choices":[],"usage":{"prompt_tokens":` + itoa(in) + `,"completion_tokens":` + itoa(out) + `,"total_tokens":` + itoa(in+out) + `}}`,
	} {
		io.WriteString(w, "data: "+line+"\n\n")
		f.Flush()
	}
	io.WriteString(w, "data: [DONE]\n\n")
}

func itoa(n int) string { b, _ := json.Marshal(n); return string(b) }

func setup(t *testing.T) (*fakes, *Gateway, *httptest.Server) {
	t.Helper()
	f := &fakes{}
	f.soloist = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		f.soloistHits.Add(1)
		sse(w, "qwen3-4b", 400, 20)
	}))
	f.cloud = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		f.cloudHits.Add(1)
		f.cloudHeaders.Store(r.Header.Clone())
		body, _ := io.ReadAll(r.Body)
		f.cloudBody.Store(string(body))
		if strings.HasPrefix(r.URL.Path, "/v1/admin/") {
			w.Header().Set("Content-Type", "application/json")
			io.WriteString(w, `{"users":[]}`)
			return
		}
		sse(w, "orchestra/moonshotai.kimi-k2.5", 1000, 100)
	}))
	f.app = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("X-Symphony-Internal") != secret {
			w.WriteHeader(401)
			return
		}
		f.tokenHits.Add(1)
		json.NewEncoder(w).Encode(map[string]any{"access_token": "token-for-" + r.URL.Query().Get("sub"), "expires_in": 3600})
	}))
	t.Cleanup(func() { f.soloist.Close(); f.cloud.Close(); f.app.Close() })
	g, err := New(Config{
		SoloistURL:   f.soloist.URL,
		SoloistModel: "qwen3-4b",
		CloudURL:     f.cloud.URL,
		CloudClient:  f.cloud.Client(),
		AppTokenURL:  f.app.URL + "/api/internal/token",
		LocalKey:     localKey,
		Secret:       secret,
		DataDir:      t.TempDir(),
		Prices:       DefaultPrices(),
		Now:          func() time.Time { return time.Date(2026, 10, 11, 12, 0, 0, 0, time.UTC) },
	})
	if err != nil {
		t.Fatal(err)
	}
	srv := httptest.NewServer(g.Handler())
	t.Cleanup(srv.Close)
	return f, g, srv
}

func chat(t *testing.T, srv *httptest.Server, session, parent string) (int, string) {
	t.Helper()
	req, _ := http.NewRequest("POST", srv.URL+"/v1/chat/completions", strings.NewReader(`{"model":"auto","stream":true,"messages":[{"role":"user","content":"hi"}]}`))
	req.Header.Set("Authorization", "Bearer "+localKey)
	if session != "" {
		req.Header.Set("x-opencode-session-id", session)
	}
	if parent != "" {
		req.Header.Set("x-opencode-parent-session-id", parent)
	}
	res, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	defer res.Body.Close()
	body, _ := io.ReadAll(res.Body)
	return res.StatusCode, string(body)
}

func register(t *testing.T, srv *httptest.Server, body string) int {
	t.Helper()
	req, _ := http.NewRequest("POST", srv.URL+"/internal/sessions", strings.NewReader(body))
	req.Header.Set("X-Symphony-Internal", secret)
	res, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	res.Body.Close()
	return res.StatusCode
}

const alice = `{"session":"ses_a","place":"private","user_sub":"sub-alice","model":"orchestra","tier":"private-cloud"}`

func TestUnknownSessionStaysOnSoloist(t *testing.T) {
	f, _, srv := setup(t)
	code, body := chat(t, srv, "ses_unknown", "")
	if code != 200 || f.soloistHits.Load() != 1 || f.cloudHits.Load() != 0 {
		t.Fatalf("want Soloist only: code=%d soloist=%d cloud=%d", code, f.soloistHits.Load(), f.cloudHits.Load())
	}
	if !strings.Contains(body, `"model":"soloist/qwen3-4b"`) || !strings.Contains(body, "data: [DONE]") {
		t.Fatalf("model not rewritten or stream incomplete: %s", body)
	}
}

func TestOrchestraSessionGoesToTheCloudAsItsUser(t *testing.T) {
	f, _, srv := setup(t)
	if code := register(t, srv, alice); code != 204 {
		t.Fatalf("register: %d", code)
	}
	code, body := chat(t, srv, "ses_a", "")
	if code != 200 || f.cloudHits.Load() != 1 || f.soloistHits.Load() != 0 {
		t.Fatalf("want cloud only: code=%d cloud=%d soloist=%d", code, f.cloudHits.Load(), f.soloistHits.Load())
	}
	h := f.cloudHeaders.Load().(http.Header)
	if h.Get("Authorization") != "Bearer token-for-sub-alice" || h.Get("X-Symphony-Data-Tier") != "private-cloud" || h.Get("X-Opencode-Session-Id") != "ses_a" {
		t.Fatalf("cloud headers: %v", h)
	}
	var sent map[string]any
	json.Unmarshal([]byte(f.cloudBody.Load().(string)), &sent)
	if sent["model"] != "orchestra" {
		t.Fatalf("model sent to cloud: %v", sent["model"])
	}
	if !strings.Contains(body, `"model":"orchestra/moonshotai.kimi-k2.5"`) {
		t.Fatalf("cloud model not passed through: %s", body)
	}
}

func TestChildSessionsInheritTheParentRoute(t *testing.T) {
	f, _, srv := setup(t)
	register(t, srv, strings.Replace(alice, "ses_a", "ses_parent", 1))
	chat(t, srv, "ses_child", "ses_parent")
	if f.cloudHits.Load() != 1 {
		t.Fatal("a subagent of an Orchestra job should run on Orchestra")
	}
}

func TestTokensAreCachedPerUser(t *testing.T) {
	f, _, srv := setup(t)
	register(t, srv, alice)
	chat(t, srv, "ses_a", "")
	chat(t, srv, "ses_a", "")
	if f.tokenHits.Load() != 1 {
		t.Fatalf("token fetched %d times", f.tokenHits.Load())
	}
}

func TestSpendIsCountedFromTheModelThatAnswered(t *testing.T) {
	_, g, srv := setup(t)
	register(t, srv, alice)
	chat(t, srv, "ses_a", "")
	chat(t, srv, "ses_local", "")
	// Kimi K2.5: 1,000 in at $0.60/M + 100 out at $3.00/M = $0.0009. Soloist is free.
	s := g.ledger.Summary(func(e Entry) bool { return e.Session == "ses_a" })
	if s.Requests != 1 || s.Input != 1000 || s.Output != 100 || !near(s.Cost, 0.0009) {
		t.Fatalf("orchestra summary: %+v", s)
	}
	month := g.ledger.Summary(func(e Entry) bool { return strings.HasPrefix(e.Time, "2026-10") })
	if month.Requests != 2 || !near(month.ByPlace["private"], 0.0009) || month.ByPlace["device"] != 0 {
		t.Fatalf("month summary: %+v", month)
	}
	raw, _ := os.ReadFile(filepath.Join(g.cfg.DataDir, "usage.jsonl"))
	if strings.Contains(string(raw), "Hello") {
		t.Fatal("the ledger must not hold answer text")
	}
}

func TestUsageEndpointReportsPerSessionAndMonth(t *testing.T) {
	_, _, srv := setup(t)
	register(t, srv, alice)
	chat(t, srv, "ses_a", "")
	for _, q := range []string{"session=ses_a", "month=2026-10"} {
		req, _ := http.NewRequest("GET", srv.URL+"/internal/usage?"+q, nil)
		req.Header.Set("X-Symphony-Internal", secret)
		res, _ := http.DefaultClient.Do(req)
		var s Summary
		json.NewDecoder(res.Body).Decode(&s)
		res.Body.Close()
		if s.Requests != 1 || !near(s.Cost, 0.0009) {
			t.Fatalf("usage %s: %+v", q, s)
		}
	}
}

func TestRefusesCallersWithoutTheRightKeys(t *testing.T) {
	_, _, srv := setup(t)
	req, _ := http.NewRequest("POST", srv.URL+"/v1/chat/completions", strings.NewReader(`{}`))
	req.Header.Set("Authorization", "Bearer wrong")
	res, _ := http.DefaultClient.Do(req)
	res.Body.Close()
	if res.StatusCode != 401 {
		t.Fatalf("chat with a bad key: %d", res.StatusCode)
	}
	req, _ = http.NewRequest("POST", srv.URL+"/internal/sessions", strings.NewReader(`{"session":"x","place":"private"}`))
	res, _ = http.DefaultClient.Do(req)
	res.Body.Close()
	if res.StatusCode != 401 {
		t.Fatalf("internal without the secret: %d", res.StatusCode)
	}
	if code := register(t, srv, `{"session":"x","place":"open"}`); code != 400 {
		t.Fatalf("unknown place: %d", code)
	}
}

func TestCloudDownIsAnErrorNeverASilentSwitchToSoloist(t *testing.T) {
	f, _, srv := setup(t)
	register(t, srv, alice)
	f.cloud.Close()
	code, body := chat(t, srv, "ses_a", "")
	if code != 502 || !strings.Contains(body, "UPSTREAM_UNAVAILABLE") || f.soloistHits.Load() != 0 {
		t.Fatalf("code=%d soloist=%d body=%s", code, f.soloistHits.Load(), body)
	}
}

func TestPausedSessionsAreRefused(t *testing.T) {
	f, _, srv := setup(t)
	register(t, srv, strings.Replace(alice, `"tier":"private-cloud"`, `"tier":"private-cloud","paused":true`, 1))
	code, body := chat(t, srv, "ses_a", "")
	if code != 402 || !strings.Contains(body, "SPENDING_LIMIT") || f.cloudHits.Load() != 0 {
		t.Fatalf("code=%d body=%s", code, body)
	}
}

func TestRoutesSurviveARestart(t *testing.T) {
	_, g, srv := setup(t)
	register(t, srv, alice)
	again, err := New(g.cfg)
	if err != nil {
		t.Fatal(err)
	}
	if r := again.routes.Lookup("ses_a", ""); r.Place != "private" || r.UserSub != "sub-alice" {
		t.Fatalf("route lost on restart: %+v", r)
	}
}

func TestCloudPassthroughForTheAppsTeamCalls(t *testing.T) {
	f, _, srv := setup(t)
	req, _ := http.NewRequest("GET", srv.URL+"/internal/cloud/v1/admin/users", nil)
	req.Header.Set("X-Symphony-Internal", secret)
	req.Header.Set("Authorization", "Bearer owner-token")
	res, _ := http.DefaultClient.Do(req)
	body, _ := io.ReadAll(res.Body)
	res.Body.Close()
	h := f.cloudHeaders.Load().(http.Header)
	if res.StatusCode != 200 || string(body) != `{"users":[]}` || h.Get("Authorization") != "Bearer owner-token" {
		t.Fatalf("passthrough: %d %s %v", res.StatusCode, body, h.Get("Authorization"))
	}
	req, _ = http.NewRequest("GET", srv.URL+"/internal/cloud/v1/chat/completions", nil)
	req.Header.Set("X-Symphony-Internal", secret)
	res, _ = http.DefaultClient.Do(req)
	res.Body.Close()
	if res.StatusCode != 404 {
		t.Fatalf("only admin routes pass through: %d", res.StatusCode)
	}
}

func TestStreamsArriveIncrementally(t *testing.T) {
	_, _, srv := setup(t)
	req, _ := http.NewRequest("POST", srv.URL+"/v1/chat/completions", strings.NewReader(`{"stream":true,"messages":[]}`))
	req.Header.Set("Authorization", "Bearer "+localKey)
	res, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	defer res.Body.Close()
	if ct := res.Header.Get("Content-Type"); ct != "text/event-stream" {
		t.Fatalf("content type %q", ct)
	}
	line, _ := bufio.NewReader(res.Body).ReadString('\n')
	if !strings.HasPrefix(line, "data: {") {
		t.Fatalf("first line %q", line)
	}
}

func near(a, b float64) bool { return a-b < 1e-9 && b-a < 1e-9 }
