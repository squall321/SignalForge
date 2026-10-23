# SP 허브 코어 API 의 가짜 — 카탈로그·쪽 넘기기·무덤·숨김·401·404 둘·5xx 를 흉내 내 PLM 거울 수신을 시험한다
"""응답 꼴은 StepForge tests/sp_fake_core.py 가 SP coreapi(f7485c0)에서 옮긴 것을 따른다.
- `GET /api/core` → `{system, revision, types: [{slug, label, …}]}`
- `GET /api/core/{type}?limit&cursor` → `{items, next, as_of}` — next 가 남으면 as_of 는 null.
- 오류 봉투 `{"error": {"code", "message"}}` · 토큰이 틀리면 401 · 공개 안 된 타입 404 `APP-CORE-0001` ·
  못 읽는 커서 404 `APP-CORE-0002`.
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

TOKEN = "sp_pat_FAKE_SECRET_9a1b"


class FakeHub:
    def __init__(self):
        self.objects = {"plm_project": [], "plm_task": [], "plm_model": []}
        self.closed = set()            # 카탈로그에서 뺀 타입
        self.fail_5xx = 0              # 앞으로 이만큼 500
        self.reject_cursor_once = False
        self.calls = []

    def add(self, typ, key, label=None, **props):
        prev = props.pop("previous_keys", None)
        deleted = props.pop("deleted", False)
        hidden = props.pop("hidden", False)
        self.objects[typ].append({"key": key, "label": label or key, "properties": props, "previous_keys": prev or [],
                                  "deleted": deleted, "hidden": hidden, "updated_at": "2026-10-23T00:00:00Z"})

    def respond(self, path, query, auth):
        self.calls.append(path)
        if auth != f"Bearer {TOKEN}":
            return 401, {"error": {"code": "APP-AUTH-0001", "message": "bad token"}}
        if self.fail_5xx:
            self.fail_5xx -= 1
            return 500, {"error": {"code": "APP-COMMON-500", "message": "boom"}}
        if path.rstrip("/") == "/api/core":
            types = [{"slug": t, "label": t} for t in self.objects if t not in self.closed]
            return 200, {"system": "fake-hub", "revision": "r1", "types": types}
        if path.startswith("/api/core/"):
            typ = path[len("/api/core/"):].strip("/")
            if typ not in self.objects or typ in self.closed:
                return 404, {"error": {"code": "APP-CORE-0001", "message": "not open"}}
            limit = int((query.get("limit") or ["500"])[0])
            cursor = (query.get("cursor") or [None])[0]
            if cursor and self.reject_cursor_once:
                self.reject_cursor_once = False
                return 404, {"error": {"code": "APP-CORE-0002", "message": "cursor"}}
            start = int(cursor) if cursor else 0
            rows = self.objects[typ][start:start + limit]
            more = start + limit < len(self.objects[typ])
            return 200, {"items": rows, "next": str(start + limit) if more else None,
                         "as_of": None if more else "2026-10-23T00:00:01Z"}
        return 404, {"error": {"code": "APP-COMMON-404", "message": "no route"}}


def serve(hub: FakeHub):
    class H(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            u = urlsplit(self.path)
            code, body = hub.respond(u.path, parse_qs(u.query), self.headers.get("Authorization"))
            data = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}/api"
