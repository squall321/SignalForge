# SP 코어 키트 시험장 8단계(가짜 SP)를 우리 받기(plm_mirror.sync)로 돌려 채점기를 통과하는지 본다 — 키트 01-AI-GUIDE §8
import json
import pathlib
import subprocess
import sys
import urllib.request

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
pytest.importorskip("requests", reason="키트 받기(sp_core_pull)가 requests 를 쓴다 — crawler 이미지에 있다")

from base import plm_mirror as P  # noqa: E402

BED = ROOT / "sp_core_kit" / "testbed"
EXPECT = {"정상": "ok", "확인 필요": "check"}


@pytest.fixture
def fake_sp():
    """가짜 SP — 빈 포트를 잡고 첫 줄에 PORT=<번호> 를 찍는다. -B 로 키트 폴더에 __pycache__ 를 만들지 않는다."""
    proc = subprocess.Popen([sys.executable, "-B", str(BED / "fake_sp.py"), "--port", "0", "--quiet"],
                            stdout=subprocess.PIPE, text=True)
    try:
        line = proc.stdout.readline()
        assert line.startswith("PORT="), line
        yield f"http://127.0.0.1:{int(line.split('=', 1)[1])}"
    finally:
        proc.terminate()
        proc.wait(timeout=10)


def _step(server: str) -> dict:
    req = urllib.request.Request(f"{server}/_step", method="POST", data=b"")
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode("utf-8"))


def test_testbed_eight_steps(tmp_path, fake_sp):
    token = tmp_path / "sp_token"
    token.write_text("test-token\n")
    env = {P.ENV_URL: f"{fake_sp}/api", P.ENV_TOKEN_FILE: str(token), P.ENV_TYPES: "*"}   # 시험장 타입은 공개 타입 전부
    root = tmp_path / "plm"
    passed = []
    for step in range(1, 9):
        info = _step(fake_sp)
        r = P.sync(env, root, full=info["run"] == "처음부터")
        assert r["status"] == EXPECT[info["expect_status"]], (step, info["title"], r)
        dump = tmp_path / f"state-{step}.json"
        subprocess.run([sys.executable, "-B", str(BED / "dump_reference.py"), "--out", str(root / P.OUT),
                        "--to", str(dump)], check=True, capture_output=True)
        cmp = subprocess.run([sys.executable, "-B", str(BED / "compare.py"), "--dump", str(dump), "--server", fake_sp],
                             capture_output=True, text=True)
        assert cmp.returncode == 0, f"{step}단계 {info['title']} — 규칙 {info['rules']}\n{cmp.stdout}{cmp.stderr}"
        passed.append((step, r["status"]))
    assert passed == [(1, "ok"), (2, "ok"), (3, "ok"), (4, "ok"), (5, "ok"), (6, "ok"), (7, "check"), (8, "check")]
    assert not list((ROOT / "sp_core_kit").rglob("__pycache__")), "키트 폴더는 고치지 않는다"
