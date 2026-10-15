# 백업 검증이 컨테이너에서 '구조적으로 불가'한 상태로 되돌아가지 않는지 고정한다.
"""막는 고장(2026-10-15 감사).

tasks.verify_backup 이 scripts/drive-sync/verify-backup.sh 를 subprocess 로
실행하려 했다. 컨테이너 안에서 repo_root 가 '/' 로 계산돼
/scripts/drive-sync/verify-backup.sh 를 찾다 script_missing 을 반환하고 끝났다 —
알림 INSERT 경로에 **도달조차 못 해 81일간 아무것도 검증하지 않았다.**
backup_fail 알림의 마지막 발화가 2026-07-05 였다.

애초에 가능하지 않았다. 컨테이너에는 /scripts 도 rclone 도 없다. 그래서 —
  · 검증은 호스트 cron 이 한다(rclone·Drive 자격증명이 거기 있다)
  · 결과를 reports/backup_verify.json 에 남긴다(컨테이너가 바인드로 읽는다)
  · celery 는 그 결과만 읽고, 없거나 낡으면 실패로 보고 알림을 올린다
  · 알림 INSERT 도 psql 이 아니라 asyncpg 로 한다 — 컨테이너에 psql 이 없어
    FileNotFoundError 로 죽었다(탐지는 되고 기록이 안 되는 상태였다)
"""
import pathlib
import shutil
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
# 아래 두 검사는 **호스트 전용**이다. 컨테이너는 /crawler 만 바인드하므로
# ROOT.parent 가 '/' 가 되어 scripts/ 가 없고, crontab 명령도 없다.
# 조용히 통과시키지 않고 skip 으로 남긴다 — 호스트 수트에서는 반드시 돈다.
_HOST_SCRIPTS = (ROOT.parent / "scripts" / "drive-sync" / "verify-backup.sh")
_HAS_CRONTAB = shutil.which("crontab") is not None
sys.path.insert(0, str(ROOT))

SRC = (ROOT / "tasks.py").read_text()
_RAW = SRC.split("def verify_backup", 1)[1].split("\n@app.task", 1)[0]
# **주석을 뺀 코드만 본다.** 사유를 적은 주석에 'psql'·'verify-backup.sh' 가
# 등장하므로, 주석까지 포함하면 "쓰지 않는다"는 설명 때문에 테스트가 실패한다.
BODY = "\n".join(
    l for l in _RAW.splitlines()
    if not l.strip().startswith("#")
)
DOC = _RAW   # 주석 포함 — 사유가 적혀 있는지 볼 때 쓴다


def test_does_not_exec_the_shell_script():
    """컨테이너에는 /scripts 가 없다 — exec 하려 들면 영원히 script_missing 이다."""
    # 이름을 언급하는 것(힌트 문구)은 문제가 아니다 — **실행하는 것**이 문제다.
    assert "subprocess.run" not in BODY, (
        "셸 스크립트를 subprocess 로 실행한다 — 컨테이너에서 찾을 수 없어 no-op 가 된다")
    assert "script_missing" not in BODY, "찾지 못하면 조용히 반환하는 경로가 남아 있다"
    assert "os.path.join(_CRAWLER_DIR" not in BODY, (
        "컨테이너에서 '/' 가 되는 repo_root 계산이 남아 있다")


def test_reads_the_state_file_written_by_host():
    assert "backup_verify.json" in BODY, "호스트가 남긴 검증 결과를 읽지 않는다"
    assert "REPORTS_DIR" in BODY, "경로를 박아 넣었다 — 박스마다 다르다"


def test_stale_state_counts_as_failure():
    """결과가 낡았으면 'ok' 를 믿을 수 없다 — 검증기가 멈춘 것이다.

    이 가드가 없으면 호스트 cron 이 죽어도 마지막 성공 결과를 영원히 ok 로 읽는다.
    """
    assert "state_age_hours" in BODY
    assert "verify_state_stale" in BODY, "낡은 결과를 성공으로 읽는다"
    assert "verify_state_missing" in BODY, "결과 파일이 없을 때를 다루지 않는다"


def test_alert_insert_does_not_use_psql():
    """컨테이너에 psql 이 없다 — 실패를 감지해도 기록이 안 됐다."""
    assert "psql" not in BODY, "psql 서브프로세스를 쓴다 — FileNotFoundError 로 죽는다"
    assert "asyncpg" in BODY, "DB 기록 경로가 없다"


def test_alert_respects_cooldown():
    """수동 재실행으로 알림이 쏟아지면 안 된다."""
    assert "cooldown_sec" in BODY


@pytest.mark.skipif(not _HOST_SCRIPTS.is_file(),
                    reason="호스트 전용 — 컨테이너에는 scripts/ 가 없다")
def test_host_script_writes_the_report_copy():
    """컨테이너가 읽을 자리에 남기지 않으면 위 경로가 전부 헛돈다."""
    sh = _HOST_SCRIPTS.read_text()
    assert "REPORT_COPY" in sh
    assert 'tee "$STATE_FILE" "$REPORT_COPY"' in sh, (
        "검증 결과를 reports/ 에 남기지 않는다 — celery 가 읽을 수 없다")


@pytest.mark.skipif(not _HAS_CRONTAB,
                    reason="호스트 전용 — 컨테이너에는 crontab 이 없다")
def test_verification_is_registered_in_cron():
    """스크립트가 멀쩡해도 **아무도 부르지 않으면** 검증은 0회다.

    실측 — 5,259바이트짜리 verify-backup.sh 가 cron 에 없었다.
    """
    import subprocess
    out = subprocess.run(["crontab", "-l"], capture_output=True, text=True).stdout
    assert "verify-backup.sh" in out, (
        "verify-backup.sh 가 cron 에 없다 — 검증이 어디서도 돌지 않는다")
