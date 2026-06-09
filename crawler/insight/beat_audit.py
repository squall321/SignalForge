"""Beat schedule audit — collector vs 실제 fire 비교.

PLATFORM_MAP (tasks.py) 에 등록되어 있고 platforms DB 에 is_active=True 인
collector 중, celery_app.py 의 beat_schedule 에 ``crawl_platform`` args 로
없는 항목을 *missing schedule* 로 식별한다.

반대로 beat 에는 있는데 PLATFORM_MAP 에 없는 코드는 *orphan* (오타 또는
dispatch 누락) 으로 보고한다.

본 모듈은 *pure function* 만 노출 — DB I/O 와 파일 파싱은 호출부 (CLI /
스크립트) 에서 수행하고, ``audit_beat_schedule`` 는 세 집합을 입력으로 받아
결과 dict 를 돌려준다.  이렇게 두면 단위 테스트가 외부 환경 없이 결정적으로
검증된다.
"""
from __future__ import annotations

from typing import Iterable


def audit_beat_schedule(
    platform_map_keys: Iterable[str],
    beat_schedule_codes: Iterable[str],
    active_db_codes: Iterable[str],
) -> dict:
    """Cross-check 결과.

    Args:
        platform_map_keys: ``crawler/tasks.py::PLATFORM_MAP`` 의 키.
        beat_schedule_codes: ``celery_app.py::beat_schedule`` 에서 추출한
            ``crawl_platform`` task 의 첫 positional arg.
        active_db_codes: ``platforms.code WHERE is_active=true``.

    Returns:
        dict with keys:
        - ``missing_in_beat`` (list[str]):  PLATFORM_MAP + DB 활성인데 beat 미등록
          → 데이터가 *수집 가능* 한데 *fire 되지 않는* 진짜 누락.
        - ``inactive_with_beat`` (list[str]): DB 비활성이지만 beat 에는 등록됨
          → 자원 낭비 또는 의도적 비활성. 운영자 확인 필요.
        - ``orphan_in_beat`` (list[str]): beat 에는 있는데 PLATFORM_MAP 미정의
          → dispatcher 가 KeyError 를 던질 위험.
        - ``orphan_in_map`` (list[str]): PLATFORM_MAP 에는 있는데 DB 등록 없음
          → 코드만 존재, 실 운영 대상 아님.
        - ``healthy`` (list[str]): MAP + beat + DB(active) 3 자 정합.
    """
    pm = set(platform_map_keys)
    bs = set(beat_schedule_codes)
    db_active = set(active_db_codes)

    # 진짜 누락 = MAP 에 있고 DB 활성인데 beat 미등록
    missing_in_beat = sorted((pm & db_active) - bs)
    # 비활성인데 beat 등록 (자원 낭비)
    inactive_with_beat = sorted(bs - db_active)
    # beat 에는 있는데 dispatcher 가 모르는 코드 (KeyError 위험)
    orphan_in_beat = sorted(bs - pm)
    # MAP 에 있는데 DB 미등록
    orphan_in_map = sorted(pm - db_active)
    # 정상
    healthy = sorted(pm & bs & db_active)

    return {
        "missing_in_beat": missing_in_beat,
        "inactive_with_beat": inactive_with_beat,
        "orphan_in_beat": orphan_in_beat,
        "orphan_in_map": orphan_in_map,
        "healthy": healthy,
        "counts": {
            "platform_map": len(pm),
            "beat_schedule": len(bs),
            "db_active": len(db_active),
            "missing_in_beat": len(missing_in_beat),
            "inactive_with_beat": len(inactive_with_beat),
            "orphan_in_beat": len(orphan_in_beat),
            "orphan_in_map": len(orphan_in_map),
            "healthy": len(healthy),
        },
    }
