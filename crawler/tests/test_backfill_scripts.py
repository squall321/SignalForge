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


def test_every_deep_site_is_scheduled():
    """SITES 에 넣고 요일 배분에 빠뜨리면 그 소스는 영영 안 돈다.

    등록과 배분이 따로라 조용히 어긋난다 — 실패도 로그도 없다.
    """
    import re
    sh = (SCRIPTS / "deep-page-backfill.sh").read_text()
    scheduled = set()
    for m in re.finditer(r'SITES="([a-z_0-9,]+)"', sh):
        scheduled |= {x for x in m.group(1).split(",") if x}

    py = (SCRIPTS.parent / "crawler" / "scripts" / "deep_page_backfill.py").read_text()
    sites = set(re.findall(r'^\s+"([a-z_0-9]+)": \("platforms\.', py, re.M))

    assert sites, "SITES 를 못 읽었다"
    missing = sites - scheduled
    assert not missing, f"요일 배분에 빠진 소스: {sorted(missing)}"
    extra = scheduled - sites
    assert not extra, f"SITES 에 없는 스케줄: {sorted(extra)}"


def test_unsupported_and_sites_do_not_overlap():
    """같은 소스가 '가능'과 '불가' 양쪽에 있으면 판단이 흐려진다."""
    import re
    py = (SCRIPTS.parent / "crawler" / "scripts" / "deep_page_backfill.py").read_text()
    sites = set(re.findall(r'^\s+"([a-z_0-9]+)": \("platforms\.', py, re.M))
    block = py.split("UNSUPPORTED = {", 1)[1].split("}", 1)[0]
    unsup = set(re.findall(r'"([a-z_0-9]+)":', block))
    assert unsup, "불가 목록이 비었다"
    assert not (sites & unsup), f"양쪽에 있다: {sorted(sites & unsup)}"


def test_rotate_logs_truncates_in_place():
    """**파일을 지우면 안 된다.** 프로세스가 열어둔 fd 를 유지해야 한다 —
    rm/mv 하면 celery 는 지워진 inode 에 계속 쓰고 디스크는 그대로 차 있다."""
    src = (SCRIPTS / "rotate-logs.sh").read_text()
    assert ": > \"$f\"" in src, "제자리 절단(`: > file`)을 쓰지 않는다"
    assert "rm -f \"$f\"\n" not in src, "로그 파일 자체를 지운다"
    assert "mv \"$f\"" not in src, "로그 파일을 옮긴다 — fd 가 끊긴다"


def test_rotate_logs_keeps_tail():
    """자르기 전 꼬리를 남겨야 한다 — 직전 상황을 잃으면 사고 조사가 안 된다."""
    src = (SCRIPTS / "rotate-logs.sh").read_text()
    assert "tail -n" in src and "KEEP_LINES" in src


def test_rotate_logs_skips_itself():
    src = (SCRIPTS / "rotate-logs.sh").read_text()
    assert "rotate-logs.log) continue" in src, "자기 로그를 자르면 기록이 사라진다"


BACKFILL_COLLECTORS = [
    "youtube-backfill.sh", "hn-backfill.sh", "wpnews-backfill.sh",
    "wayback-backfill.sh", "kr-backfill.sh", "global-backfill.sh",
]


@pytest.mark.parametrize("name", BACKFILL_COLLECTORS)
def test_collectors_run_in_backfill_mode(name):
    """**역사 백필은 번역을 건너뛴다.**

    대량 수집이 번역 서비스를 두드리면 레이트리밋을 유발해 실시간 파이프라인의
    할당량까지 갉아먹는다(실측 — youtube 백필이 MyMemory 429 를 연달아 맞았다).
    원문은 저장되고 12시간 주기 translation_reprocess 가 나중에 메운다.
    """
    src = (SCRIPTS / name).read_text()
    assert "BACKFILL_MODE=1" in src, f"{name}: 수집 시점에 번역을 시도한다"


def test_backfill_mode_is_honoured_by_crawler():
    """크롤러가 그 플래그를 실제로 본다 — 셸에만 있으면 무의미하다."""
    src = (SCRIPTS.parent / "crawler" / "base" / "crawler.py").read_text()
    assert 'os.getenv("BACKFILL_MODE"' in src
    assert "_translate_deadline" in src


def test_runner_captures_exit_code_before_date():
    """`echo "$(date ...) rc=$?"` 는 $(date) 가 먼저 실행되며 $? 를 0 으로 덮는다.

    실패했는데 rc=0 으로 찍혀 원인 추적이 막힌다(실측 2026-09-16 —
    youtube-backfill 이 '실패(rc=0)' 로 남았다).
    """
    src = (SCRIPTS / "backfill-runner.sh").read_text()
    assert "local rc=$?" in src, "종료코드를 즉시 붙잡지 않는다"
    assert 'rc=$?)"' not in src, "$(date) 뒤에서 $? 를 읽고 있다"


def test_runner_logs_end_even_on_failure():
    """실패해도 '끝' 을 남겨야 죽은 건지 도는 건지 구분된다."""
    src = (SCRIPTS / "backfill-runner.sh").read_text()
    body = src.split("run_step() {", 1)[1].split("\n}", 1)[0]
    assert "■" in body, "종료 로그가 run_step 안에 없다"
    assert body.index("rc=$?") < body.index("■"), "종료 로그 전에 rc 를 잡아야 한다"
