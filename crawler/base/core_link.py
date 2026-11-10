# SignalForge 부품 · 증상을 코어 part · failure_mode 에 잇는 대응표를 짓는다 — 정확 일치만 rule 선, 짐작은 후보, 사람 선은 지킨다
"""코어와 잇기(Q-2 — 사용자 결정 2026-11-10, 11-04 '코어와 잇지 않는다' 를 뒤집었다).

대응표 둘(포털 core-link design §9) — VOC 부품 16 → 코어 `part` key, 증상 32 → 코어 `failure_mode` key.

- 입력 — PLM 거울 사본의 CORE_TYPES(plm_mirror._read_replica) · SignalForge 어휘(shared/ontology 의 COMPONENT_LABELS ·
  SYMPTOM_LABELS — key · 한국어 · 영어) · 지금 대응표.
- rule 선 — SignalForge 개념의 이름 가운데 하나가 코어 객체의 이름(key · label · 별칭)과 정확히 같고, 그렇게 걸리는 코어 객체가
  **하나뿐**일 때만 잇는다. 둘 이상이면 후보로, 하나도 없으면 비슷한 이름을 후보로만 낸다 — 짐작은 잇지 않는다.
- 사람 선 — cae00 운영 사용자가 확인해 넣은 줄(origin=human). 코어에 맞는 것이 없다고 정한 줄은 core_key 없이 까닭만 둔다.
  다시 지을 때 rule 선만 통째로 바꾸고 사람 선은 그대로 둔다(키트 01-AI-GUIDE §6.2 '표시가 있는 연결만 끊는다' 와 같은 꼴).
- 바뀔 때(사용자 결정 2026-11-10 — 키트 기본 제안) — 사람 선의 코어 key 가 바뀌었으면(옛 key) 새 key 로 따라가고, 사라졌으면
  '사용 안 함'(unused), 합쳐졌으면 '확인 필요'(check — 이긴 쪽 key 를 merged_into 에)로 둔다. 확인 필요는 review.json 에 모아
  포털 받은 편지함으로 보낸다. rule 선은 다시 지으면 저절로 지금 코어를 따른다.

보관 — `crawler/data/plm/core-link/`. 코어 key · 이름은 허브 데이터라 공개 리포 밖에 둔다(사용자 결정 2026-11-10). VOC DB 밖이라
Drive 백업 · cae00 덤프에도 안 실린다(PLM 거울과 같은 자리 · 같은 까닭). map.json(대응표) · review.json(후보 · 확인 필요).
"""
from __future__ import annotations

import difflib
import json
import os
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from base import plm_mirror as _pm

PAIRS = (("part", "part"), ("symptom", "failure_mode"))       # (SignalForge 종류, 코어 타입)
# 코어 객체의 별칭 칸 — 칸 이름은 허브 정의를 따른다. 실물 카탈로그(cae00 받은 편지함 Q6)가 오면 맞춘다.
ALIAS_FIELDS = ("aliases", "alias", "synonyms", "name_en", "name_ko")
DIR = "core-link"
MAP = "map.json"
REVIEW = "review.json"
MAX_OPTIONS = 3
CLOSE = 0.6
_SEP = re.compile(r"[\s_\-·/]+")
_PARTS = re.compile(r"[·/]")


def _norm(text) -> str:
    """비교용 이름 — 대소문자 · 띄어쓰기 · 밑줄 · 하이픈 · 가운뎃점을 맞춘다('charging_port' = 'Charging port')."""
    return _SEP.sub(" ", str(text or "")).strip().casefold()


def _names(*labels: str) -> List[str]:
    """한 개념의 이름들 — 가운뎃점으로 묶은 이름('발화·폭발')은 낱말로도 넣는다. 같은 이름은 한 번만."""
    out, seen = [], set()
    for label in labels:
        for one in [label] + ([p for p in _PARTS.split(label)] if _PARTS.search(label or "") else []):
            if _norm(one) and _norm(one) not in seen:
                seen.add(_norm(one))
                out.append(one.strip())
    return out


def sf_vocab() -> Dict[str, Dict[str, List[str]]]:
    """SignalForge 부품 · 증상 → {종류: {key: 이름들}}. shared/ontology 가 정본이다 — 못 읽으면 ImportError 로 멈춘다
    (조용히 key 만으로 이으면 한국어 · 영어 이름이 빠진 채 정상처럼 보인다)."""
    for p in ("/shared", str(Path(__file__).resolve().parents[2] / "shared")):
        if Path(p).is_dir() and p not in sys.path:
            sys.path.append(p)
    import ontology as O
    return {kind: {key: _names(key, ko, en) for key, (ko, en) in labels.items()}
            for kind, labels in (("part", O.COMPONENT_LABELS), ("symptom", O.SYMPTOM_LABELS))}


