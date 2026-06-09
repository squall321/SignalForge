"""R23 트랙 E — crisis_platform_direct audit env 표준키 단위 테스트.

목적
----
backfill_audit JSONL 에 emit 되는 env dict 가 표준키
(``DRY_RUN`` / ``PRESERVE_EXISTING`` / ``BACKUP_BEFORE``) 와 도구별 키
(``CPD_*``) 를 *모두* 포함하는지, 그리고 ``backfill_audit_monitor`` 가
이 env 를 보고 critical alert 를 발생시키지 않는지 검증.

검증 대상 변경
~~~~~~~~~~~~~~
``crisis_platform_direct.py`` 의 ``record_run(env=...)`` 호출 dict 가
표준키 + 도구별 키 둘 다 포함하도록 수정 (R22 E partial → R23 E 완료).

테스트 방법
~~~~~~~~~~~
1. 소스 파일을 텍스트로 읽어 env 리터럴 dict 영역에 표준키 3개가 모두
   존재하는지 정적 확인 (런타임 import 비용·DB 의존성 회피).
2. backfill_audit_monitor._check_rules 에 동일 형태의 run dict 를 주입,
   critical alert 가 0 건인지 확인.

실행
~~~~
::

    cd crawler && python -m pytest tests/test_cpd_env.py -v
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

_CRAWLER_DIR = Path(__file__).resolve().parent.parent
if str(_CRAWLER_DIR) not in sys.path:
    sys.path.insert(0, str(_CRAWLER_DIR))

from insight.backfill_audit_monitor import _check_rules  # noqa: E402


_CPD_SRC = _CRAWLER_DIR / "scripts" / "crisis_platform_direct.py"


def _read_env_block() -> str:
    """``record_run(... env={ ... })`` 블록을 source 에서 추출."""
    text = _CPD_SRC.read_text(encoding="utf-8")
    # record_run 호출 ~ ") as audit:" 까지가 env 정의 영역.
    m = re.search(
        r"with record_run\(.*?\) as audit:",
        text,
        flags=re.DOTALL,
    )
    assert m, "record_run(...) as audit: 블록을 찾지 못함"
    return m.group(0)


def test_cpd_env_emits_standard_and_legacy_keys():
    """env dict 가 표준키 3개 + 도구별 키 5개 모두 포함."""
    block = _read_env_block()

    # 표준키 — backfill_audit_monitor 가 인식하는 안전상태 키.
    for key in ('"DRY_RUN"', '"PRESERVE_EXISTING"', '"BACKUP_BEFORE"'):
        assert key in block, f"표준키 누락: {key}"

    # 도구별 키 — 기존 호환성 (CPD_*).
    for key in (
        '"CPD_DRY_RUN"',
        '"CPD_PRESERVE_EXISTING"',
        '"CPD_PER_KEYWORD_MAX"',
        '"CPD_MAX_PAGES"',
        '"CPD_PLATFORM"',
    ):
        assert key in block, f"도구별 키 누락: {key}"


def test_cpd_env_monitor_no_critical_alert(monkeypatch):
    """audit_monitor._check_rules — 표준키 시나리오에서 critical 0건.

    DRY_RUN=False / PRESERVE_EXISTING=True / BACKUP_BEFORE=True / status=ok
    의 일반 본런 시나리오를 시뮬레이션.  INSERT_ONLY 면제 (기본값) 와
    표준키 양쪽으로 보호되므로 critical 가 0 이어야 함.
    """
    # 면제 리스트를 비워서 — 표준키 자체가 보호한다는 사실을 확인.
    monkeypatch.setenv("AUDIT_INSERT_ONLY_SCRIPTS", "")
    monkeypatch.setenv("AUDIT_PRESERVE_EXEMPT_SCRIPTS", "")

    run = {
        "run_id": "cpd-r23-e-test",
        "script": "crisis_platform_direct",
        "mode": "preserve",
        "status": "ok",
        "env": {
            "DRY_RUN": False,
            "PRESERVE_EXISTING": True,
            "BACKUP_BEFORE": True,
            "CPD_DRY_RUN": 0,
            "CPD_PRESERVE_EXISTING": 1,
            "CPD_PER_KEYWORD_MAX": 5,
            "CPD_MAX_PAGES": 2,
            "CPD_PLATFORM": "9to5google",
        },
    }

    violations = _check_rules(run)
    criticals = [v for v in violations if v["risk"] == "critical"]
    assert not criticals, f"표준키 본런에 critical 위반 발생: {criticals}"


if __name__ == "__main__":
    test_cpd_env_emits_standard_and_legacy_keys()

    # monkeypatch 가 없는 standalone 실행 — env 직접 setenv/unsetenv.
    os.environ["AUDIT_INSERT_ONLY_SCRIPTS"] = ""
    os.environ["AUDIT_PRESERVE_EXEMPT_SCRIPTS"] = ""

    run = {
        "run_id": "cpd-r23-e-test",
        "script": "crisis_platform_direct",
        "mode": "preserve",
        "status": "ok",
        "env": {
            "DRY_RUN": False,
            "PRESERVE_EXISTING": True,
            "BACKUP_BEFORE": True,
            "CPD_DRY_RUN": 0,
            "CPD_PRESERVE_EXISTING": 1,
            "CPD_PER_KEYWORD_MAX": 5,
            "CPD_MAX_PAGES": 2,
            "CPD_PLATFORM": "9to5google",
        },
    }
    violations = _check_rules(run)
    criticals = [v for v in violations if v["risk"] == "critical"]
    assert not criticals, criticals
    print("OK")
