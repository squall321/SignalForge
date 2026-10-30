# PLM 거울 — SP 키트 받기를 감싸는 쪽(잠금·주 1회 처음부터·결과·토큰)과 사본을 읽는 조회 색인을 고정한다(시험장 8단계는 test_sp_core_testbed)
import fcntl
import importlib.util
import json
import os
import pathlib
import sqlite3
import sys
import textwrap

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from base import plm_mirror as P  # noqa: E402

TOKEN = "tok-SECRET-123"


def _env(tmp_path, token=TOKEN):
    tf = tmp_path / "sp_token"
    tf.write_text(f"# 사내 허브\nSF_SP_TOKEN={token}\n")
    return {P.ENV_URL: "https://hub.example/api", P.ENV_TOKEN_FILE: str(tf)}


@pytest.fixture
def fake_kit(tmp_path, monkeypatch):
    """키트 받기 대신 — 받은 인자·설정·토큰을 root 밖에 적고, FAKE_PLAN 대로 끝난다(망 없이 감싸는 쪽만 본다)."""
    script = tmp_path / "fake_pull.py"
    script.write_text(textwrap.dedent('''
        import json, os, pathlib, sys
        cfg = pathlib.Path(sys.argv[sys.argv.index("--config") + 1])
        out = pathlib.Path([l.split("=", 1)[1].strip() for l in cfg.read_text().splitlines() if l.startswith("out_dir")][0])
        plan = json.loads(os.environ.get("FAKE_PLAN") or "{}")
        with open(os.environ["FAKE_LOG"], "a") as fh:
            fh.write(json.dumps({"argv": sys.argv[1:], "token": os.environ.get("SP_TOKEN"), "cfg": cfg.read_text()}) + "\\n")
        (out / "plm_task.csv").write_text("key\\n")
        print("plm_task: 3건 수신")
        for line in plan.get("stderr", []):
            print(line, file=sys.stderr)
        sys.exit(plan.get("rc", 0))
    '''))
    log = tmp_path / "calls.jsonl"
    monkeypatch.setattr(P, "KIT_PULL", script)
    monkeypatch.setenv("FAKE_LOG", str(log))
    monkeypatch.delenv("FAKE_PLAN", raising=False)
    return lambda: [json.loads(ln) for ln in log.read_text().splitlines()]


@pytest.fixture
def kit():
    """진짜 키트 받기 모듈 — 사본을 키트 코드로 쓴다. 키트 폴더에 __pycache__ 를 만들지 않는다."""
    pytest.importorskip("requests", reason="키트가 requests 를 쓴다 — crawler 이미지에 있다")
    before = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec = importlib.util.spec_from_file_location("sp_core_pull", P.KIT_PULL)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        sys.dont_write_bytecode = before
    return mod


def test_not_configured_without_url_or_token(tmp_path):
    r = P.sync({}, tmp_path / "plm")
    assert r["status"] == "not_configured" and len(r["problems"]) == 2
    assert not (tmp_path / "plm").exists()


def test_kit_runs_with_token_only_in_its_env_and_leaves_a_status(tmp_path, fake_kit):
    root = tmp_path / "plm"
    r = P.sync(_env(tmp_path), root)
    assert r["status"] == "ok" and r["full"] is True, "처음은 처음부터 받는다"
    call = fake_kit()[-1]
    assert call["token"] == TOKEN and "--full" in call["argv"] and "-B" not in call["argv"]
    assert TOKEN not in call["cfg"] and "types = plm_project, plm_task, plm_model" in call["cfg"]
    assert not list((root / P.OUT).glob("*.csv")), "수신 이력 CSV 는 남기지 않는다"
    st = P.last_status(root)
    assert st["status"] == "ok" and st["lines"] == ["plm_task: 3건 수신"] and st["last_full_at"] == st["pulled_at"]
    for f in root.rglob("*"):
        assert not f.is_file() or TOKEN not in f.read_text(errors="ignore"), f"토큰이 {f} 에 남았다"


def test_weekly_full_receive(tmp_path, fake_kit):
    env, root = _env(tmp_path), tmp_path / "plm"
    P.sync(env, root)
    assert P.sync(env, root)["full"] is False and "--full" not in fake_kit()[-1]["argv"], "그다음은 증분"
    st = P.last_status(root)
    st["last_full_at"] = "2026-01-01T00:00:00+00:00"
    (root / P.STATUS).write_text(json.dumps(st))
    assert P.sync(env, root)["full"] is True, "마지막 처음부터 받기가 7일을 넘으면 처음부터"
    assert P.sync(env, root, full=True, allow_shrink=True)["full"] and "--allow-shrink" in fake_kit()[-1]["argv"]


