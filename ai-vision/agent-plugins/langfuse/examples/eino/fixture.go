package main

import (
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
)

// The fixture replaces only the model HTTP service. Eino, its OpenAI model
// component, tool execution, callback, and OTLP exporter all execute normally.
func modelFixture() (*httptest.Server, <-chan struct{}) {
	started := make(chan struct{})
	var once sync.Once
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		parts := strings.Split(strings.Trim(r.URL.Path, "/"), "/")
		if len(parts) != 4 || parts[1] != "v1" || parts[2] != "chat" || parts[3] != "completions" {
			http.NotFound(w, r)
			return
		}
		scenario := parts[0]
		var request struct {
			Stream bool `json:"stream"`
		}
		if json.NewDecoder(r.Body).Decode(&request) != nil {
			w.WriteHeader(400)
			return
		}
		if scenario == "cancel" {
			once.Do(func() { close(started) })
			<-r.Context().Done()
			return
		}
		w.Header().Set("Content-Type", "application/json")
		if scenario == "error" {
			w.WriteHeader(400)
			fmt.Fprint(w, `{"error":{"message":"controlled provider failure","type":"invalid_request_error"}}`)
			return
		}
		if request.Stream {
			w.Header().Set("Content-Type", "text/event-stream")
			for _, chunk := range []string{
				`{"id":"fixture-completion","object":"chat.completion.chunk","model":"fixture-model","choices":[{"index":0,"delta":{"role":"assistant","tool_calls":[{"index":0,"id":"fixture-call","type":"function","function":{"name":"echo","arguments":"{\"text\":\"hello-"}}]}}]}`,
				`{"id":"fixture-completion","object":"chat.completion.chunk","model":"fixture-model","choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"function":{"arguments":"eino\"}"}}]},"finish_reason":"tool_calls"}]}`,
				`{"id":"fixture-completion","object":"chat.completion.chunk","model":"fixture-model","choices":[],"usage":{"prompt_tokens":11,"completion_tokens":7,"total_tokens":18}}`,
			} {
				fmt.Fprintf(w, "data: %s\n\n", chunk)
				w.(http.Flusher).Flush()
			}
			fmt.Fprint(w, "data: [DONE]\n\n")
			return
		}
		response := map[string]any{"id": "fixture-completion", "object": "chat.completion", "model": "fixture-model", "choices": []any{map[string]any{"index": 0, "finish_reason": "tool_calls", "message": map[string]any{"role": "assistant", "content": "", "tool_calls": []any{map[string]any{"id": "fixture-call", "type": "function", "function": map[string]string{"name": "echo", "arguments": `{"text":"hello-eino"}`}}}}}}}
		if scenario == "zero_usage" {
			response["usage"] = map[string]int{"prompt_tokens": 11, "completion_tokens": 0, "total_tokens": 11}
		} else if scenario != "missing_usage" {
			response["usage"] = map[string]int{"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18}
		}
		json.NewEncoder(w).Encode(response)
	}))
	return server, started
}
