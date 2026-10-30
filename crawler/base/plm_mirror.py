# SP 허브 코어 API 의 과제·개발모델·프로젝트를 SP 키트 받기(sp_core_pull)로 사본에 맞추고, 사본을 읽어 조회 색인을 만든다
"""PLM 거울 — StandardPlatform(SP) 허브의 과제(plm_task)·개발모델(plm_model)·프로젝트(plm_project).

두 층(키트 01-AI-GUIDE §3).
- 층 1 받기 — SP 키트의 참고 구현 `crawler/sp_core_kit/sp_core_pull.py`(SP 778db2f · KIT_VERSION 2026-10-21)를 **고치지
  않고** 자식 프로세스로 돌린다. 받는 쪽 규칙 열여섯(since·as_of 증분 · 옛 식별자 옮기기 · 지움·합침·안 보임 · 선 끊기 ·
  처음부터 받은 뒤 부재 대조와 절반 안전장치 · reset · 404 둘 · 정의 판)은 키트가 지킨다. 사본은 `sp-core/sp_core.sqlite`
  (타입별 표 · `<타입>__relations` · `<타입>__previous_keys`). 감싸는 쪽(sync)이 더하는 것 — 잠금(규칙 14, 참고 구현에
  없다) · 실행 기록(status.json — 정상·확인 필요·실패와 사유) · 주 1회 처음부터 받기 · 토큰은 자식 프로세스 환경
  (SP_TOKEN)으로만 · 수신 이력 CSV 는 지운다(현재 상태만 쓴다).
- 층 2 반영 — PlmIndex 가 사본의 활성 행을 읽어 조회 색인을 만든다. 저장하는 레코드가 없어(질의 해석뿐) 매번 통째로 읽는다.

보관 — `crawler/data/plm/`. VOC DB 밖이라 30분 Drive 백업·cae00 덤프에 실리지 않는다(과제코드·과제명은 사내 정보 —
사용자 결정 2026-10-23). 허브를 볼 수 있는 서버가 직접 받는다.
설정 — 주소는 `.env` 의 `SF_SP_CORE_URL`(허브 API 루트, 끝에 /api 까지). 토큰은 **파일로만** — `crawler/data/plm/sp_token`
(한 줄, 또는 `SF_SP_TOKEN=…` 줄) 또는 `SF_SP_TOKEN_FILE` 이 가리키는 파일. `.env` 에 두지 않는다 — up.sh 가 .env 전부를
모든 컨테이너 환경으로 내보낸다. 토큰은 로그·상태 파일·예외 문구에 남기지 않는다. 받을 타입은 `SF_SP_CORE_TYPES`
(쉼표, 기본 plm_project·plm_task·plm_model, `*` 면 공개 타입 전부).
"""
from __future__ import annotations

import fcntl
import json
import logging
import os
import re
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlsplit

log = logging.getLogger(__name__)

TYPES = ("plm_project", "plm_task", "plm_model")
ENV_URL = "SF_SP_CORE_URL"
ENV_TOKEN_FILE = "SF_SP_TOKEN_FILE"
ENV_TYPES = "SF_SP_CORE_TYPES"
TOKEN_KEY = "SF_SP_TOKEN"
KIT_PULL = Path(__file__).resolve().parents[1] / "sp_core_kit" / "sp_core_pull.py"
OUT = "sp-core"                         # 키트의 out_dir — 사본 · 기준 시각(state.json) · 카탈로그
MIRROR = f"{OUT}/sp_core.sqlite"        # 사본 — root 아래
STATUS = "status.json"
FULL_EVERY = timedelta(days=7)          # 주 1회 처음부터 — 표식 없이 빠진 것까지 맞춘다(키트 §2-6)
RUN_TIMEOUT_S = 1800


