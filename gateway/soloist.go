// Soloist routes first: when SOLOIST_ROUTER_URL points at the edge router
// (everything/, POST /v1/chat), every prompt goes to Soloist, which decides with
// its difficulty head. LOCAL answers stream back from the box, translated to
// OpenAI chunks; a handoff goes to Orchestra. A handoff is remembered for the
// prompt, so the tool calls that follow it stay on Orchestra.
package main

import (
	"bufio"
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"net/http"
	"strings"
	"sync"
)

// The edge router's context is 2,048 tokens, so Soloist sees the recent turns only.
const (
	soloistPromptChars   = 4000
	soloistDefaultTokens = 512
	soloistMaxTokens     = 1024
	soloistSystem        = "You are Soloist, Symphony's assistant on the office's own box. Answer clearly and briefly."
)

type routerMessage struct {
	Role    string `json:"role"`
	Content string `json:"content"`
}

// handoffs remembers prompts Soloist sent to Orchestra (key: session + prompt hash).
type handoffs struct {
	mu sync.Mutex
	m  map[string]bool
}

func (h *handoffs) has(key string) bool {
	h.mu.Lock()
	defer h.mu.Unlock()
	return h.m[key]
}

func (h *handoffs) add(key string) {
	h.mu.Lock()
	defer h.mu.Unlock()
	if h.m == nil || len(h.m) > 4096 {
		h.m = map[string]bool{}
	}
	h.m[key] = true
}

func text(content any) string {
	switch c := content.(type) {
	case string:
		return c
	case []any:
		var parts []string
		for _, p := range c {
			if m, ok := p.(map[string]any); ok && m["type"] == "text" {
				if s, ok := m["text"].(string); ok {
					parts = append(parts, s)
				}
			}
		}
		return strings.Join(parts, "\n")
	}
	return ""
}

// routerMessages turns OpenAI messages into the edge router's: no OpenCode system
// prompt or tool calls, tool results as user turns, newest turns within the budget.
func routerMessages(raw any) (msgs []routerMessage, prompt string) {
	list, _ := raw.([]any)
	var turns []routerMessage
	for _, item := range list {
		m, _ := item.(map[string]any)
		role, _ := m["role"].(string)
		body := strings.TrimSpace(text(m["content"]))
		switch {
		case body == "":
			continue
		case role == "user":
			prompt = body
		case role == "tool":
			role, body = "user", "Tool result:\n"+body
		case role != "assistant":
			continue
		}
		turns = append(turns, routerMessage{Role: role, Content: body})
	}
	budget := soloistPromptChars
	for i := len(turns) - 1; i >= 0 && budget > 0; i-- {
		t := turns[i]
		if len(t.Content) > budget {
			t.Content = t.Content[len(t.Content)-budget:]
		}
		budget -= len(t.Content)
		msgs = append([]routerMessage{t}, msgs...)
	}
	if len(msgs) == 0 {
		return nil, prompt
	}
	return append([]routerMessage{{Role: "system", Content: soloistSystem}}, msgs...), prompt
}

func generation(body map[string]any) map[string]any {
	tokens := soloistDefaultTokens
	for _, k := range []string{"max_completion_tokens", "max_tokens"} {
		if n := num(body[k]); n > 0 {
			tokens = min(n, soloistMaxTokens)
		}
	}
	temp, _ := body["temperature"].(float64)
	return map[string]any{"max_new_tokens": tokens, "temperature": max(0, min(temp, 2)), "top_p": 1}
}

type routerEvent struct {
	name string
	data map[string]any
}

func readEvents(r *bufio.Reader, emit func(routerEvent) bool) error {
	var ev routerEvent
	for {
		line, err := r.ReadString('\n')
		line = strings.TrimRight(line, "\r\n")
		switch {
		case strings.HasPrefix(line, "event: "):
			ev.name = line[7:]
		case strings.HasPrefix(line, "data: "):
			json.Unmarshal([]byte(line[6:]), &ev.data)
		case line == "" && ev.name != "":
			if !emit(ev) {
				return nil
			}
			ev = routerEvent{}
		}
		if err != nil {
			return err
		}
	}
}

