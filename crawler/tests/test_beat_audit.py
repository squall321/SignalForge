"""R28-harvest 트랙 E — beat schedule audit 단위 테스트.

Discovery (2026-06-06) 에서 식별한 *PLATFORM_MAP 등록 + DB active 인데 beat
미등록* 상태가 ``audit_beat_schedule`` 로 정확히 missing_in_beat 에 잡히는지
확인한다. Discovery 실 데이터:

    missing_in_beat = [gigazine, gsmchoice, hipertextual, inside_handy, ithome,
                       mobile_review, mysmartprice, sammobile, sammyfans]
    orphan_in_beat  = [bluesky]       (bluesky 는 DB 비활성)
    inactive_with_beat = [bluesky]    (beat 에 있지만 DB is_active=false)

본 테스트는 외부 의존성 없이 입력 집합만으로 결과를 검증한다.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from insight.beat_audit import audit_beat_schedule  # noqa: E402


def test_audit_beat_schedule_discovery_replay():
    """Discovery 2026-06-06 실측 입력으로 missing_in_beat 9건 검출."""
    # Discovery 시점 PLATFORM_MAP (축약: 비교에 필요한 항목만)
    platform_map = {
        "reddit", "twitter", "clien", "dcinside",
        # missing 후보 9
        "gigazine", "gsmchoice", "hipertextual", "inside_handy", "ithome",
        "mobile_review", "mysmartprice", "sammobile", "sammyfans",
    }
    # beat 등록 (missing 후보 9 누락 + bluesky 만 추가)
    beat_schedule = {
        "reddit", "twitter", "clien", "dcinside", "bluesky",
    }
    # DB 활성 (bluesky 만 비활성)
    db_active = {
        "reddit", "twitter", "clien", "dcinside",
        "gigazine", "gsmchoice", "hipertextual", "inside_handy", "ithome",
        "mobile_review", "mysmartprice", "sammobile", "sammyfans",
    }

    res = audit_beat_schedule(platform_map, beat_schedule, db_active)

    assert res["missing_in_beat"] == [
        "gigazine", "gsmchoice", "hipertextual", "inside_handy", "ithome",
        "mobile_review", "mysmartprice", "sammobile", "sammyfans",
    ], res["missing_in_beat"]
    # bluesky 는 dispatcher 미등록 (orphan) 이자 DB 비활성 (inactive_with_beat)
    assert res["orphan_in_beat"] == ["bluesky"]
    assert res["inactive_with_beat"] == ["bluesky"]
    # 4 자 정합
    assert res["healthy"] == ["clien", "dcinside", "reddit", "twitter"]
    # 카운트 무결성
    assert res["counts"]["missing_in_beat"] == 9
    assert res["counts"]["platform_map"] == 13
    assert res["counts"]["db_active"] == 13
    assert res["counts"]["beat_schedule"] == 5
