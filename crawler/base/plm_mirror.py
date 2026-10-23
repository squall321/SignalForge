# SP 허브 코어 API 에서 과제·개발모델·프로젝트를 통째로 받아 VOC DB 밖 SQLite 파일(읽기 전용 거울)로 두고 조회 색인을 만든다
"""PLM 거울 — StandardPlatform(SP) 허브의 과제(plm_task)·개발모델(plm_model)·프로젝트(plm_project).

참고 — StepForge `core/sp_core.py`(증분 수신 · 수신 규칙 열하나), SP `backend/app/modules/coreapi/kit/sp_core_pull.py`@f7485c0.
SignalForge 는 조회용 **현재 상태**만 쓴다(이력도 쓰기도 없다). 그래서 매번 전체 스냅샷을 받아 임시 파일에 쓰고, 끝까지
받았을 때만 `os.replace` 로 바꾼다 — 도중에 실패하면 옛 거울이 그대로 남는다. since·as_of·reset·부재 대조가 필요 없어진다.
남기는 수신 규칙 — key 로 싣는다 · `deleted`·`hidden` 은 싣지 않는다 · 모르는 칸은 무시(properties 는 통째로) ·
`previous_keys` 는 옛 과제코드로도 찾게 둔다 · 401/403 은 멈춤 · 429/5xx 는 재시도 · 404 둘(`-CORE-0001` 공개 안 된 타입 ·
`-CORE-0002` 커서를 못 읽음 — 처음부터 한 번 더).

급감 보류 — 새 스냅샷의 한 타입이 옛 것의 절반 아래로 줄면(옛 것 10건 이상) 바꾸지 않고 알린다. 토큰의 부서·공개 범위가
바뀌어 반이 사라진 것을 "과제가 없어졌다" 로 믿지 않는다(StepForge 와 같은 문턱).

보관 — `crawler/data/plm/sp_mirror.sqlite`. VOC DB 밖이라 30분 Drive 백업·cae00 덤프에 실리지 않는다(과제코드·과제명은
사내 정보 — 사용자 결정 2026-10-23). 허브를 볼 수 있는 서버가 직접 받는다.
설정 — 주소는 `.env` 의 `SF_SP_CORE_URL`(허브 API 루트, 끝에 /api 까지). 토큰은 **파일로만** — `crawler/data/plm/sp_token`
(한 줄, 또는 `SF_SP_TOKEN=…` 줄) 또는 `SF_SP_TOKEN_FILE` 이 가리키는 파일. `.env` 에 두지 않는다 — up.sh 가 .env 전부를
모든 컨테이너 환경으로 내보낸다. 토큰은 로그·상태 파일·예외 문구에 남기지 않는다.
"""
from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlsplit

log = logging.getLogger(__name__)

TYPES = ("plm_project", "plm_task", "plm_model")
REQUIRED = ("plm_task", "plm_model")
ENV_URL = "SF_SP_CORE_URL"
ENV_TOKEN_FILE = "SF_SP_TOKEN_FILE"
TOKEN_KEY = "SF_SP_TOKEN"
PAGE_LIMIT = 500
TIMEOUT_S = 60.0
RETRIES = 3
RETRY_PAUSE_S = 2.0
HOLD_RATIO = 0.5
HOLD_MIN = 10
MIRROR = "sp_mirror.sqlite"
STATUS = "status.json"


class NotConfigured(ValueError):
    """주소나 토큰이 없다 — 받기를 건너뛴다(오류가 아니다)."""


class SyncError(RuntimeError):
    pass


class TypeClosed(SyncError):
    """허브가 그 타입을 코어로 열지 않는다(`-CORE-0001`)."""


class CursorRejected(SyncError):
    """허브가 커서를 못 읽는다(`-CORE-0002`) — 처음부터 다시."""


def default_root() -> Path:
    return Path(__file__).resolve().parents[1] / "data" / "plm"


# ── 설정 ────────────────────────────────────────────────────────────────
def _base_url(raw: str) -> str:
    url = (raw or "").strip().rstrip("/")
    return url[: -len("/core")] if url.endswith("/core") else url


