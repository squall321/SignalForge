# 백필 셸 스크립트가 문법·필수 변수·락·상태 규약을 지키는지 검증한다.
import pathlib
import re
import shutil
import subprocess

import pytest

SCRIPTS = pathlib.Path(__file__).resolve().parents[2] / "scripts"
BACKFILL = sorted(
    p for p in SCRIPTS.glob("*backfill*.sh")
) + [SCRIPTS / "repair-dogdrip.sh"]
BACKFILL = [p for p in BACKFILL if p.exists()]


def _ids(p):
    return p.name


@pytest.mark.parametrize("path", BACKFILL, ids=_ids)
def test_shell_syntax(path):
    if not shutil.which("bash"):
        pytest.skip("bash 없음")
    r = subprocess.run(["bash", "-n", str(path)], capture_output=True, text=True)
    assert r.returncode == 0, f"{path.name} 문법 오류: {r.stderr}"


@pytest.mark.parametrize("path", BACKFILL, ids=_ids)
def test_uses_undefined_variable_guard(path):
    """`set -u` 가 없으면 오타난 변수가 빈 값으로 흘러 조용히 잘못 돈다."""
    src = path.read_text()
    assert re.search(r"set\s+-[a-z]*u", src), f"{path.name}: set -u 가 없다"


@pytest.mark.parametrize("path", BACKFILL, ids=_ids)
def test_db_url_is_defined_before_use(path):
    """DB 를 쓰면 반드시 먼저 정의해야 한다.

    편집 중에 정의를 지워 `DB: unbound variable` 로 매 실행 즉시 죽은 적이 있다
    (2026-09-15 wpnews). set -u 덕에 조용히 틀리진 않았지만, 로그에 '시작'만
    남고 '끝'이 없어 겉보기엔 실행 중처럼 보였다.
    """
    src = path.read_text()
    if "$DB" not in src and "${DB}" not in src:
        pytest.skip("DB 미사용")
    define = src.find('DB="')
    assert define != -1, f"{path.name}: DB 를 쓰는데 정의가 없다"
    first_use = min(
        (i for i in (src.find('"$DB"'), src.find('="$DB'), src.find("${DB}"))
         if i != -1), default=-1)
    assert first_use == -1 or define < first_use, \
        f"{path.name}: DB 를 정의 전에 쓴다"


@pytest.mark.parametrize("path", BACKFILL, ids=_ids)
def test_holds_global_lock(path):
    """백필은 직렬이어야 한다 — 호스트 swap 이 0 이라 동시 실행은 OOM 을 부른다."""
    src = path.read_text()
    if path.name == "backfill-runner.sh":
        assert "sf-backfill-runner.lock" in src
        return
    assert "sf-backfill-global.lock" in src, f"{path.name}: 전역 락이 없다"
    assert "flock -n" in src, f"{path.name}: flock 이 없다"


@pytest.mark.parametrize("path", BACKFILL, ids=_ids)
def test_logs_start_and_end(path):
    """'시작'만 남고 '끝'이 없으면 죽은 건지 도는 건지 구분이 안 된다."""
    src = path.read_text()
    if path.name == "backfill-runner.sh":
        pytest.skip("러너는 자식이 기록한다")
    if "시작" not in src:
        pytest.skip("로그 규약 밖")
    assert "끝" in src or "완료" in src or "진행 중" in src, \
        f"{path.name}: 종료 로그가 없다"


def test_runner_invokes_every_backfill():
    """새 백필을 만들고 러너에 등록하지 않으면 영영 안 돈다."""
    runner = (SCRIPTS / "backfill-runner.sh").read_text()
    for path in BACKFILL:
        if path.name == "backfill-runner.sh":
            continue
        assert path.name in runner, f"{path.name} 이 러너에 없다 — 영영 안 돈다"
