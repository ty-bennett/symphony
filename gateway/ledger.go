package main

import (
	"bufio"
	"encoding/json"
	"os"
	"sync"
)

// Price in USD per million tokens.
type Price struct {
	Input     float64 `json:"input"`
	Output    float64 `json:"output"`
	CacheRead float64 `json:"cache_read"`
}

// DefaultPrices: AWS list prices (us-east-1, 2026-10-11). Soloist runs on the box: $0.
func DefaultPrices() map[string]Price {
	return map[string]Price{
		"orchestra/moonshotai.kimi-k2.5":                       {Input: 0.60, Output: 3.00, CacheRead: 0.60},
		"orchestra/us.anthropic.claude-opus-4-5-20251101-v1:0": {Input: 5.50, Output: 27.50, CacheRead: 0.55},
	}
}

// Entry is one model request: who, where, which model, how many tokens, what it cost. No text.
type Entry struct {
	Time      string  `json:"time"`
	Session   string  `json:"session"`
	User      string  `json:"user,omitempty"`
	Place     string  `json:"place"`
	Model     string  `json:"model"`
	Input     int     `json:"input"`
	Output    int     `json:"output"`
	CacheRead int     `json:"cache_read,omitempty"`
	Cost      float64 `json:"cost"`
	Priced    bool    `json:"priced"`
}

type Summary struct {
	Requests int                `json:"requests"`
	Input    int                `json:"input"`
	Output   int                `json:"output"`
	Cost     float64            `json:"cost"`
	ByPlace  map[string]float64 `json:"by_place"`
	Unpriced int                `json:"unpriced"`
}

type Ledger struct {
	mu     sync.Mutex
	path   string
	prices map[string]Price
}

func (l *Ledger) Append(e Entry) error {
	if e.Place == "device" {
		e.Priced = true
	} else if p, ok := l.prices[e.Model]; ok {
		uncached := e.Input - e.CacheRead
		e.Cost = (float64(uncached)*p.Input + float64(e.CacheRead)*p.CacheRead + float64(e.Output)*p.Output) / 1e6
		e.Priced = true
	}
	line, _ := json.Marshal(e)
	l.mu.Lock()
	defer l.mu.Unlock()
	f, err := os.OpenFile(l.path, os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0o600)
	if err != nil {
		return err
	}
	defer f.Close()
	_, err = f.Write(append(line, '\n'))
	return err
}

func (l *Ledger) Summary(keep func(Entry) bool) Summary {
	s := Summary{ByPlace: map[string]float64{"device": 0, "private": 0}}
	l.mu.Lock()
	defer l.mu.Unlock()
	f, err := os.Open(l.path)
	if err != nil {
		return s
	}
	defer f.Close()
	scan := bufio.NewScanner(f)
	for scan.Scan() {
		var e Entry
		if json.Unmarshal(scan.Bytes(), &e) != nil || !keep(e) {
			continue
		}
		s.Requests++
		s.Input += e.Input
		s.Output += e.Output
		s.Cost += e.Cost
		s.ByPlace[e.Place] += e.Cost
		if !e.Priced {
			s.Unpriced++
		}
	}
	return s
}
