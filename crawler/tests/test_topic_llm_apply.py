"""topic_llm_apply — Track A R21 단위 테스트.

DB/LLM 미사용. 순수함수 검증:
  1. merge_preserve() — PRESERVE_EXISTING 규칙
  2. summarize()      — pair_counts / agree / expand 카운트
  3. write_report()   — 보고서 생성 (드라이런)
  4. write_audit()    — backfill_audit.jsonl 호환 라인 1개

추가: parse_llm_label() smoke (재사용 검증).
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from scripts.topic_eval import parse_llm_label
from scripts.topic_llm_apply import (
    merge_preserve,
    summarize,
    write_audit,
    write_report,
)


# ---------------------------------------------------------------------------
# 1. merge_preserve
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "db_topics, llm_label, expected",
    [
        # 기존 라벨 유지 — llm 동일
        (["positive_general"], "positive_general", ["positive_general"]),
        # llm 'other' — 변경 없음
        (["comparison"], "other", ["comparison"]),
        # llm 새 라벨 — 확장
        (["comparison"], "experience", ["comparison", "experience"]),
        # llm 빈 응답 — 변경 없음
        (["experience"], "", ["experience"]),
        # 이미 multi-label, llm 가 그 중 하나 — 변경 없음
        (["positive_general", "comparison"], "comparison", ["positive_general", "comparison"]),
        # 이미 multi-label, llm 새 라벨 — 확장
        (
            ["positive_general", "comparison"],
            "experience",
            ["positive_general", "comparison", "experience"],
        ),
    ],
)
def test_merge_preserve(db_topics, llm_label, expected):
    assert merge_preserve(db_topics, llm_label) == expected


def test_merge_preserve_does_not_mutate_input():
    db = ["comparison"]
    out = merge_preserve(db, "experience")
    assert db == ["comparison"]  # 원본 보존
    assert out == ["comparison", "experience"]


# ---------------------------------------------------------------------------
# 2. summarize
# ---------------------------------------------------------------------------
def test_summarize_basic():
    rows = [
        # agree
        {"db_primary": "positive_general", "llm_label": "positive_general"},
        {"db_primary": "comparison", "llm_label": "comparison"},
        # expand
        {"db_primary": "experience", "llm_label": "comparison"},
        # other (제외 — 확장 불가)
        {"db_primary": "negative_general", "llm_label": "other"},
    ]
    s = summarize(rows)
    assert s["total"] == 4
    assert s["agree"] == 2
    assert s["agree_rate"] == 0.5
    assert s["expand_candidates"] == 1
    assert s["other_n"] == 1
    assert s["pair_counts"]["experience"]["comparison"] == 1


def test_summarize_empty():
    s = summarize([])
    assert s["total"] == 0
    assert s["agree_rate"] == 0.0
    assert s["expand_candidates"] == 0


# ---------------------------------------------------------------------------
# 3. write_report
# ---------------------------------------------------------------------------
def test_write_report_creates_files(tmp_path):
    out_md = str(tmp_path / "r.md")
    out_json = str(tmp_path / "r.json")
    rows = [
        {
            "id": 1,
            "db_topics": ["comparison"],
            "db_primary": "comparison",
            "llm_label": "experience",
            "content": "S25 쓰다가 S24로 돌아왔어요. 배터리 차이 큼.",
        }
    ]
    s = summarize(rows)
    write_report(out_md, out_json, rows, s, applied_n=0)
    assert os.path.exists(out_md)
    assert os.path.exists(out_json)

    payload = json.loads(open(out_json, encoding="utf-8").read())
    assert payload["summary"]["total"] == 1
    assert payload["rows"][0]["merged"] == ["comparison", "experience"]


# ---------------------------------------------------------------------------
# 4. write_audit
# ---------------------------------------------------------------------------
def test_write_audit_appends_line(tmp_path):
    audit = tmp_path / "audit.jsonl"
    with patch("scripts.topic_llm_apply.AUDIT_PATH", str(audit)):
        write_audit(
            run_id="testrun123",
            started_at=datetime(2026, 6, 5, 0, 0, tzinfo=timezone.utc),
            finished_at=datetime(2026, 6, 5, 0, 1, tzinfo=timezone.utc),
            status="ok",
            summary={"total": 10, "agree": 5, "agree_rate": 0.5, "expand_candidates": 3},
            applied_n=0,
        )
    line = open(str(audit), encoding="utf-8").read().strip()
    entry = json.loads(line)
    assert entry["run_id"] == "testrun123"
    assert entry["script"] == "topic_llm_apply"
    assert entry["counters"]["expand_candidates"] == 3
    assert entry["env"]["PRESERVE_EXISTING"] is True


# ---------------------------------------------------------------------------
# 5. parse_llm_label smoke (재사용 검증)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "raw, expected",
    [
        ("positive_general", "positive_general"),
        ("답: comparison", "comparison"),
        ("OTHER", "other"),
        ("", "other"),
        ("Experience.", "experience"),
    ],
)
def test_parse_llm_label_smoke(raw, expected):
    assert parse_llm_label(raw) == expected


# ---------------------------------------------------------------------------
# 6. mock LLM 통합 — apply 흐름이 sample/llm/summarize 를 mock 으로 검증
# ---------------------------------------------------------------------------
def test_apply_flow_with_mock_llm(tmp_path, monkeypatch):
    """call_llm 을 mock 으로 교체해 summarize 까지 흐름 확인 (DB/네트워크 미사용)."""
    from scripts import topic_llm_apply as mod

    # 가짜 row 4개
    rows = [
        {"id": 1, "db_topics": ["comparison"], "db_primary": "comparison",
         "content": "비교 글입니다."},
        {"id": 2, "db_topics": ["experience"], "db_primary": "experience",
         "content": "쓴 후기입니다."},
        {"id": 3, "db_topics": ["positive_general"], "db_primary": "positive_general",
         "content": "좋아요!"},
        {"id": 4, "db_topics": ["negative_general"], "db_primary": "negative_general",
         "content": "별로네요."},
    ]
    # LLM mock — 첫 두건은 agree, 셋째는 expand (experience), 넷째는 other
    answers = iter(["comparison", "experience", "experience", "other"])

    def fake_call_llm(client, content, model):  # noqa: ARG001
        return next(answers)

    monkeypatch.setattr(mod, "call_llm", fake_call_llm)

    # parse_llm_label 은 그대로 사용
    for r in rows:
        raw = fake_call_llm(None, r["content"], "x")
        r["llm_raw"] = raw
        r["llm_label"] = parse_llm_label(raw)

    s = summarize(rows)
    assert s["total"] == 4
    assert s["agree"] == 2  # 1,2
    assert s["expand_candidates"] == 1  # 3
    assert s["other_n"] == 1  # 4
