package main

import (
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync/atomic"
	"testing"
)

// fakeRouter answers like the edge router's POST /v1/chat stream: LOCAL tokens, or a handoff.
func fakeRouter(t *testing.T, local bool, hits *atomic.Int32, sent *atomic.Value) *httptest.Server {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		hits.Add(1)
		body, _ := io.ReadAll(r.Body)
		sent.Store(string(body))
		w.Header().Set("Content-Type", "text/event-stream")
		route := "PRIVATE_AWS"
		if local {
			route = "LOCAL"
		}
		decision := `{"route":"` + route + `","prompt_tokens":42}`
		io.WriteString(w, "event: decision\ndata: "+decision+"\n\n")
		if local {
			io.WriteString(w, "event: token\ndata: {\"text\":\"Hi \"}\n\n")
			io.WriteString(w, "event: token\ndata: {\"text\":\"there\"}\n\n")
			io.WriteString(w, `event: complete`+"\ndata: "+`{"status":"COMPLETED","route":"LOCAL","generated_tokens":2,"finish_reason":"eos","decision":`+decision+"}\n\n")
			return
		}
		io.WriteString(w, `event: complete`+"\ndata: "+`{"status":"HANDOFF_REQUIRED","route":"PRIVATE_AWS","decision":`+decision+"}\n\n")
	}))
	t.Cleanup(srv.Close)
	return srv
}

func routed(t *testing.T, local bool, user string) (*fakes, *Gateway, *httptest.Server, *atomic.Int32, *atomic.Value) {
	f, g, _ := setup(t)
	hits, sent := &atomic.Int32{}, &atomic.Value{}
	g.cfg.RouterURL = fakeRouter(t, local, hits, sent).URL
	g.cfg.DefaultUser = user
	srv := httptest.NewServer(g.Handler())
	t.Cleanup(srv.Close)
	return f, g, srv, hits, sent
}

func TestSoloistAnswersLocalPromptsAsOpenAIChunks(t *testing.T) {
	f, g, srv, hits, sent := routed(t, true, "")
	code, body := chat(t, srv, "ses_x", "")
	if code != 200 || hits.Load() != 1 || f.cloudHits.Load() != 0 || f.soloistHits.Load() != 0 {
		t.Fatalf("code=%d router=%d cloud=%d", code, hits.Load(), f.cloudHits.Load())
	}
	for _, want := range []string{`"role":"assistant"`, `"content":"Hi "`, `"content":"there"`, `"finish_reason":"stop"`, `"prompt_tokens":42`, `"model":"soloist/qwen3-4b"`, "data: [DONE]"} {
		if !strings.Contains(body, want) {
			t.Fatalf("missing %s in %s", want, body)
		}
	}
	var req map[string]any
	json.Unmarshal([]byte(sent.Load().(string)), &req)
	msgs := req["messages"].([]any)
	if req["stream"] != true || msgs[0].(map[string]any)["role"] != "system" || msgs[len(msgs)-1].(map[string]any)["content"] != "hi" {
		t.Fatalf("router request: %v", req)
	}
	if s := g.ledger.Summary(func(e Entry) bool { return e.Session == "ses_x" }); s.Requests != 1 || s.Input != 42 || s.Output != 2 || s.Cost != 0 {
		t.Fatalf("ledger: %+v", s)
	}
}

func TestSoloistHandoffGoesToOrchestraAndSticksForThePrompt(t *testing.T) {
	f, _, srv, hits, _ := routed(t, false, "sub-owner")
	code, body := chat(t, srv, "ses_y", "")
	if code != 200 || f.cloudHits.Load() != 1 || !strings.Contains(body, "kimi-k2.5") {
		t.Fatalf("code=%d cloud=%d body=%s", code, f.cloudHits.Load(), body)
	}
	if h := f.cloudHeaders.Load().(http.Header); h.Get("Authorization") != "Bearer token-for-sub-owner" || h.Get("X-Symphony-Data-Tier") != "private-cloud" {
		t.Fatalf("cloud headers: %v", h)
	}
	chat(t, srv, "ses_y", "") // the tool-call turn of the same prompt
	if hits.Load() != 1 || f.cloudHits.Load() != 2 {
		t.Fatalf("a handed-off prompt should not be re-decided: router=%d cloud=%d", hits.Load(), f.cloudHits.Load())
	}
}