class NotConfigured(ValueError):
    """주소나 토큰이 없다 — 받기를 건너뛴다(오류가 아니다)."""


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
    raw_types = (env.get(ENV_TYPES) or "").strip()
    types = [] if raw_types == "*" else [t.strip() for t in (raw_types or ",".join(TYPES)).split(",") if t.strip()]
    problems = []
    if not url:
        problems.append(f".env 의 {ENV_URL} 가 비었다(허브 API 루트, 끝에 /api 까지)")
    if not token_file.is_file():
        problems.append(f"토큰 파일이 없다: {token_file}")
    return {"url": url, "host": urlsplit(url).netloc if url else None, "token_file": str(token_file),
            "types": types, "configured": not problems, "problems": problems}


def kit_version() -> str:
    """키트 판 — 받기를 import 하지 않고 읽는다(mcp 이미지에는 requests 가 없다)."""
    try:
        m = re.search(r'^KIT_VERSION\s*=\s*"([^"]+)"', KIT_PULL.read_text(encoding="utf-8"), re.M)
    except OSError:
        return ""
    return m.group(1) if m else ""


def last_status(root: Optional[Path] = None) -> dict:
    try:
        return json.loads((Path(root or default_root()) / STATUS).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _write_status(root: Path, status: dict):
    tmp = Path(root) / f".{STATUS}.tmp"
    tmp.write_text(json.dumps(status, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, Path(root) / STATUS)


def _when(text) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(str(text))
    except ValueError:
        return None


# ── 받기 (층 1 — 키트) ──────────────────────────────────────────────────
def sync(env=None, root: Optional[Path] = None, *, full: bool = False, allow_shrink: bool = False) -> dict:
    """한 번 받기 — 키트 받기를 자식 프로세스로. 결과는 status.json 에도 남긴다(토큰 없음).

    status — ok(정상) · check(확인 필요 — 끝까지 돌았지만 사람이 볼 것: 공개 안 된 타입 · 절반 넘게 안 와 정리를 멈춘
    타입) · error(멈춤 — 인증·주소·연결·서버) · busy(다른 받기가 도는 중) · not_configured(주소·토큰 없음, 조용히 끝난다).
    full — 처음부터 다시 받기(끝까지 받은 뒤 안 온 것을 정리). 마지막 처음부터 받기가 7일을 넘었으면 저절로 그렇게 한다.
    allow_shrink — 절반 넘게 안 와도 정리한다(사람이 토큰 범위를 확인한 뒤에만)."""
    root = Path(root or default_root())
    s = settings(env, root)
    if not s["configured"]:
        return {"status": "not_configured", "problems": s["problems"]}
    try:
        token = read_token(Path(s["token_file"]))
    except NotConfigured as e:
        return {"status": "not_configured", "problems": [str(e)]}
    out = root / OUT
    out.mkdir(parents=True, exist_ok=True)
    with open(out / ".lock", "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)      # 규칙 14 — beat · cron · 손 실행이 겹치지 않게
        except OSError:
            return {"status": "busy", "note": "다른 받기가 돌고 있다 — 이번 차례는 건너뛴다"}
        return _run(s, token, root, out, full=full, allow_shrink=allow_shrink)


def _run(s: dict, token: str, root: Path, out: Path, *, full: bool, allow_shrink: bool) -> dict:
    prev = last_status(root)
    now = datetime.now(timezone.utc)
    if not full:
        last_full = _when(prev.get("last_full_at"))
        full = last_full is None or now - last_full >= FULL_EVERY
    cfg = out / "config.ini"                                     # 토큰은 쓰지 않는다 — SP_TOKEN 으로만 넘긴다
    cfg.write_text(f"[sp]\nbase_url = {s['url']}\ntypes = {', '.join(s['types'])}\nout_dir = {out}\n", encoding="utf-8")
    cmd = [sys.executable, "-B", str(KIT_PULL), "--config", str(cfg)]
    cmd += ["--full"] if full else []
    cmd += ["--allow-shrink"] if allow_shrink else []
    started = time.monotonic()
    try:
        p = subprocess.run(cmd, env={**os.environ, "SP_TOKEN": token, "PYTHONDONTWRITEBYTECODE": "1"},
                           capture_output=True, text=True, timeout=RUN_TIMEOUT_S)
        rc, stdout, stderr = p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired:
        rc, stdout, stderr = 1, "", f"받기가 {RUN_TIMEOUT_S}초 안에 끝나지 않았다"
    for f in out.glob("*.csv"):                                  # 수신 이력 — 현재 상태만 쓴다
        f.unlink(missing_ok=True)
    lines = [ln for ln in (stdout or "").replace(token, "***").splitlines() if ln.strip()]
    errs = [ln for ln in (stderr or "").replace(token, "***").splitlines() if ln.strip()]
    status = {0: "ok", 2: "check"}.get(rc, "error")
    at = now.isoformat(timespec="seconds")
    result = {"status": status, "full": full, "kit": kit_version(), "host": s["host"], "types": s["types"] or "*",
              "problems": [ln.removeprefix("확인 필요:").strip() for ln in errs if ln.startswith("확인 필요:")],
              "lines": lines[-40:], "counts": replica_counts(out / "sp_core.sqlite"),
              "elapsed_sec": round(time.monotonic() - started, 1), "at": at,
              "pulled_at": prev.get("pulled_at") if status == "error" else at,
              "last_full_at": at if full and status != "error" else prev.get("last_full_at")}
    if status == "error":
        result["error"] = (errs or lines or [f"받기가 끝나는 값 {rc} 로 멈췄다"])[-1]
    _write_status(root, result)
    return result


def replica_counts(path: Path) -> Dict[str, int]:
    """사본의 타입별 활성 객체 수."""
    if not Path(path).is_file():
        return {}
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        tables = [n for (n,) in con.execute("SELECT name FROM sqlite_master WHERE type='table'") if "__" not in n]
        return {t: con.execute(f'SELECT count(*) FROM "{t}" WHERE deleted=0').fetchone()[0] for t in tables}
    finally:
        con.close()


# ── 읽기 (층 2) — 조회 색인 ──────────────────────────────────────────────
_REV_SUFFIX = re.compile(r"^(.+?)[_\s]+([0-9a-z]{1,4})$", re.IGNORECASE)   # '감마_C2' → '감마' (과제명 뒤 차수 접미어)


def _fold(text: str) -> str:
    return " ".join((text or "").lower().split())


def _strings(value) -> List[str]:
    """한 값·목록·{alias|value|name|text: …} 목록 → 글자 목록. 별칭 봉투의 꼴이 허브마다 달라 넉넉히 받는다."""
    if value is None:
        return []
    items = value if isinstance(value, (list, tuple)) else [value]
    out = []
    for v in items:
        if isinstance(v, dict):
            v = next((v[k] for k in ("alias", "value", "name", "text", "key") if v.get(k)), None)
        if isinstance(v, (str, int)) and str(v).strip():
            out.append(str(v).strip())
    return out


def _read_replica(path: Path) -> Tuple[Dict[str, List[dict]], dict]:
    """키트 사본 → 타입별 행(비활성 포함 — deleted 로 가른다)과 meta. 옛 key 는 `<타입>__previous_keys`(옛 → 지금)."""
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        names = {n for (n,) in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        rows: Dict[str, List[dict]] = {}
        for typ in TYPES:
            if typ not in names:
                continue
            olds: Dict[str, List[str]] = {}
            if f"{typ}__previous_keys" in names:
                for old, key in con.execute(f'SELECT old_key, key FROM "{typ}__previous_keys"'):
                    olds.setdefault(key, []).append(old)
            rows[typ] = [{"key": key, "label": label, "deleted": bool(deleted), "merged_into": merged,
                          "properties": json.loads(props or "{}"), "previous_keys": olds.get(key, [])}
                         for key, label, deleted, merged, props in con.execute(
                             f'SELECT key, label, deleted, merged_into, properties FROM "{typ}"')]
    finally:
        con.close()
    meta: dict = {}
    try:
        cat = json.loads((path.parent / "catalog.json").read_text(encoding="utf-8"))   # 키트가 남기는 카탈로그
        meta.update(system=cat.get("system"), revision=cat.get("revision"))
    except (OSError, ValueError):
        pass
    return rows, meta


class PlmIndex:
    """사본의 조회 색인 — 과제코드·별칭 → by_code, 과제명(옛 이름·차수 접미어를 뗀 개발명) → by_name,
    개발모델명·모델코드·양산모델코드 → by_model. 활성 행만 싣는다.

    허브마다 과제의 key 가 다르다. SP 기본 정의(plm-core)는 key = 과제코드인데, 코어 온톨로지(rootdesign —
    자주검증 Datahub)는 key = **과제명**이고 과제코드는 `properties.task_code` 와 별칭에 있다(cae00 2026-10-27:
    plm_task 5,663 중 task_code 3,263). task_code 가 하나라도 오면 rootdesign 꼴로 보고 key 를 이름 쪽에 둔다(SG-03).
    합쳐진 것(비활성 · merged_into) — 진 쪽의 key · 옛 key · 과제코드 · 별칭은 이긴 쪽을 찾는 옛 이름으로 둔다
    (키트 §2-4 '참조를 이긴 쪽으로' — SignalForge 의 참조는 질의뿐이다).
    """

    def __init__(self, path: Path):
        self.path = Path(path)
        rows, meta = _read_replica(self.path)
        self._build(rows, meta)

    @classmethod
    def from_rows(cls, rows: Dict[str, List[dict]], meta: Optional[dict] = None) -> "PlmIndex":
        """시험·탐침용 — 타입 → 행(key · label · properties · previous_keys · deleted · merged_into)."""
        idx = cls.__new__(cls)
        idx.path = None
        idx._build(rows, meta or {})
        return idx

    def _build(self, rows: Dict[str, List[dict]], meta: dict):
        self.meta = dict(meta)
        live: Dict[str, Dict[str, dict]] = {}
        for typ, items in rows.items():
            for r in items:
                if not r.get("deleted"):
                    live.setdefault(typ, {})[str(r["key"])] = r
        won_olds: Dict[Tuple[str, str], List[str]] = {}
        won_codes: Dict[Tuple[str, str], List[str]] = {}
        for typ, items in rows.items():
            for r in items:
                winner = str(r.get("merged_into") or "")
                if r.get("deleted") and winner in live.get(typ, {}):
                    p = r.get("properties") or {}
                    won_olds.setdefault((typ, winner), []).extend([str(r["key"])] + _strings(r.get("previous_keys")))
                    won_codes.setdefault((typ, winner), []).extend(_strings(p.get("task_code")) + _strings(p.get("aliases")))
        self.tasks: Dict[str, dict] = {}
        self.models: Dict[str, dict] = {}
        self.projects: Dict[str, str] = {}
        self.project_info: Dict[str, dict] = {}
        for typ, by_key in live.items():
            for key, r in by_key.items():
                p = r.get("properties") or {}
                label = r.get("label")
                olds = list(dict.fromkeys(_strings(r.get("previous_keys")) + _strings(r.get("renamed_from"))
                                          + won_olds.get((typ, key), [])))
                # 별칭은 코어가 properties.aliases 에 둔다(StepForge sp_core ALIAS_FIELD 와 같다). 행 봉투의 aliases 도 받는다
                names = list(dict.fromkeys(_strings(r.get("aliases")) + _strings(p.get("aliases"))
                                           + won_codes.get((typ, key), [])))
                if typ == "plm_task":
                    self.tasks[key] = {"key": key, "label": label or "", "project": p.get("project"),
                                       "status": p.get("task_status"), "task_code": _strings(p.get("task_code")),
                                       "previous_keys": olds, "aliases": names, "models": []}
                elif typ == "plm_model":
                    self.models[key] = {"key": key, "label": label or key, "task": p.get("task"),
                                        "model_code": p.get("model_code"), "base_code": p.get("base_code"),
                                        "mass_model_code": p.get("mass_model_code"),
                                        "model_kind": p.get("model_kind"), "product_text": p.get("product_text")}
                elif typ == "plm_project":
                    self.projects[key] = label or key
                    self.project_info[key] = {"key": key, "label": label or key, "aliases": names, "previous_keys": olds}
        # 과제가 가리키는 프로젝트 key 가 옛 key 일 수 있다(프로젝트 개명)
        self._project_by_old: Dict[str, str] = {}
        for pk, pi in self.project_info.items():
            for old in pi["previous_keys"]:
                self._project_by_old.setdefault(str(old), pk)
        # 프로젝트 이름·별칭(마케팅명) → 프로젝트 key — 질의가 어느 프로젝트를 짚었는지 본다(SG-04). 세 글자 미만은 뺀다
        self.project_names: Dict[str, List[str]] = {}
        for pk, pi in self.project_info.items():
            for n in [pi["label"]] + pi["aliases"]:
                f = _fold(str(n))
                if len(f) >= 3 and pk not in self.project_names.setdefault(f, []):
                    self.project_names[f].append(pk)
        for m in self.models.values():
            if m["task"] in self.tasks:
                self.tasks[m["task"]]["models"].append(m["key"])
        self.key_is_name = any(t["task_code"] for t in self.tasks.values())
        self.by_code: Dict[str, str] = {}
        self.by_name: Dict[str, List[str]] = {}
        self.by_model: Dict[str, str] = {}

        def add_name(name: str, key: str):
            name = _fold(name)
            if len(name) < 2:
                return
            if key not in self.by_name.setdefault(name, []):
                self.by_name[name].append(key)
            core = _REV_SUFFIX.match(name)
            if core and len(core.group(1)) >= 2 and key not in self.by_name.setdefault(core.group(1), []):
                self.by_name[core.group(1)].append(key)

        for t in self.tasks.values():
            codes = t["task_code"] + t["aliases"] + ([] if self.key_is_name else [t["key"]] + t["previous_keys"])
            for c in codes:
                self.by_code.setdefault(_fold(str(c)), t["key"])
            for n in [t["label"]] + ([t["key"]] + t["previous_keys"] if self.key_is_name else []):
                add_name(str(n), t["key"])
            code = (t["task_code"] or [t["key"]])[0]
            name = t["label"] or t["key"]
            t["display"] = code if _fold(code) == _fold(name) else f"{code}({name})"
        for m in self.models.values():
            for name in (m["key"], m["label"], m["model_code"], m["mass_model_code"]):
                if name:
                    self.by_model.setdefault(_fold(str(name)), m["key"])
        # 띄어 쓴 과제명은 질의의 낱말 하나로 안 잡힌다 — 질의 해석이 통째로 찾는다(긴 것부터)
        self.multi_names: List[str] = sorted((n for n in self.by_name if " " in n), key=len, reverse=True)

    def __len__(self) -> int:
        return len(self.tasks)

    def project_of(self, task_key: Optional[str]) -> Optional[str]:
        """과제의 프로젝트 key(지금 key 로). 거울에 없는 프로젝트 key 도 그대로 돌려준다 — 다른 프로젝트임은 안다."""
        t = self.tasks.get(task_key) if task_key else None
        ref = t.get("project") if t else None
        if not isinstance(ref, str) or not ref:
            return None
        return ref if ref in self.project_info else self._project_by_old.get(ref, ref)

    def task_models(self, task_key: str) -> List[dict]:
        t = self.tasks.get(task_key)
        return [self.models[k] for k in (t["models"] if t else [])]


_cache: Dict[str, Tuple[float, float, Optional[PlmIndex]]] = {}


def mirror(root: Optional[Path] = None, *, check_every_s: float = 30.0) -> Optional[PlmIndex]:
    """사본 색인(없으면 None). 파일이 바뀌면 다시 연다 — 30초마다만 본다. 받기가 쓰는 도중에 못 열면 옛 색인을 두고
    다음에 다시 본다(바뀐 mtime 을 적어 두지 않는다)."""
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
        log.warning("[plm] 사본을 못 열었다: %r", e)
        _cache[key] = (now, hit[1] if hit else 0.0, hit[2] if hit else None)
        return _cache[key][2]
    _cache[key] = (now, mtime, idx)
    return idx
