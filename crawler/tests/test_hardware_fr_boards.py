"""Harvest 4 H6 — Hardware.fr 추가 보드 확장 단위 테스트.

검증 포인트
  1. CATEGORY_PATHS 가 6 → 8개 이상으로 확장됨 (Smartphones/Tablets 추가 보드).
  2. 신규 보드 슬러그 'android' / 'telephone' 가 CATEGORY_PATHS 에 포함.
  3. 갤럭시 키워드 필터 유지 — 비-삼성 스레드는 신규 보드에서도 배제됨.
  4. record_run 컨텍스트의 round=harvest4 라벨이 audit jsonl 한 줄에 정확히 박힘
     (verify D 의 end event 부재 회귀 가드).
"""
import importlib
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# --- 보드 확장 ---------------------------------------------------------------

def test_h6_category_paths_extended():
    from platforms import hardware_fr

    importlib.reload(hardware_fr)
    paths = hardware_fr.CATEGORY_PATHS
    assert len(paths) >= 8, f"H6: 8개 이상 보드 기대, 실제 {len(paths)}"


def test_h6_includes_android_and_telephone_boards():
    """신규 Smartphones/Tablets 보드 슬러그가 포함되어야 함."""
    from platforms import hardware_fr

    importlib.reload(hardware_fr)
    joined = "|".join(hardware_fr.CATEGORY_PATHS)
    assert "/hfr/gsmgpspda/android/" in joined, \
        "H6: 'android' 보드 누락 — Samsung Galaxy 일반 토픽 손실"
    assert "/hfr/gsmgpspda/telephone/" in joined, \
        "H6: 'telephone' 보드 누락 — 일반 전화기 카테고리 손실"


def test_h6_gps_pda_excluded():
    """실측 결과 galaxy_hits=0 — GPS-PDA 는 의도적으로 제외."""
    from platforms import hardware_fr

    importlib.reload(hardware_fr)
    joined = "|".join(hardware_fr.CATEGORY_PATHS)
    assert "/hfr/gsmgpspda/GPS-PDA/" not in joined, \
        "H6: GPS-PDA 는 Samsung 콘텐츠 0건으로 제외 — 오탐 방지"


# --- 키워드 필터 (신규 보드 환경에서도 동일하게 작동) -----------------------

def test_h6_keyword_filter_still_blocks_non_samsung_in_new_boards():
    """android/telephone 보드 listing 에서 Pixel/OnePlus 같은 비-삼성 스레드는 배제."""
    from platforms.hardware_fr import HardwareFRCrawler

    sample = """
    <table>
    <tr><td>Galaxy S26 Ultra discussion
    <a href="/hfr/gsmgpspda/android/galaxy-s26u-sujet_99001_1.htm">1</a>
    </td></tr>
    <tr><td>Pixel 10 Pro [TU]
    <a href="/hfr/gsmgpspda/android/pixel-10-sujet_99002_1.htm">1</a>
    </td></tr>
    <tr><td>OnePlus 13 retours
    <a href="/hfr/gsmgpspda/telephone/oneplus-13-sujet_99003_1.htm">1</a>
    </td></tr>
    </table>
    """
    threads = HardwareFRCrawler._extract_galaxy_threads(sample)
    topic_ids = {t[1] for t in threads}
    assert "99001" in topic_ids, "Galaxy S26U 통과 실패"
    assert "99002" not in topic_ids, "Pixel 비-삼성 — 배제되어야 함"
    assert "99003" not in topic_ids, "OnePlus 비-삼성 — 배제되어야 함"


# --- audit 라운드 라벨 (smoke) ----------------------------------------------

def test_h6_record_run_writes_round_harvest4(monkeypatch):
    """harvest4 H6 의 record_run 호출이 jsonl 한 줄에 round=harvest4 라벨을 박는지."""
    with tempfile.TemporaryDirectory() as td:
        monkeypatch.setenv("BACKFILL_AUDIT_DIR", td)
        from insight import backfill_audit

        importlib.reload(backfill_audit)
        with backfill_audit.record_run(
            script="harvest4_hardware_fr_boards",
            mode="dry_run",
            env={
                "DRY_RUN": True,
                "round": "harvest4",
                "track": "H6",
                "HARDWARE_FR_BACKFILL_PAGES": 3,
            },
        ) as au:
            au.bump("inserted", 0)
            au.note("h6 smoke")

        log_path = Path(td) / "backfill_audit.jsonl"
        assert log_path.exists()
        lines = [json.loads(x) for x in log_path.read_text().splitlines() if x.strip()]
        assert len(lines) == 1
        row = lines[0]
        assert row["script"] == "harvest4_hardware_fr_boards"
        assert row["env"]["round"] == "harvest4"
        assert row["env"]["track"] == "H6"
        # verify D 의 end event 부재 회귀 가드
        assert row.get("started_at")
        assert row.get("finished_at")
        assert row["status"] == "ok"
