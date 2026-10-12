// Refresh tokens kept on the box (edge-gateway.md, "Tokens"): <data dir>/users/<sub>.refresh,
// mode 0600, written by `symphony login` (for now scripts/link-orchestra.sh). The Jetson's
// app client is public, so renewing an access token needs no AWS credentials.
package main

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"regexp"
	"strings"
)

type accessToken struct {
	AccessToken string `json:"access_token"`
	ExpiresIn   int    `json:"expires_in"`
}

var validSub = regexp.MustCompile(`^[A-Za-z0-9-]{1,128}$`)

func (g *Gateway) refreshAtCognito(ctx context.Context, sub string) (accessToken, bool, error) {
	if g.cfg.CognitoClientID == "" || !validSub.MatchString(sub) {
		return accessToken{}, false, nil
	}
	raw, err := os.ReadFile(filepath.Join(g.cfg.DataDir, "users", sub+".refresh"))
	if errors.Is(err, os.ErrNotExist) {
		return accessToken{}, false, nil
	} else if err != nil {
		return accessToken{}, false, err
	}
	body, _ := json.Marshal(map[string]any{
		"AuthFlow":       "REFRESH_TOKEN_AUTH",
		"ClientId":       g.cfg.CognitoClientID,
		"AuthParameters": map[string]string{"REFRESH_TOKEN": strings.TrimSpace(string(raw))},
	})
	req, _ := http.NewRequestWithContext(ctx, "POST", g.cfg.CognitoURL, bytes.NewReader(body))
	req.Header.Set("Content-Type", "application/x-amz-json-1.1")
	req.Header.Set("X-Amz-Target", "AWSCognitoIdentityProviderService.InitiateAuth")
	res, err := g.local.Do(req)
	if err != nil {
		return accessToken{}, true, fmt.Errorf("cognito: %w", err)
	}
	defer res.Body.Close()
	data, _ := io.ReadAll(io.LimitReader(res.Body, 1<<20))
	if res.StatusCode != 200 {
		var e struct {
			Type string `json:"__type"` // never log the body: it may echo the token
		}
		json.Unmarshal(data, &e)
		return accessToken{}, true, fmt.Errorf("cognito refresh: status %d %s", res.StatusCode, e.Type)
	}
	var out struct {
		AuthenticationResult struct {
			AccessToken string
			ExpiresIn   int
		}
	}
	if err := json.Unmarshal(data, &out); err != nil || out.AuthenticationResult.AccessToken == "" {
		return accessToken{}, true, errors.New("cognito refresh: no access token")
	}
	return accessToken{out.AuthenticationResult.AccessToken, out.AuthenticationResult.ExpiresIn}, true, nil
}