def _core_names(row: dict) -> List[str]:
    props = row.get("properties") or {}
    names = [row.get("key"), row.get("label")]
    for field in ALIAS_FIELDS:
        names += _pm._strings(props.get(field))
    return [n for n in names if n]


def _options(hits: Dict[str, str], names: List[str], by_name: Dict[str, set], active: Dict[str, dict]) -> List[dict]:
    """후보 — 여럿 걸렸으면 그것들, 하나도 없으면 이름이 가까운 코어 객체(사람이 고를 거리)."""
    if hits:
        return [{"core_key": k, "label": active[k].get("label"), "matched_by": hits[k]} for k in sorted(hits)]
    scored: Dict[str, float] = {}
    for name in names:
        for close in difflib.get_close_matches(_norm(name), list(by_name), n=MAX_OPTIONS * 2, cutoff=CLOSE):
            score = difflib.SequenceMatcher(None, _norm(name), close).ratio()
            for key in by_name[close]:
                scored[key] = max(scored.get(key, 0.0), score)
    best = sorted(scored.items(), key=lambda kv: (-kv[1], kv[0]))[:MAX_OPTIONS]
    return [{"core_key": k, "label": active[k].get("label"), "score": round(s, 2)} for k, s in best]


def _human_state(row: dict, active: Dict[str, dict], gone: Dict[str, dict], renamed: Dict[str, str]) -> dict:
    """사람 선 한 줄을 지금 코어에 맞춰 본다 — 지우지 않는다. 바뀐 key 는 따라가고, 사라지면 unused, 합쳐지면 check."""
    out = dict(row)
    out.pop("merged_into", None)
    key = row.get("core_key")
    if key is None or key in active:
        out["status"] = "active"
    elif key in renamed:
        out.update(core_key=renamed[key], status="active", renamed_from=key)
    elif key in gone and gone[key].get("merged_into"):
        out.update(status="check", merged_into=gone[key]["merged_into"])
    else:
        out["status"] = "unused"
    return out


def build(sf: Dict[str, Dict[str, List[str]]], core: Dict[str, List[dict]], current: List[dict],
          now: Optional[str] = None) -> Tuple[List[dict], dict]:
    """(대응표 줄, 검토 거리). current 의 rule 선은 버리고 다시 짓는다. 사람 선은 지금 코어에 맞춰 상태만 고친다."""
    now = now or datetime.now(timezone.utc).isoformat(timespec="seconds")
    human = {(r["kind"], r["sf_key"]): r for r in current if r.get("origin") == "human"}
    rows: List[dict] = []
    candidates: List[dict] = []
    for kind, ctype in PAIRS:
        objs = core.get(ctype, [])
        active = {r["key"]: r for r in objs if not r.get("deleted")}
        gone = {r["key"]: r for r in objs if r.get("deleted")}
        renamed = {old: r["key"] for r in active.values() for old in r.get("previous_keys") or []}
        by_name: Dict[str, set] = defaultdict(set)
        for r in active.values():
            for name in _core_names(r):
                by_name[_norm(name)].add(r["key"])
        for sf_key, names in sf.get(kind, {}).items():
            if (kind, sf_key) in human:
                rows.append(_human_state(human[(kind, sf_key)], active, gone, renamed))
                continue
            hits: Dict[str, str] = {}
            for name in names:
                for key in by_name.get(_norm(name), ()):
                    hits.setdefault(key, name)
            if len(hits) == 1:
                (key, matched), = hits.items()
                rows.append({"kind": kind, "sf_key": sf_key, "core_type": ctype, "core_key": key, "origin": "rule",
                             "status": "active", "matched_by": matched, "at": now})
            else:
                candidates.append({"kind": kind, "sf_key": sf_key, "core_type": ctype, "sf_names": names,
                                   "why": "ambiguous" if hits else "no_exact",
                                   "options": _options(hits, names, by_name, active)})
    # SignalForge 쪽 개념이 사라진 사람 선 — 지우지 않고 '사용 안 함' 으로 남긴다(사람이 정한 것은 사람이 거둔다)
    for (kind, sf_key), row in human.items():
        if sf_key not in sf.get(kind, {}):
            rows.append({**row, "status": "unused", "note": "SignalForge 어휘에서 사라진 개념"})
    rows.sort(key=lambda r: (r["kind"], r["sf_key"]))
    counts = {"rule": sum(r["origin"] == "rule" for r in rows),
              "human": sum(r["origin"] == "human" for r in rows),
              "human_none": sum(r["origin"] == "human" and r.get("core_key") is None for r in rows),
              "unused": sum(r["status"] == "unused" for r in rows),
              "check": sum(r["status"] == "check" for r in rows),
              "candidates": len(candidates),
              "sf": {kind: len(sf.get(kind, {})) for kind, _ in PAIRS},
              "core": {ctype: sum(not r.get("deleted") for r in core.get(ctype, [])) for _, ctype in PAIRS}}
    review = {"generated_at": now, "counts": counts, "candidates": candidates,
              "check": [r for r in rows if r["status"] == "check"],
              "unused": [r for r in rows if r["status"] == "unused"]}
    return rows, review