// soloistFirst answers on Soloist, or returns the Orchestra route to forward to.
func (g *Gateway) soloistFirst(w http.ResponseWriter, r *http.Request, body map[string]any, route Route, pinned bool) (Route, bool) {
	msgs, prompt := routerMessages(body["messages"])
	if len(msgs) == 0 {
		apiError(w, 400, "INVALID_REQUEST", "There is no message to answer")
		return route, true
	}
	sum := sha256.Sum256([]byte(prompt))
	key := route.Session + "\x00" + hex.EncodeToString(sum[:])
	if !pinned && g.handoffs.has(key) {
		return g.toOrchestra(w, route)
	}

	out, _ := json.Marshal(map[string]any{"messages": msgs, "generation": generation(body), "stream": true})
	req, _ := http.NewRequestWithContext(r.Context(), "POST", g.cfg.RouterURL+"/v1/chat", bytes.NewReader(out))
	req.Header.Set("Content-Type", "application/json")
	res, err := g.local.Do(req)
	if err != nil {
		apiError(w, 502, "UPSTREAM_UNAVAILABLE", "Soloist could not be reached")
		return route, true
	}
	defer res.Body.Close()
	if res.StatusCode != 200 {
		apiError(w, 502, "UPSTREAM_UNAVAILABLE", fmt.Sprintf("Soloist refused the request (status %d)", res.StatusCode))
		return route, true
	}

	stream, _ := body["stream"].(bool)
	model := "soloist/" + g.cfg.SoloistModel
	id := "chatcmpl-soloist-" + hex.EncodeToString(sum[:6])
	created := g.cfg.Now().Unix()
	chunk := func(choices []any, extra map[string]any) {
		c := map[string]any{"id": id, "object": "chat.completion.chunk", "created": created, "model": model, "choices": choices}
		for k, v := range extra {
			c[k] = v
		}
		raw, _ := json.Marshal(c)
		fmt.Fprintf(w, "data: %s\n\n", raw)
		if f, ok := w.(http.Flusher); ok {
			f.Flush()
		}
	}
	started, handoff, failed := false, false, false
	var answer strings.Builder
	start := func() {
		if started {
			return
		}
		started = true
		if stream {
			w.Header().Set("Content-Type", "text/event-stream")
			w.Header().Set("Cache-Control", "no-cache")
			w.WriteHeader(200)
			chunk([]any{map[string]any{"index": 0, "delta": map[string]any{"role": "assistant", "content": ""}}}, nil)
		}
	}
	finish := func(done map[string]any) {
		decision, _ := done["decision"].(map[string]any)
		in, outTokens := num(decision["prompt_tokens"]), num(done["generated_tokens"])
		reason := "stop"
		if done["finish_reason"] == "length" {
			reason = "length"
		}
		usage := map[string]any{"prompt_tokens": in, "completion_tokens": outTokens, "total_tokens": in + outTokens}
		g.record(Entry{Session: route.Session, User: route.UserSub, Place: "device", Model: model, Input: in, Output: outTokens})
		if stream {
			chunk([]any{map[string]any{"index": 0, "delta": map[string]any{}, "finish_reason": reason}}, nil)
			chunk([]any{}, map[string]any{"usage": usage})
			fmt.Fprint(w, "data: [DONE]\n\n")
			return
		}
		writeJSON(w, 200, map[string]any{
			"id": id, "object": "chat.completion", "created": created, "model": model, "usage": usage,
			"choices": []any{map[string]any{"index": 0, "finish_reason": reason, "message": map[string]any{"role": "assistant", "content": answer.String()}}},
		})
	}
	readEvents(bufio.NewReaderSize(res.Body, 64<<10), func(ev routerEvent) bool {
		switch ev.name {
		case "token":
			start()
			piece, _ := ev.data["text"].(string)
			answer.WriteString(piece)
			if stream && piece != "" {
				chunk([]any{map[string]any{"index": 0, "delta": map[string]any{"content": piece}}}, nil)
			}
		case "complete":
			if ev.data["status"] == "HANDOFF_REQUIRED" {
				handoff = true
				return false
			}
			start()
			finish(ev.data)
			return false
		case "error":
			failed = true
			return false
		}
		return true
	})
	switch {
	case handoff && pinned:
		apiError(w, 403, "DATA_TIER_DENIED", "Soloist says this needs Orchestra, but this job is set to stay on the box")
		return route, true
	case handoff:
		g.handoffs.add(key)
		return g.toOrchestra(w, route)
	case !started:
		apiError(w, 502, "LOCAL_EXECUTION_FAILED", "Soloist could not answer")
	case failed && stream:
		raw, _ := json.Marshal(map[string]any{"error": map[string]any{"message": "Soloist stopped unexpectedly", "code": "LOCAL_EXECUTION_FAILED"}})
		fmt.Fprintf(w, "data: %s\n\n", raw)
	}
	return route, true
}

// toOrchestra fills in what a handoff needs; sessions the app never registered
// run as SYMPHONY_DEFAULT_USER_SUB, if set.
func (g *Gateway) toOrchestra(w http.ResponseWriter, route Route) (Route, bool) {
	route.Place = "private"
	if route.UserSub == "" {
		route.UserSub = g.cfg.DefaultUser
	}
	if route.UserSub == "" {
		apiError(w, 401, "USER_UNAUTHORIZED", "Soloist sent this to Orchestra, but no signed-in person is linked to this session")
		return route, true
	}
	if route.Model == "" {
		route.Model = "orchestra"
	}
	if route.Tier == "" {
		route.Tier = "private-cloud"
	}
	return route, false
}
