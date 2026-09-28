package main

import (
	"context"
	"flag"
	"log"
	"net"
	"net/http"
	"os"
	"os/exec"
	"os/signal"
	"runtime"
	"strconv"
	"syscall"
	"time"
)

func main() {
	port := flag.Int("port", 0, "local HTTP port; 0 selects a free port and opens a browser")
	flag.Parse()
	if *port < 0 || *port > 65535 {
		log.Fatal("a valid --port is required")
	}
	listener, err := net.Listen("tcp", "127.0.0.1:"+strconv.Itoa(*port))
	if err != nil {
		log.Fatal(err)
	}
	actualPort := listener.Addr().(*net.TCPAddr).Port
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

	server := &http.Server{Handler: mux}
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	go func() {
		<-ctx.Done()
		shutdownCtx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()
		_ = server.Shutdown(shutdownCtx)
	}()
	if *port == 0 {
		address := "http://127.0.0.1:" + strconv.Itoa(actualPort) + "/"
		var command *exec.Cmd
		if runtime.GOOS == "windows" {
			command = exec.Command("rundll32", "url.dll,FileProtocolHandler", address)
		} else if runtime.GOOS == "darwin" {
			command = exec.Command("open", address)
		}
		if command != nil {
			go func() {
				if err := command.Run(); err != nil {
					log.Printf("无法自动打开浏览器：%v", err)
				}
			}()
		}
		log.Printf("打开 %s", address)
	}
	if err := server.Serve(listener); err != nil && err != http.ErrServerClosed {
		log.Fatal(err)
	}
}
