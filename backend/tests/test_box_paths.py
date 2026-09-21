# 런타임 코드에 **박스 절대경로**가 박히지 못하게 — 리포 루트는 박스마다 다르다(2026-09-20)
"""왜 — dev 는 `~/claude/<Repo>`, cae00 은 `~/Projects/<Repo>` 다. 코드에 한쪽을 박으면 다른 박스에서
**조용히** 빗나간다(예외가 아니라 빈 디렉터리·빈 목록·다음 후보로 넘어감).

같은 날 이 리포에서 두 번 났다.
  · `mcp-server/tools/stats.py`·`backend/app/services/stats_service.py` — `("/shared", "/app/../shared")`
    가 정규화하면 같은 경로라 폴백이 없었고, cae00 에서 MCP 가 `ModuleNotFoundError` 로 **아예 안 떴다**.
  · `frontend/serve_prod.py` — dist 경로에 dev 절대경로가 박혀, 다른 박스에서는 없는 디렉터리를
    서빙 루트로 잡고도 조용히 떴다.

⚠ `crawler/scripts/` 는 제외한다 — 그 박스에서 한 번 돌리고 마는 조사 스크립트라 성격이 다르다
(env 로 덮을 수 있게는 돼 있다). 넓히면 소음이 되고, 소음이 나면 아무도 안 본다.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ("backend/app", "mcp-server", "shared", "frontend")
BOX_PATH = re.compile(r"""["']/home/[a-z0-9_.-]+/(claude|Projects)/""")

ALLOWED: dict[str, int] = {}          # **줄어들기만 한다**


def _hits() -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for sub in RUNTIME:
        base = ROOT / sub
        if not base.is_dir():
            continue
        for f in sorted(base.rglob("*.py")):
            if any(part in (".venv", "node_modules", "__pycache__") for part in f.parts):
                continue
            for n, line in enumerate(f.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                if line.lstrip().startswith("#"):
                    continue          # 주석은 설명이다(이 사고를 적은 주석이 그 경로를 인용한다)
                if BOX_PATH.search(line):
                    found.setdefault(str(f.relative_to(ROOT)), []).append(f"{n}: {line.strip()[:90]}")
    return found


def test_no_box_absolute_path_in_runtime_code() -> None:
    found = _hits()
    over = {f: v for f, v in found.items() if len(v) > ALLOWED.get(f, 0)}
    assert not over, (
        "런타임 코드에 박스 절대경로가 박혔다 — 다른 박스에서 조용히 빗나간다.\n"
        "파일 위치에서 유도하라(`Path(__file__).resolve().parents[N]`), 배포가 다르면 env 로 주입하라.\n"
        + "\n".join(f"  {f}\n    " + "\n    ".join(v) for f, v in over.items()))


def test_the_frontend_serves_a_dist_next_to_itself() -> None:
    """서빙 루트는 이 파일 옆의 `dist` 다 — 박스가 달라도 따라온다(주입값이 있으면 그것이 이긴다)."""
    src = (ROOT / "frontend" / "serve_prod.py").read_text(encoding="utf-8")
    assert "FRONTEND_DIST" in src, "주입 경로가 없다"
    assert "__file__" in src.split("DIST =", 1)[1].splitlines()[0], "파일 위치에서 유도하지 않는다"
