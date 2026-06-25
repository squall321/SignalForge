# SignalForge frontend 프로덕션 빌드(dist) 정적 서빙 + /api 프록시 + SPA fallback.
# vite dev 서버가 이 환경(no-TTY/Bash 툴)에서 ready 직후 죽는 문제를 우회하는 안정 서버.
# - GET/POST/PUT/DELETE /api/* → http://127.0.0.1:18000/api/* 로 프록시 (dev proxy 동등)
# - 그 외 경로: dist 내 실제 파일 있으면 그 파일, 없으면 index.html (BrowserRouter SPA)
import os
import sys
import http.server
import socketserver
import urllib.request
import urllib.error

DIST = "/home/koopark/claude/SignalForge/frontend/dist"
BACKEND = "http://127.0.0.1:18000"
PORT = int(os.environ.get("FRONTEND_PORT", "17370"))
# 이 호스트는 0.0.0.0:17370(전체 인터페이스) 바인딩을 보안정책상 SIGKILL 한다.
# localhost 바인딩은 허용 → 기본 127.0.0.1. (포털·로컬 접근 모두 same-host 라 충분)
HOST = os.environ.get("FRONTEND_HOST", "127.0.0.1")
PROXY_PREFIXES = ("/api",)


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=DIST, **kw)

    def log_message(self, fmt, *args):  # 조용히
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def _is_proxy(self):
        return self.path.startswith(PROXY_PREFIXES)

    def _proxy(self):
        body = None
        clen = self.headers.get("Content-Length")
        if clen:
            body = self.rfile.read(int(clen))
        url = BACKEND + self.path
        req = urllib.request.Request(url, data=body, method=self.command)
        for h in ("Content-Type", "Authorization", "Cookie", "Accept"):
            v = self.headers.get(h)
            if v:
                req.add_header(h, v)
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                self.send_response(r.status)
                for k, v in r.headers.items():
                    if k.lower() in ("transfer-encoding", "connection", "content-length"):
                        continue
                    self.send_header(k, v)
                data = r.read()
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
        except urllib.error.HTTPError as e:
            data = e.read()
            self.send_response(e.code)
            for k, v in e.headers.items():
                if k.lower() in ("transfer-encoding", "connection", "content-length"):
                    continue
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except Exception as e:  # backend down 등
            msg = ("proxy error: %s" % e).encode()
            self.send_response(502)
            self.send_header("Content-Length", str(len(msg)))
            self.end_headers()
            self.wfile.write(msg)

    def _spa_get(self):
        # 실제 파일이면 정적 서빙, 아니면 index.html (클라이언트 라우팅)
        rel = self.path.split("?", 1)[0].lstrip("/")
        fpath = os.path.join(DIST, rel)
        if rel and os.path.isfile(fpath):
            return super().do_GET()
        self.path = "/index.html"
        return super().do_GET()

    def do_GET(self):
        if self._is_proxy():
            return self._proxy()
        return self._spa_get()

    def do_POST(self):
        return self._proxy() if self._is_proxy() else self.send_error(405)

    def do_PUT(self):
        return self._proxy() if self._is_proxy() else self.send_error(405)

    def do_DELETE(self):
        return self._proxy() if self._is_proxy() else self.send_error(405)


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


if __name__ == "__main__":
    with Server((HOST, PORT), Handler) as httpd:
        sys.stderr.write("SignalForge frontend(prod) serving dist on %s:%d (api→%s)\n" % (HOST, PORT, BACKEND))
        httpd.serve_forever()
