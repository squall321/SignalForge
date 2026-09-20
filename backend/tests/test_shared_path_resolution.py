# `shared/stats_sql.py` 를 못 찾아 MCP 가 아예 안 뜨던 자리(2026-09-20 cae00)
"""왜 — 두 모듈이 `shared/stats_sql.py` 를 `sys.path` 에 끼워 import 한다. 그 경로 목록이
`("/shared", "/app/../shared")` 였는데 **둘은 정규화하면 같은 경로**다(`/app/../shared` → `/shared`).
즉 폴백이 폴백이 아니었다.

컨테이너는 `/shared` 로 마운트하니 문제가 안 났고, dev 호스트에도 우연히 `/shared` 가 있어
호스트 실행까지 통과했다. cae00 에는 없다 — `update-all` 이 호스트에서 MCP 를 띄우자
`ModuleNotFoundError: No module named 'stats_sql'` 로 **기동 자체가 실패**했다(헬스 120초 대기 후 FAIL).

이 리포의 규율 그대로다 — 박스마다 경로가 다르니 **절대경로를 박지 말고 파일 위치에서 리포 루트를 유도한다.**
"""
import os
import pathlib
import re

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
TARGETS = (REPO / "mcp-server" / "tools" / "stats.py",
           REPO / "backend" / "app" / "services" / "stats_service.py")


def _shared_paths(src_file: pathlib.Path):
    """모듈을 import 하지 않고 `_SHARED_PATHS` 계산식만 그 파일의 이름으로 실행한다
    (import 하면 db·sqlalchemy 등 앱 의존이 전부 딸려 온다)."""
    line = next((l for l in src_file.read_text(encoding="utf-8").splitlines()
                 if l.startswith("_SHARED_PATHS")), None)
    assert line, f"{src_file.name} 에 _SHARED_PATHS 가 없다 — 경로 유도를 되돌렸나?"
    ns = {"pathlib": pathlib, "__file__": str(src_file)}
    exec(line, ns)                      # noqa: S102 — 이 리포 자기 소스의 한 줄이다
    return list(ns["_SHARED_PATHS"])


@pytest.mark.parametrize("src", TARGETS, ids=lambda p: p.name)
def test_the_fallback_is_actually_a_different_path(src):
    """옛 버그의 본체 — `/app/../shared` 는 `/shared` 와 같은 경로라 폴백이 없었다."""
    paths = _shared_paths(src)
    real = {os.path.realpath(p) for p in paths}
    assert len(real) == len(paths) >= 2, f"경로가 겹친다(폴백이 아니다): {paths}"


@pytest.mark.parametrize("src", TARGETS, ids=lambda p: p.name)
def test_the_repo_fallback_points_at_a_real_shared_module(src):
    """호스트에서 띄울 때 실제로 찾아지는가 — 이게 안 되면 cae00 에서 또 못 뜬다."""
    repo_path = pathlib.Path(_shared_paths(src)[-1])
    assert repo_path.is_dir(), f"리포 폴백이 디렉터리가 아니다: {repo_path}"
    assert (repo_path / "stats_sql.py").is_file(), f"stats_sql.py 가 없다: {repo_path}"


@pytest.mark.parametrize("src", TARGETS, ids=lambda p: p.name)
def test_no_host_absolute_only_fallback_comes_back(src):
    """`/app/...` 류 호스트 절대경로만으로 된 폴백은 다시 들어오면 안 된다."""
    # 주석은 뺀다 — 이 사고를 설명하는 주석이 그 문자열을 인용하고 있고, 그 설명은 남아야 한다.
    code = "\n".join(l for l in src.read_text(encoding="utf-8").splitlines()
                      if not l.lstrip().startswith("#"))
    text = src.read_text(encoding="utf-8")
    assert '"/app/../shared"' not in code, "정규화하면 /shared 와 같은 그 폴백이 되돌아왔다"
    assert re.search(r"_SHARED_PATHS\s*=.*__file__", text), "파일 위치에서 유도하지 않는다"