def read_token(path: Path) -> str:
    """토큰 파일 → 토큰. 한 줄 토큰이거나 `SF_SP_TOKEN=…` 줄(다른 줄은 읽지 않는다). 못 읽으면 NotConfigured — 경로만 말한다."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        raise NotConfigured(f"토큰 파일을 못 읽는다: {path}") from None
    lines = [ln.strip() for ln in text.splitlines() if ln.strip() and not ln.strip().startswith("#")]
    for ln in lines:
        key, sep, value = ln.partition("=")
        if sep and key.strip().removeprefix("export ").strip() == TOKEN_KEY:
            token = value.strip().strip("'\"")
            if token:
                return token
    if len(lines) == 1 and "=" not in lines[0]:
        return lines[0]
    raise NotConfigured(f"토큰 파일에 토큰이 없다(한 줄 토큰 또는 {TOKEN_KEY}= 줄): {path}")


def settings(env=None, root: Optional[Path] = None) -> dict:
    env = os.environ if env is None else env
    root = Path(root or default_root())
    url = _base_url(env.get(ENV_URL, ""))
    token_file = Path(env.get(ENV_TOKEN_FILE) or root / "sp_token")
    problems = []
    if not url:
        problems.append(f".env 의 {ENV_URL} 가 비었다(허브 API 루트, 끝에 /api 까지)")
    if not token_file.is_file():
        problems.append(f"토큰 파일이 없다: {token_file}")
    return {"url": url, "host": urlsplit(url).netloc if url else None, "token_file": str(token_file),
            "configured": not problems, "problems": problems}


# ── 받기 ────────────────────────────────────────────────────────────────
class Hub:
    """허브 하나. 토큰은 이 객체 밖으로 나가지 않는다 — 오류 문구에서도 지운다."""

    def __init__(self, base_url: str, token: str, *, client=None, pause_s: float = RETRY_PAUSE_S):
        import httpx
        self.base = _base_url(base_url)
        self._token = token
        self._client = client or httpx.Client(timeout=TIMEOUT_S)
        self._pause = pause_s

    def _scrub(self, text: str) -> str:
        return (text or "").replace(self._token, "***") if self._token else (text or "")

    def get(self, path: str, params: Optional[dict] = None) -> dict:
        import httpx
        url = f"{self.base}{path}"
        headers = {"Authorization": f"Bearer {self._token}", "Accept": "application/json"}
        last = ""
        for attempt in range(1, RETRIES + 1):
            try:
                r = self._client.get(url, headers=headers, params=params)
            except httpx.HTTPError as e:
                last = self._scrub(f"연결 실패 {type(e).__name__}")
                time.sleep(self._pause * attempt)
                continue
            if r.status_code in (401, 403):
                raise SyncError(f"허브가 인증을 거절했다(HTTP {r.status_code}) — 토큰과 core:read 범위를 볼 것")
            if r.status_code == 404:
                code = self._error_code(r)
                if code.endswith("-CORE-0001"):
                    raise TypeClosed(f"허브가 그 타입을 코어로 열지 않는다: {path}")
                if code.endswith("-CORE-0002"):
                    raise CursorRejected(f"허브가 커서를 못 읽는다: {path}")
                raise SyncError(f"허브에 없는 주소다(HTTP 404 {code or '-'}): {path} — 주소가 /api 까지인지 볼 것")
            if r.status_code == 429 or r.status_code >= 500:
                last = f"HTTP {r.status_code}"
                time.sleep(self._pause * attempt)
                continue
            if r.status_code >= 400:
                raise SyncError(f"허브가 요청을 거절했다(HTTP {r.status_code}): {path}")
            try:
                return r.json()
            except ValueError:
                raise SyncError(f"허브 응답이 JSON 이 아니다: {path}") from None
        raise SyncError(f"허브가 {RETRIES}번 모두 답하지 않았다({last}): {path}")

    def _error_code(self, r) -> str:
        try:
            return str(((r.json() or {}).get("error") or {}).get("code") or "")
        except ValueError:
            return ""


def _pull_type(hub: Hub, slug: str) -> List[dict]:
    """한 타입 전부 — since 없이 next 가 빌 때까지. 커서를 못 읽으면 처음부터 한 번 더."""
    for attempt in range(2):
        items: List[dict] = []
        cursor = None
        try:
            while True:
                params = {"limit": PAGE_LIMIT, **({"cursor": cursor} if cursor else {})}
                body = hub.get(f"/core/{slug}", params) or {}
                items += [it for it in body.get("items") or []
                          if isinstance(it, dict) and it.get("key") and not it.get("deleted") and not it.get("hidden")]
                cursor = body.get("next")
                if not cursor:
                    return items
        except CursorRejected:
            if attempt:
                raise
            log.warning("[plm] %s 커서를 허브가 못 읽는다 — 처음부터 다시 받는다", slug)
    return []


def pull(hub: Hub) -> Tuple[dict, Dict[str, List[dict]]]:
    cat = hub.get("/core") or {}
    opened = {t.get("slug") for t in cat.get("types") or [] if isinstance(t, dict)}
    missing = [t for t in REQUIRED if t not in opened]
    if missing:
        raise TypeClosed(f"허브 카탈로그에 {', '.join(missing)} 가 없다 — 허브가 과제·개발모델을 코어로 열어야 한다")
    data = {}
    for slug in TYPES:
        if slug not in opened:
            data[slug] = []
            continue
        data[slug] = _pull_type(hub, slug)
    meta = {"system": cat.get("system"), "revision": cat.get("revision")}
    return meta, data


# ── 쓰기 ────────────────────────────────────────────────────────────────
_SCHEMA = """
CREATE TABLE objects (type TEXT NOT NULL, key TEXT NOT NULL, label TEXT, properties TEXT, previous_keys TEXT,
                      merged_into TEXT, updated_at TEXT, PRIMARY KEY (type, key));
