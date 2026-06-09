"""Harvest 3 P3 — Hardware.fr 보드 확장 단위 테스트.

검증 포인트
  1. CATEGORY_PATHS 가 3개 → 6개로 확장됨 (모바일/태블릿/액세서리 + 시계 + 일반).
  2. LIST_PAGES 가 env HARDWARE_FR_BACKFILL_PAGES 로 오버라이드 가능 (기본 5).
  3. GALAXY_KEYWORDS 에 'z fold' / 'z flip' / 'tab s' / 'tablette samsung' 추가됨.
  4. _extract_galaxy_threads 가 'tab s9' 같은 새 키워드도 잡아냄.
  5. record_run 컨텍스트로 audit 한 줄에 round=harvest3p 가 들어감 (smoke).
"""
import importlib
import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# --- 카테고리/페이지 환경 확장 ----------------------------------------------

def test_category_paths_expanded():
    from platforms import hardware_fr

    importlib.reload(hardware_fr)
    paths = hardware_fr.CATEGORY_PATHS
    assert len(paths) >= 6, f"카테고리 6개 이상 기대, 실제 {len(paths)}"
    # 새로 추가된 핵심 슬러그 확인
    joined = "|".join(paths)
    assert "montres-connectees" in joined
    assert "accessoires-smartphones" in joined
    assert "general-mobilite" in joined


def test_list_pages_env_override(monkeypatch):
    monkeypatch.setenv("HARDWARE_FR_BACKFILL_PAGES", "7")
    from platforms import hardware_fr

    importlib.reload(hardware_fr)
    try:
        assert hardware_fr.LIST_PAGES == 7
    finally:
        monkeypatch.delenv("HARDWARE_FR_BACKFILL_PAGES", raising=False)
        importlib.reload(hardware_fr)


def test_list_pages_default_is_five(monkeypatch):
    monkeypatch.delenv("HARDWARE_FR_BACKFILL_PAGES", raising=False)
    from platforms import hardware_fr

    importlib.reload(hardware_fr)
    assert hardware_fr.LIST_PAGES == 5


# --- 키워드 확장 -------------------------------------------------------------

def test_galaxy_keywords_includes_z_fold_z_flip_tab_s():
    from platforms import hardware_fr

    importlib.reload(hardware_fr)
    kws = set(hardware_fr.GALAXY_KEYWORDS)
    assert "z fold" in kws
    assert "z flip" in kws
    assert "tab s" in kws
    assert "tablette samsung" in kws


def test_extract_threads_picks_up_z_fold_thread():
    """확장된 키워드 'z fold' 가 'Z Fold 6' 같은 새 표기 thread 를 잡아냄."""
    from platforms.hardware_fr import HardwareFRCrawler

    # 두 행 — 첫 행은 'Z Fold 6' (Samsung 모델, 통과해야), 두번째는 OnePlus (배제)
    sample = """
    <table>
    <tr><td>Z Fold 6 - retour utilisateurs
    <a href="/hfr/gsmgpspda/telephone-android/z-fold-6-sujet_42000_2.htm">2</a>
    </td></tr>
    <tr><td>OnePlus 12 [Discussion]
    <a href="/hfr/gsmgpspda/telephone-android/oneplus-12-sujet_42001_1.htm">1</a>
    </td></tr>
    </table>
    """
    threads = HardwareFRCrawler._extract_galaxy_threads(sample)
    topic_ids = {t[1] for t in threads}
    assert "42000" in topic_ids  # Z Fold 6 — 통과
    assert "42001" not in topic_ids  # OnePlus — 배제


# --- audit 라운드 라벨 (smoke) ------------------------------------------------

def test_record_run_writes_round_harvest3p(monkeypatch):
    """harvest3p 스크립트의 record_run 호출이 jsonl 한 줄에 round 라벨을 박는지 확인.

    실제 크롤은 하지 않고, record_run context 만 with 블록으로 사용해 한 줄을
    쓰고, 그 줄에 round=harvest3p 가 들어가는지만 본다.  Harvest 2/3 verify D 에서
    *end event 부재* 가 발생했던 회귀의 단위 가드.
    """
    with tempfile.TemporaryDirectory() as td:
        monkeypatch.setenv("BACKFILL_AUDIT_DIR", td)
        # 의존 reload — _audit_path 가 매 호출 환경변수 읽음
        from insight import backfill_audit

        importlib.reload(backfill_audit)
        with backfill_audit.record_run(
            script="harvest3p_hardware_fr",
            mode="dry_run",
            env={
                "DRY_RUN": True,
                "round": "harvest3p",
                "HARDWARE_FR_BACKFILL_PAGES": 5,
            },
        ) as au:
            au.bump("inserted", 0)
            au.note("smoke")

        log_path = Path(td) / "backfill_audit.jsonl"
        assert log_path.exists(), "audit jsonl 파일이 생성되어야 함"
        lines = [json.loads(x) for x in log_path.read_text().splitlines() if x.strip()]
        assert len(lines) == 1
        row = lines[0]
        assert row["script"] == "harvest3p_hardware_fr"
        assert row["env"]["round"] == "harvest3p"
        # 시작/종료 시각이 모두 있어야 verify D 의 end event 부재 회귀를 막음
        assert row.get("started_at")
        assert row.get("finished_at")
        assert row["status"] == "ok"
