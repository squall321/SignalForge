"""
Harvest 2 B 트랙 (한국 deep) 후행 정합성 단위 테스트.

검증 (1 케이스, 외부 의존 0):
  - audit JSONL 파싱 → harvest2 round 의 'start' event 가 존재 (baseline 기록 확인)
  - 만일 'end' event 도 존재하면 saved counts 와 deltas 형식이 schema 적합
  - end event 부재 시 'incomplete=True' 플래그를 반환 (drift 신호로 보고)
  - 시간 단조: start.ts < end.ts (있을 때)
  - 사이트 목록: clien/dcinside/ppomppu/fmkorea/dogdrip 모두 포함

본 테스트는 db 접근 없이 audit 파일만 검증 — 실측 psql 카운트와의 비교는
보고 단계에서 수동 비교한다 (단위 테스트 격리 원칙).

실행:
    cd backend && .venv/bin/pytest tests/test_harvest2_b_verify.py -v
"""
from __future__ import annotations

import json
import os
import tempfile
import textwrap


REQUIRED_SITES = {"clien", "dcinside", "ppomppu", "fmkorea", "dogdrip"}


def _scan_audit(path: str) -> dict:
    """audit JSONL 한 파일을 스캔, harvest2 round 의 정합성 요약을 반환."""
    summary = {
        "lines": 0,
        "start_count": 0,
        "end_count": 0,
        "dry_run_count": 0,
        "new_collector_count": 0,
        "first_start": None,
        "last_end": None,
        "sites_seen": set(),
        "incomplete": True,
        "schema_ok": True,
        "errors": [],
    }

    if not os.path.exists(path):
        summary["errors"].append(f"audit path missing: {path}")
        return summary

    with open(path, "r", encoding="utf-8") as f:
        for raw in f:
            raw = raw.strip()
            if not raw:
                continue
            summary["lines"] += 1
            try:
                row = json.loads(raw)
            except json.JSONDecodeError as e:
                summary["schema_ok"] = False
                summary["errors"].append(f"invalid json: {e}")
                continue

            if row.get("round") not in ("harvest2", "harvest2-dry"):
                continue

            ev = row.get("event")
            if ev == "start" and row.get("round") == "harvest2":
                summary["start_count"] += 1
                summary["first_start"] = summary["first_start"] or row.get("ts")
                summary["sites_seen"].update(row.get("sites", []) or [])
            elif ev == "dry_run_end":
                summary["dry_run_count"] += 1
            elif ev == "new_collector":
                summary["new_collector_count"] += 1
            elif ev == "end" and row.get("round") == "harvest2":
                summary["end_count"] += 1
                summary["last_end"] = row.get("ts")
                # schema check on deltas if present
                d = row.get("deltas") or {}
                for site, info in d.items():
                    for key in ("total_before", "total_after", "d_total",
                                "old_before", "old_after", "d_old_90d",
                                "old_ratio_pct", "saved_by_crawler"):
                        if key not in info:
                            summary["schema_ok"] = False
                            summary["errors"].append(
                                f"deltas[{site}] missing {key}"
                            )

    summary["incomplete"] = summary["start_count"] > 0 and summary["end_count"] == 0
    if summary["first_start"] and summary["last_end"]:
        # ISO 형식이므로 lexicographic 비교가 단조와 동치
        if summary["first_start"] >= summary["last_end"]:
            summary["schema_ok"] = False
            summary["errors"].append("ts monotonic violation: start>=end")
    return summary


def _write(path: str, body: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write(textwrap.dedent(body).lstrip())


def test_harvest2_b_audit_integrity():
    """3 가지 시나리오 — 완성/미완성/불량 — 모두 graceful 진단."""
    # ── 시나리오 1: 완성된 audit (start + end + new_collector) ───────────
    with tempfile.TemporaryDirectory() as tmp:
        complete = os.path.join(tmp, "audit_complete.jsonl")
        _write(complete, """
        {"ts":"2026-06-06T07:54:48+00:00","round":"harvest2","event":"start","pages":50,"max_posts":600,"sites":["clien","dcinside","ppomppu","fmkorea","dogdrip"],"dry_run":false,"before":{}}
        {"ts":"2026-06-06T08:10:49+00:00","round":"harvest2","event":"new_collector","track":"C","platforms":[]}
        {"ts":"2026-06-06T08:20:00+00:00","round":"harvest2","event":"end","elapsed_s":1512,"results":{},"after":{},"deltas":{"clien":{"total_before":"8879","total_after":"8882","d_total":3,"old_before":"7763","old_after":"7767","d_old_90d":4,"old_ratio_pct":133.3,"saved_by_crawler":3}}}
        """)
        s = _scan_audit(complete)
        assert s["start_count"] == 1, s
        assert s["end_count"] == 1, s
        assert s["new_collector_count"] == 1, s
        assert s["incomplete"] is False, s
        assert s["schema_ok"] is True, s["errors"]
        assert REQUIRED_SITES.issubset(s["sites_seen"]), s["sites_seen"]

    # ── 시나리오 2: 미완성 (start 만, end 부재) — 실제 harvest2 파일 형태 ─
    with tempfile.TemporaryDirectory() as tmp:
        partial = os.path.join(tmp, "audit_partial.jsonl")
        _write(partial, """
        {"ts":"2026-06-06T07:54:32+00:00","round":"harvest2-dry","event":"start","pages":50,"max_posts":600,"sites":["clien","dcinside","ppomppu","fmkorea","dogdrip"],"dry_run":true,"before":{}}
        {"ts":"2026-06-06T07:54:32+00:00","round":"harvest2-dry","event":"dry_run_end","before":{}}
        {"ts":"2026-06-06T07:54:48+00:00","round":"harvest2","event":"start","pages":50,"max_posts":600,"sites":["clien","dcinside","ppomppu","fmkorea","dogdrip"],"dry_run":false,"before":{}}
        {"ts":"2026-06-06T08:10:49+00:00","round":"harvest2","event":"new_collector","track":"C","platforms":[]}
        """)
        s = _scan_audit(partial)
        assert s["start_count"] == 1, s
        assert s["end_count"] == 0, s
        assert s["dry_run_count"] == 1, s
        assert s["new_collector_count"] == 1, s
        assert s["incomplete"] is True, s  # drift 신호
        assert s["schema_ok"] is True, s["errors"]  # 형식 자체는 정상
        assert REQUIRED_SITES.issubset(s["sites_seen"]), s["sites_seen"]

    # ── 시나리오 3: 불량 JSON ──────────────────────────────────────────
    with tempfile.TemporaryDirectory() as tmp:
        broken = os.path.join(tmp, "audit_broken.jsonl")
        _write(broken, """
        {"ts":"2026-06-06T07:54:48+00:00","round":"harvest2","event":"start","sites":["clien"]}
        this-is-not-json
        """)
        s = _scan_audit(broken)
        assert s["schema_ok"] is False, s
        assert any("invalid json" in e for e in s["errors"]), s["errors"]


if __name__ == "__main__":
    test_harvest2_b_audit_integrity()
    print("OK")