def set_human(rows: List[dict], kind: str, sf_key: str, core_key: Optional[str], reason: Optional[str] = None,
              now: Optional[str] = None) -> List[dict]:
    """사람 선을 넣거나 바꾼다 — core_key 가 None 이면 '코어에 맞는 것이 없다'(까닭 필수). 같은 개념의 rule 선은 뺀다."""
    ctype = dict(PAIRS).get(kind)
    if ctype is None:
        raise ValueError(f"종류는 {', '.join(k for k, _ in PAIRS)} 중 하나다: {kind}")
    if core_key is None and not (reason or "").strip():
        raise ValueError("맞는 것이 없다고 정할 때는 까닭을 단다")
    row = {"kind": kind, "sf_key": sf_key, "core_type": ctype, "core_key": core_key, "origin": "human", "status": "active",
           "at": now or datetime.now(timezone.utc).isoformat(timespec="seconds")}
    if reason:
        row["reason"] = reason.strip()
    return [r for r in rows if (r["kind"], r["sf_key"]) != (kind, sf_key)] + [row]


def unset_human(rows: List[dict], kind: str, sf_key: str) -> List[dict]:
    """사람 선을 거둔다 — 다음 짓기에서 그 개념은 rule 선이나 후보로 돌아간다."""
    return [r for r in rows if not (r.get("origin") == "human" and (r["kind"], r["sf_key"]) == (kind, sf_key))]


# ── 파일 ────────────────────────────────────────────────────────────────
def link_dir(root: Optional[Path] = None) -> Path:
    return Path(root or _pm.default_root()) / DIR


def load_map(root: Optional[Path] = None) -> List[dict]:
    try:
        return json.loads((link_dir(root) / MAP).read_text(encoding="utf-8")).get("rows", [])
    except (OSError, ValueError):
        return []


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def save_map(rows: List[dict], root: Optional[Path] = None, now: Optional[str] = None) -> None:
    _write(link_dir(root) / MAP, {"generated_at": now or datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                  "pairs": [list(p) for p in PAIRS], "rows": rows})


def after_sync(result: dict, root: Optional[Path] = None) -> Optional[dict]:
    """받기 뒤 대응표 다시 짓기 — 받기가 끝까지 돈 때(ok · check)만. 실패해도 받기 결과는 그대로 두고 까닭만 싣는다
    (shared/ontology 를 못 읽음 — /shared 바인드 누락 등. 조용히 넘어가면 대응표가 낡은 채 정상처럼 보인다)."""
    if result.get("status") not in ("ok", "check"):
        return None
    try:
        return rebuild(root)
    except Exception as exc:
        return {"status": "error", "error": f"{type(exc).__name__}: {str(exc)[:200]}"}


def rebuild(root: Optional[Path] = None, sf: Optional[Dict[str, Dict[str, List[str]]]] = None) -> dict:
    """사본을 읽어 대응표를 다시 짓고 map.json · review.json 을 쓴다. 사본에 코어 타입이 없으면 쓰지 않고 그렇다고만 말한다."""
    root = Path(root or _pm.default_root())
    replica = root / _pm.MIRROR
    if not replica.is_file():
        return {"status": "no_replica"}
    core, _ = _pm._read_replica(replica, _pm.CORE_TYPES)
    if not core:
        return {"status": "no_core_types", "note": "사본에 part · failure_mode 가 없다 — 받을 타입 · 토큰 범위를 본다"}
    rows, review = build(sf if sf is not None else sf_vocab(), core, load_map(root))
    save_map(rows, root, review["generated_at"])
    _write(link_dir(root) / REVIEW, review)
    return {"status": "check" if review["check"] else "ok", **review["counts"]}