func TestHandoffWithoutAUserIsRefused(t *testing.T) {
	f, _, srv, _, _ := routed(t, false, "")
	code, body := chat(t, srv, "ses_z", "")
	if code != 401 || f.cloudHits.Load() != 0 || !strings.Contains(body, "USER_UNAUTHORIZED") {
		t.Fatalf("code=%d body=%s", code, body)
	}
}

func TestJobsPinnedToTheBoxNeverHandOff(t *testing.T) {
	f, _, srv, _, _ := routed(t, false, "sub-owner")
	register(t, srv, `{"session":"ses_p","place":"device"}`)
	code, body := chat(t, srv, "ses_p", "")
	if code != 403 || f.cloudHits.Load() != 0 || !strings.Contains(body, "DATA_TIER_DENIED") {
		t.Fatalf("code=%d body=%s", code, body)
	}
}

func TestSoloistDownIsAnErrorNeverTheCloud(t *testing.T) {
	f, g, srv, _, _ := routed(t, true, "sub-owner")
	g.cfg.RouterURL = "http://127.0.0.1:1"
	code, body := chat(t, srv, "ses_d", "")
	if code != 502 || f.cloudHits.Load() != 0 || !strings.Contains(body, "Soloist could not be reached") {
		t.Fatalf("code=%d body=%s", code, body)
	}
}

func TestRouterMessagesKeepRecentTurnsOnly(t *testing.T) {
	var raw any
	json.Unmarshal([]byte(`[
		{"role":"system","content":"`+strings.Repeat("s", 9000)+`"},
		{"role":"user","content":[{"type":"text","text":"`+strings.Repeat("a", 5000)+`"}]},
		{"role":"assistant","content":"","tool_calls":[{"id":"1"}]},
		{"role":"tool","content":"file contents"},
		{"role":"user","content":"and now?"}]`), &raw)
	msgs, prompt := routerMessages(raw)
	total := 0
	for _, m := range msgs[1:] {
		total += len(m.Content)
	}
	if prompt != "and now?" || msgs[0].Content != soloistSystem || total > soloistPromptChars || msgs[len(msgs)-2].Content != "Tool result:\nfile contents" {
		t.Fatalf("prompt=%q msgs=%d total=%d", prompt, len(msgs), total)
	}
}

func TestHandoffsUseTheRefreshTokenKeptOnTheBox(t *testing.T) {
	f, g, srv, _, _ := routed(t, false, "sub-owner")
	var cognitoHits atomic.Int32
	cognito := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		cognitoHits.Add(1)
		var in map[string]any
		json.NewDecoder(r.Body).Decode(&in)
		params := in["AuthParameters"].(map[string]any)
		if r.Header.Get("X-Amz-Target") != "AWSCognitoIdentityProviderService.InitiateAuth" || in["AuthFlow"] != "REFRESH_TOKEN_AUTH" || in["ClientId"] != "jetson-client" || params["REFRESH_TOKEN"] != "refresh-owner" {
			w.WriteHeader(400)
			io.WriteString(w, `{"__type":"NotAuthorizedException"}`)
			return
		}
		io.WriteString(w, `{"AuthenticationResult":{"AccessToken":"access-from-cognito","ExpiresIn":3600}}`)
	}))
	t.Cleanup(cognito.Close)
	g.cfg.CognitoClientID, g.cfg.CognitoURL = "jetson-client", cognito.URL
	os.MkdirAll(filepath.Join(g.cfg.DataDir, "users"), 0o700)
	os.WriteFile(filepath.Join(g.cfg.DataDir, "users", "sub-owner.refresh"), []byte("refresh-owner\n"), 0o600)

	chat(t, srv, "ses_r", "")
	chat(t, srv, "ses_r", "")
	if h := f.cloudHeaders.Load().(http.Header); h.Get("Authorization") != "Bearer access-from-cognito" {
		t.Fatalf("cloud auth: %q", h.Get("Authorization"))
	}
	if cognitoHits.Load() != 1 || f.tokenHits.Load() != 0 {
		t.Fatalf("cognito=%d app=%d: want one refresh, cached, and no app call", cognitoHits.Load(), f.tokenHits.Load())
	}
}
