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


def test_runner_caps_each_step():
    """한 단계가 길어지면 뒤가 전부 굶는다.

    실측(2026-09-16) — 러너 96분 중 reddit 이 62분이었고 그 뒤에 5단계가 남아
    있었다. 모든 백필이 커서를 남기므로 잘려도 손실이 없다.
    """
    src = (SCRIPTS / "backfill-runner.sh").read_text()
    assert "timeout" in src, "단계별 시간 상한이 없다"
    assert "STEP_TIMEOUT" in src
    assert "-eq 124" in src, "시간 초과(124)를 실패와 구분하지 않는다"


def test_timeout_is_not_counted_as_failure():
    """설계된 중단을 실패로 세면 경보가 소음이 된다."""
    src = (SCRIPTS / "backfill-runner.sh").read_text()
    block = src.split("-eq 124", 1)[1].split("fi", 1)[0]
    assert "rc=0" in block, "시간 초과를 실패로 남긴다"


@pytest.mark.parametrize("script,marker", [
    ("reddit_arctic_backfill.py", "_save_state(state)"),
    ("deep_page_backfill.py", "_save_state(state)"),
    ("repair_dogdrip_dates.py", "_save_done(done)"),
])
def test_cursors_persist_incrementally(script, marker):
    """중간에 잘려도 그 지점까지 남아야 한다 — 끝에서 한 번만 쓰면 다 잃는다."""
    src = (SCRIPTS.parent / "crawler" / "scripts" / script).read_text()
    body = src.split("async def main", 1)[1]
    assert body.count(marker) >= 1, f"{script}: 진행 중 상태 저장이 없다"


# ── 자식 실패가 러너에 전달되는가 ─────────────────────────────────────────
# 2026-10-15 감사: 러너가 부르는 자식 12개 중 11개가 종료코드를 전파하지 않아
# 2026-09-16 에 일부러 넣은 실패 경보(⚠)가 **구조적으로 발화 불가**였다.
# 09-18~09-25 여덟 번의 실패가 backfill-runner.log 에 한 줄도 남지 않았고,
# 그동안 터키어 İ KeyError 로 Reddit 역사 수집이 죽어 있던 것을 아무도 몰랐다.
_RUNNER_CHILDREN = [
    p for p in (SCRIPTS / "backfill-runner.sh").read_text().splitlines()
    if p.strip().startswith("run_step")
]


def _child_names():
    out = []
    for line in _RUNNER_CHILDREN:
        tok = line.split()
        if len(tok) >= 2 and tok[1].endswith(".sh"):
            out.append(tok[1])
    return out


def test_runner_children_are_discoverable():
    """자식 목록을 못 읽으면 아래 테스트들이 전부 공집합을 돌아 무의미해진다."""
    names = _child_names()
    assert len(names) >= 10, f"자식 {len(names)}개만 찾았다: {names}"


@pytest.mark.parametrize("name", _child_names(), ids=lambda n: n)
def test_child_propagates_exit_code(name):
    """자식이 rc 를 삼키면 러너는 영원히 '정상 완료'만 본다."""
    path = SCRIPTS / name
    if not path.exists():
        pytest.skip(f"{name} 없음")
    src = path.read_text().rstrip()
    last = [l for l in src.splitlines() if l.strip() and not l.strip().startswith("#")][-1]
    assert last.strip() in ('exit "$rc"', "exit 0", 'exit "${SF_FAILED:-0}"'), (
        f"{name} 의 마지막 실행문이 '{last.strip()[:50]}' 이다 — "
        "종료코드를 전파하지 않으면 러너의 실패 경보가 울리지 않는다")


@pytest.mark.parametrize("name", _child_names(), ids=lambda n: n)
def test_cursor_advance_is_guarded_by_rc(name):
    """**실패해도 커서가 전진하면 데이터 유실이다.**

    실측 — DB 가 내려가 있던 나흘간 HN 이 2013·2012·2011·2010 을 빈손으로
    '소비'했다. 지나간 연도는 FLOOR 까지 한 바퀴 돌아야 다시 온다.
    """
    path = SCRIPTS / name
    if not path.exists():
        pytest.skip(f"{name} 없음")
    lines = path.read_text().splitlines()
    writes = [i for i, l in enumerate(lines)
              if '> "$STATE"' in l and not l.strip().startswith("#")]
    if not writes:
        pytest.skip(f"{name} 은 셸에서 커서를 쓰지 않는다")
    for i in writes:
        ctx = "\n".join(lines[max(0, i - 10):i])
        assert ('"$rc" -eq 0' in ctx or '"$rc" = "3"' in ctx), (
            f"{name}:{i+1} 커서 쓰기가 rc 조건 밖에 있다 — 실패해도 구간을 소비한다")


@pytest.mark.parametrize("name", _child_names(), ids=lambda n: n)
def test_rc_captured_before_any_command_substitution(name):
    """`echo "$(date ...) (rc=$?)"` 는 **항상 0** 이다 — $(date) 가 $? 를 덮는다.

    러너에서 고친 버그가 deep-page-backfill.sh 에 남아 있었다(2026-10-15 감사).
    """
    path = SCRIPTS / name
    if not path.exists():
        pytest.skip(f"{name} 없음")
    for i, line in enumerate(path.read_text().splitlines(), 1):
        if line.strip().startswith("#"):
            continue
        if "$(" in line and "rc=$?" in line.split("$(", 1)[1]:
            pytest.fail(f"{name}:{i} 명령치환 뒤에서 $? 를 읽는다 — 항상 0 이다\n  {line.strip()[:70]}")
        if "$?" in line and "$(" in line and line.index("$(") < line.index("$?"):
            pytest.fail(f"{name}:{i} $(...) 가 $? 보다 앞에 있다 — $? 가 덮인다\n  {line.strip()[:70]}")