CREATE TABLE meta (k TEXT PRIMARY KEY, v TEXT);
"""


def _counts(path: Path) -> Dict[str, int]:
    if not path.is_file():
        return {}
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            return {t: n for t, n in con.execute("SELECT type, count(*) FROM objects GROUP BY type")}
        finally:
            con.close()
    except sqlite3.Error:
        return {}


def held_types(old: Dict[str, int], new: Dict[str, int]) -> List[str]:
    """절반 넘게 줄어든 타입(옛 것 HOLD_MIN 건 이상만) — 바꾸지 않을 까닭."""
    return [t for t, n in old.items() if n >= HOLD_MIN and new.get(t, 0) < n * HOLD_RATIO]


def write_snapshot(root: Path, meta: dict, data: Dict[str, List[dict]], *, host: Optional[str] = None,
                   allow_drop: bool = False) -> dict:
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    path = root / MIRROR
    new = {t: len(v) for t, v in data.items()}
    old = _counts(path)
    held = [] if allow_drop else held_types(old, new)
    if held:
        return {"status": "held", "held": held, "old": old, "new": new}
    tmp = root / f".{MIRROR}.tmp"
    tmp.unlink(missing_ok=True)
    con = sqlite3.connect(tmp)
    try:
        con.executescript(_SCHEMA)
        for slug, items in data.items():
            con.executemany(
                "INSERT OR REPLACE INTO objects VALUES (?,?,?,?,?,?,?)",
                [(slug, str(it["key"]), it.get("label"), json.dumps(it.get("properties") or {}, ensure_ascii=False),
                  json.dumps(it.get("previous_keys") or [], ensure_ascii=False), it.get("merged_into"), it.get("updated_at"))
                 for it in items])
        at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        con.executemany("INSERT INTO meta VALUES (?,?)", [
            ("pulled_at", at), ("host", host or ""), ("system", str(meta.get("system") or "")),
            ("revision", str(meta.get("revision") or "")), ("counts", json.dumps(new))])
        con.commit()
    finally:
        con.close()
    os.replace(tmp, path)
    return {"status": "ok", "counts": new, "old": old, "pulled_at": at}


def _write_status(root: Path, status: dict):
    tmp = Path(root) / f".{STATUS}.tmp"
    tmp.write_text(json.dumps(status, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, Path(root) / STATUS)


def sync(env=None, root: Optional[Path] = None, *, client=None, allow_drop: bool = False,
         pause_s: float = RETRY_PAUSE_S) -> dict:
    """한 번 받기. 주소·토큰이 없으면 not_configured(조용히 끝난다). 결과는 status.json 에도 남긴다(토큰 없음)."""
    root = Path(root or default_root())
    s = settings(env, root)
    started = time.monotonic()
    if not s["configured"]:
        return {"status": "not_configured", "problems": s["problems"]}
    try:
        hub = Hub(s["url"], read_token(Path(s["token_file"])), client=client, pause_s=pause_s)
        meta, data = pull(hub)
        result = write_snapshot(root, meta, data, host=s["host"], allow_drop=allow_drop)
    except NotConfigured as e:
        return {"status": "not_configured", "problems": [str(e)]}
    except SyncError as e:
        result = {"status": "error", "error": str(e)}
    result = {**result, "host": s["host"], "elapsed_sec": round(time.monotonic() - started, 1),
              "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    root.mkdir(parents=True, exist_ok=True)
    _write_status(root, result)
    return result


# ── 읽기 — 조회 색인 ─────────────────────────────────────────────────────
_REV_SUFFIX = re.compile(r"^(.+?)[_\s]+([0-9a-z]{1,4})$", re.IGNORECASE)   # '감마_C2' → '감마' (과제명 뒤 차수 접미어)


def _fold(text: str) -> str:
    return " ".join((text or "").lower().split())


class PlmIndex:
    """거울 파일 하나의 조회 색인. 과제코드(옛 코드 포함)·과제명(차수 접미어를 뗀 것도)·개발모델명·양산모델코드 → 과제."""

    def __init__(self, path: Path):
        self.path = Path(path)
        con = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True)
        try:
            rows = con.execute("SELECT type, key, label, properties, previous_keys FROM objects").fetchall()
            self.meta = {k: v for k, v in con.execute("SELECT k, v FROM meta")}
        finally:
            con.close()
        self.tasks: Dict[str, dict] = {}
        self.models: Dict[str, dict] = {}
        self.projects: Dict[str, str] = {}
        for typ, key, label, props, prev in rows:
            p = json.loads(props or "{}")
            if typ == "plm_task":
                self.tasks[key] = {"key": key, "label": label or "", "project": p.get("project"),
                                   "status": p.get("task_status"), "previous_keys": json.loads(prev or "[]"), "models": []}
            elif typ == "plm_model":
                self.models[key] = {"key": key, "label": label or key, "task": p.get("task"),
                                    "base_code": p.get("base_code"), "mass_model_code": p.get("mass_model_code"),
                                    "model_kind": p.get("model_kind"), "product_text": p.get("product_text")}
            elif typ == "plm_project":
                self.projects[key] = label or key
        for m in self.models.values():
            if m["task"] in self.tasks:
                self.tasks[m["task"]]["models"].append(m["key"])
        self.by_code: Dict[str, str] = {}
        self.by_name: Dict[str, List[str]] = {}
        self.by_model: Dict[str, str] = {}
        for t in self.tasks.values():
            self.by_code[_fold(t["key"])] = t["key"]
            for old in t["previous_keys"]:
                self.by_code.setdefault(_fold(str(old)), t["key"])
            name = _fold(t["label"])
            if len(name) >= 2:
                self.by_name.setdefault(name, []).append(t["key"])
                core = _REV_SUFFIX.match(name)
                if core and len(core.group(1)) >= 2:
                    self.by_name.setdefault(core.group(1), []).append(t["key"])
        for m in self.models.values():
            for name in (m["key"], m["label"], m["mass_model_code"]):
                if name:
                    self.by_model.setdefault(_fold(str(name)), m["key"])
        # 띄어 쓴 과제명은 질의의 낱말 하나로 안 잡힌다 — 질의 해석이 통째로 찾는다(긴 것부터)
        self.multi_names: List[str] = sorted((n for n in self.by_name if " " in n), key=len, reverse=True)

    def __len__(self) -> int:
        return len(self.tasks)

    def task_models(self, task_key: str) -> List[dict]:
        t = self.tasks.get(task_key)
        return [self.models[k] for k in (t["models"] if t else [])]


_cache: Dict[str, Tuple[float, float, Optional[PlmIndex]]] = {}


def mirror(root: Optional[Path] = None, *, check_every_s: float = 30.0) -> Optional[PlmIndex]:
    """거울 색인(없으면 None). 파일이 바뀌면 다시 연다 — 30초마다만 본다."""
    path = Path(root or default_root()) / MIRROR
    key = str(path)
    now = time.monotonic()
    hit = _cache.get(key)
    if hit and now - hit[0] < check_every_s:
        return hit[2]
    try:
        mtime = path.stat().st_mtime
    except OSError:
        _cache[key] = (now, 0.0, None)
        return None
    if hit and hit[1] == mtime:
        _cache[key] = (now, mtime, hit[2])
        return hit[2]
    try:
        idx = PlmIndex(path)
    except (sqlite3.Error, ValueError) as e:
        log.warning("[plm] 거울을 못 열었다: %r", e)
        idx = hit[2] if hit else None
    _cache[key] = (now, mtime, idx)
    return idx