def test_check_needed_and_stopped_runs(tmp_path, fake_kit, monkeypatch):
    env, root = _env(tmp_path), tmp_path / "plm"
    P.sync(env, root)
    monkeypatch.setenv("FAKE_PLAN", json.dumps({"rc": 2, "stderr": [
        "확인 필요: plm_project: 공개되지 않은 타입이라 건너뛰었습니다 — APP-CORE-0001"]}))
    r = P.sync(env, root)
    assert r["status"] == "check" and r["problems"] == ["plm_project: 공개되지 않은 타입이라 건너뛰었습니다 — APP-CORE-0001"]
    before = P.last_status(root)
    monkeypatch.setenv("FAKE_PLAN", json.dumps({"rc": 1, "stderr": [f"인증이 거절되었습니다(HTTP 401) {TOKEN}"]}))
    r = P.sync(env, root)
    assert r["status"] == "error" and "401" in r["error"] and TOKEN not in json.dumps(r, ensure_ascii=False)
    assert r["pulled_at"] == before["pulled_at"] and r["last_full_at"] == before["last_full_at"], "멈춘 받기는 시각을 옮기지 않는다"


def test_a_second_run_is_skipped_while_one_holds_the_lock(tmp_path, fake_kit):
    root = tmp_path / "plm"
    (root / P.OUT).mkdir(parents=True)
    with open(root / P.OUT / ".lock", "w") as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        assert P.sync(_env(tmp_path), root)["status"] == "busy"
    assert P.sync(_env(tmp_path), root)["status"] == "ok"


def test_token_file_forms(tmp_path):
    one = tmp_path / "a"
    one.write_text("plain_token_value\n")
    assert P.read_token(one) == "plain_token_value"
    kv = tmp_path / "b"
    kv.write_text("OTHER=x\nexport SF_SP_TOKEN='quoted'\n")
    assert P.read_token(kv) == "quoted"
    bad = tmp_path / "c"
    bad.write_text("OTHER=x\n")
    with pytest.raises(P.NotConfigured) as e:
        P.read_token(bad)
    assert "x" not in str(e.value).replace(str(bad), "")


def test_types_setting(tmp_path):
    assert P.settings({}, tmp_path)["types"] == list(P.TYPES)
    assert P.settings({P.ENV_TYPES: "plm_task, plm_model"}, tmp_path)["types"] == ["plm_task", "plm_model"]
    assert P.settings({P.ENV_TYPES: "*"}, tmp_path)["types"] == [], "공개 타입 전부"


def test_index_reads_the_kit_replica(tmp_path, kit):
    """키트가 쓴 사본 — 활성만 싣는다. 바뀐 식별자 · 합쳐진 진 쪽은 이긴 쪽을 찾는 옛 이름이다. 안 보이게 된 것은 없다."""
    out = tmp_path / "plm" / P.OUT
    cfg = {"out_dir": out}
    kit.save_rows(cfg, "plm_project", [{"key": "P-1", "label": "프로젝트", "properties": {"aliases": ["Galaxy Test Phone"]}}])
    kit.save_rows(cfg, "plm_task", [{"key": "TK-1", "label": "알파", "properties": {"project": "P-1"}},
                                    {"key": "TK-2", "label": "베타", "properties": {"project": "P-1"}},
                                    {"key": "TK-3", "label": "감마", "properties": {"project": "P-1"}}])
    kit.save_rows(cfg, "plm_task", [
        {"key": "TK-1B", "label": "알파", "renamed_from": "TK-1", "previous_keys": ["TK-1"], "properties": {"project": "P-1"}},
        {"key": "TK-2", "label": "베타", "deleted": True, "merged_into": "TK-1B", "properties": {}},
        {"key": "TK-3", "label": "TK-3", "deleted": True, "hidden": True, "properties": {}}])
    kit.save_rows(cfg, "plm_model", [{"key": "SM-S938B_KOR", "properties": {"task": "TK-1B", "base_code": "SM-S938B"}}])
    (out / "catalog.json").write_text(json.dumps({"system": "t", "revision": "3-1", "types": []}))
    idx = P.PlmIndex(tmp_path / "plm" / P.MIRROR)
    assert set(idx.tasks) == {"TK-1B"}, "지움 · 합침 · 안 보임은 싣지 않는다"
    assert idx.by_code["tk-1"] == "TK-1B" and idx.by_code["tk-1b"] == "TK-1B", "옛 식별자로도 찾는다"
    assert idx.by_code["tk-2"] == "TK-1B", "합쳐진 진 쪽 → 이긴 쪽"
    assert idx.project_names["galaxy test phone"] == ["P-1"] and idx.project_of("TK-1B") == "P-1"
    assert [m["key"] for m in idx.task_models("TK-1B")] == ["SM-S938B_KOR"]
    assert idx.meta["revision"] == "3-1"


