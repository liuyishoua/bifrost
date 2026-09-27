package main

import (
	"context"
	"flag"
	"log"
	"net/http"
	"os"
	"os/signal"
	"strconv"
	"syscall"
	"time"
)

func main() {
	port := flag.Int("port", 0, "local HTTP port")
	flag.Parse()
	if *port < 1 || *port > 65535 {
		log.Fatal("a valid --port is required")
	}
	mux := http.NewServeMux()
	mux.HandleFunc("/", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/html; charset=utf-8")
		_, _ = w.Write([]byte(`<!doctype html><html><head><link rel="stylesheet" href="/static/app.css"></head><body><h1>Weixin demo</h1><p id="result"></p><script>fetch('/api/hello').then(r=>r.json()).then(v=>document.getElementById('result').textContent=v.message)</script></body></html>`))
	})
	mux.HandleFunc("/api/hello", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"message":"hello"}`))
	})
	mux.HandleFunc("/static/app.css", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/css")
		_, _ = w.Write([]byte("body { background: #eef5ff; font-family: sans-serif; }"))
	})

	server := &http.Server{Addr: "127.0.0.1:" + strconv.Itoa(*port), Handler: mux}
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	go func() {
		<-ctx.Done()
		shutdownCtx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()
		_ = server.Shutdown(shutdownCtx)
	}()
	if err := server.ListenAndServe(); err != nil && err != http.ErrServerClosed {
		log.Fatal(err)
	}
}