def test_mirror_reopens_when_the_replica_changes_and_retries_after_a_failed_read(tmp_path, kit, monkeypatch):
    root = tmp_path / "plm"
    assert P.mirror(root) is None
    cfg = {"out_dir": root / P.OUT}
    kit.save_rows(cfg, "plm_task", [{"key": "TK-1", "label": "a"}])
    assert len(P.mirror(root, check_every_s=0)) == 1
    kit.save_rows(cfg, "plm_task", [{"key": f"TK-{i}", "label": "a"} for i in range(3)])
    os.utime(root / P.MIRROR, (1, 1))                    # 같은 순간에 두 번 쓰면 mtime 이 같을 수 있다
    assert len(P.mirror(root, check_every_s=0)) == 3
    kit.save_rows(cfg, "plm_task", [{"key": "TK-9", "label": "a"}])
    os.utime(root / P.MIRROR, (2, 2))
    real = P.PlmIndex

    def locked(path):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(P, "PlmIndex", locked)
    assert len(P.mirror(root, check_every_s=0)) == 3, "받기가 쓰는 도중이면 옛 색인"
    monkeypatch.setattr(P, "PlmIndex", real)
    assert len(P.mirror(root, check_every_s=0)) == 4, "다음에 다시 본다"


def test_index_codes_previous_keys_names_and_models():
    data = {
        "plm_project": [{"key": "P-1", "label": "프로젝트"}],
        "plm_task": [{"key": "TK-001", "label": "알파", "properties": {"project": "P-1"}, "previous_keys": ["OLD-1"]},
                     {"key": "TK-003", "label": "감마_C2", "properties": {"project": "P-1"}},
                     {"key": "TK-009", "label": "감마 프로", "properties": {"project": "P-1"}}],
        "plm_model": [{"key": "SM-S938B_KOR_SKT", "properties": {"task": "TK-001", "base_code": "SM-S938B",
                                                                  "mass_model_code": "SM-S938N"}},
                      {"key": "SM-S928B_NA_C2_ATT", "properties": {"task": "TK-003", "base_code": "SM-S928B"}}],
    }
    idx = P.PlmIndex.from_rows(data)
    assert idx.by_code["tk-001"] == "TK-001" and idx.by_code["old-1"] == "TK-001"
    assert idx.by_name["감마"] == ["TK-003"] and idx.by_name["감마_c2"] == ["TK-003"], idx.by_name
    assert idx.by_model["sm-s938b_kor_skt"] == "SM-S938B_KOR_SKT" and idx.by_model["sm-s938n"] == "SM-S938B_KOR_SKT"
    assert idx.multi_names == ["감마 프로"]
    assert [m["key"] for m in idx.task_models("TK-001")] == ["SM-S938B_KOR_SKT"]


def test_rootdesign_style_key_is_name_code_in_task_code_and_aliases():
    """코어(rootdesign)는 plm_task key = 과제명, 과제코드는 properties.task_code·별칭, 개명은 renamed_from,
    개발모델 코드는 model_code 다(cae00 2026-10-27, SG-03·09·12)."""
    data = {
        "plm_project": [],
        "plm_task": [
            {"key": "감마_C2", "label": "감마_C2", "properties": {"task_code": "TK-77"},
             "aliases": [{"alias": "TK-77A"}, "GAMMA-C2"], "renamed_from": "감마_C1"},
            {"key": "델타", "label": "델타", "properties": {}}],          # task_code 없는 과제도 이름으로 찾힌다
        "plm_model": [{"key": "SM-S938N_KOR", "properties": {"task": "감마_C2", "model_code": "SM-S938N"}}],
    }
    idx = P.PlmIndex.from_rows(data)
    assert idx.key_is_name
    assert idx.by_code["tk-77"] == "감마_C2" and idx.by_code["tk-77a"] == "감마_C2" and idx.by_code["gamma-c2"] == "감마_C2"
    assert "감마_c2" not in idx.by_code, "key(과제명)를 과제코드로 보지 않는다"
    assert idx.by_name["감마_c2"] == ["감마_C2"] and idx.by_name["감마_c1"] == ["감마_C2"] and idx.by_name["감마"] == ["감마_C2"]
    assert idx.by_name["델타"] == ["델타"]
    assert idx.models["SM-S938N_KOR"]["model_code"] == "SM-S938N" and idx.by_model["sm-s938n"] == "SM-S938N_KOR"
    assert idx.tasks["감마_C2"]["display"] == "TK-77(감마_C2)"
