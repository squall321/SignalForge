# PLM 거울 수신(전체 스냅샷·원자 교체·급감 보류·401·404 둘·재시도)과 조회 색인을 가짜 허브로 고정한다
import json
import os
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from base import plm_mirror as P  # noqa: E402
from sp_fake_hub import TOKEN, FakeHub, serve  # noqa: E402

httpx = pytest.importorskip("httpx")


@pytest.fixture
def hub():
    h = FakeHub()
    srv, url = serve(h)
    yield h, url
    srv.shutdown()


def _env(tmp_path, url, token=TOKEN):
    tf = tmp_path / "sp_token"
    tf.write_text(f"# 사내 허브\nSF_SP_TOKEN={token}\n")
    return {P.ENV_URL: url, P.ENV_TOKEN_FILE: str(tf)}


def _seed(h, n_tasks=12):
    h.add("plm_project", "P-1", "프로젝트")
    for i in range(n_tasks):
        h.add("plm_task", f"TK-{i:03d}", f"과제{i}", project="P-1", task_status="진행")
        h.add("plm_model", f"SM-S938B_KOR_{i}", task=f"TK-{i:03d}", base_code="SM-S938B")


def _sync(tmp_path, url, **kw):
    return P.sync(_env(tmp_path, url), tmp_path / "plm", pause_s=0.0, **kw)


def test_not_configured_without_url_or_token(tmp_path):
    r = P.sync({}, tmp_path / "plm")
    assert r["status"] == "not_configured" and len(r["problems"]) == 2
    assert not (tmp_path / "plm" / P.MIRROR).exists()


def test_full_snapshot_pages_skips_deleted_hidden_and_hides_token(tmp_path, hub, monkeypatch):
    h, url = hub
    monkeypatch.setattr(P, "PAGE_LIMIT", 5)            # 쪽 넘기기를 본다(과제 12 → 세 쪽)
    _seed(h)
    h.add("plm_task", "TK-DEL", "지움", project="P-1", deleted=True)
    h.add("plm_task", "TK-HID", "숨김", project="P-1", hidden=True)
    r = _sync(tmp_path, url)
    assert r["status"] == "ok" and r["counts"] == {"plm_project": 1, "plm_task": 12, "plm_model": 12}, r
    idx = P.PlmIndex(tmp_path / "plm" / P.MIRROR)
    assert "TK-DEL" not in idx.tasks and "TK-HID" not in idx.tasks
    for f in (tmp_path / "plm").iterdir():               # 토큰은 거울·상태 파일 어디에도 없다
        assert TOKEN not in f.read_bytes().decode("utf-8", "ignore"), f
    st = json.loads((tmp_path / "plm" / P.STATUS).read_text())
    assert st["status"] == "ok" and st["host"].startswith("127.0.0.1")


def test_sharp_drop_is_held_until_allowed(tmp_path, hub):
    h, url = hub
    _seed(h, 12)
    assert _sync(tmp_path, url)["status"] == "ok"
    h.objects["plm_task"] = h.objects["plm_task"][:4]   # 토큰 범위가 바뀌어 2/3 가 안 보인다
    r = _sync(tmp_path, url)
    assert r["status"] == "held" and r["held"] == ["plm_task"], r
    assert len(P.PlmIndex(tmp_path / "plm" / P.MIRROR).tasks) == 12, "보류면 옛 거울을 그대로 둔다"
    assert _sync(tmp_path, url, allow_drop=True)["status"] == "ok"
    assert len(P.PlmIndex(tmp_path / "plm" / P.MIRROR).tasks) == 4


def test_auth_rejected_keeps_old_mirror_and_never_echoes_token(tmp_path, hub):
    h, url = hub
    _seed(h)
    assert _sync(tmp_path, url)["status"] == "ok"
    r = P.sync(_env(tmp_path, url, token="wrong_token_XYZ"), tmp_path / "plm", pause_s=0.0)
    assert r["status"] == "error" and "401" in r["error"] and "wrong_token_XYZ" not in json.dumps(r)
    assert len(P.PlmIndex(tmp_path / "plm" / P.MIRROR).tasks) == 12


def test_retries_5xx_and_restarts_on_unreadable_cursor(tmp_path, hub, monkeypatch):
    h, url = hub
    monkeypatch.setattr(P, "PAGE_LIMIT", 5)
    _seed(h)
    h.fail_5xx = 2                                       # 재시도 셋 안에 회복
    h.reject_cursor_once = True                          # 둘째 쪽에서 0002 → 처음부터 한 번 더
    r = _sync(tmp_path, url)
    assert r["status"] == "ok" and r["counts"]["plm_task"] == 12, r


def test_required_type_closed_is_an_error(tmp_path, hub):
    h, url = hub
    _seed(h)
    h.closed.add("plm_task")
    r = _sync(tmp_path, url)
    assert r["status"] == "error" and "plm_task" in r["error"]
    assert not (tmp_path / "plm" / P.MIRROR).exists()


def test_index_codes_previous_keys_names_and_models(tmp_path):
    root = tmp_path / "plm"
    data = {
        "plm_project": [{"key": "P-1", "label": "프로젝트"}],
        "plm_task": [{"key": "TK-001", "label": "알파", "properties": {"project": "P-1"}, "previous_keys": ["OLD-1"]},
                     {"key": "TK-003", "label": "감마_C2", "properties": {"project": "P-1"}},
                     {"key": "TK-009", "label": "감마 프로", "properties": {"project": "P-1"}}],
        "plm_model": [{"key": "SM-S938B_KOR_SKT", "properties": {"task": "TK-001", "base_code": "SM-S938B",
                                                                  "mass_model_code": "SM-S938N"}},
                      {"key": "SM-S928B_NA_C2_ATT", "properties": {"task": "TK-003", "base_code": "SM-S928B"}}],
    }
    assert P.write_snapshot(root, {"system": "t"}, data)["status"] == "ok"
    idx = P.PlmIndex(root / P.MIRROR)
    assert idx.by_code["tk-001"] == "TK-001" and idx.by_code["old-1"] == "TK-001"
    assert idx.by_name["감마"] == ["TK-003"] and idx.by_name["감마_c2"] == ["TK-003"], idx.by_name
    assert idx.by_model["sm-s938b_kor_skt"] == "SM-S938B_KOR_SKT" and idx.by_model["sm-s938n"] == "SM-S938B_KOR_SKT"
    assert idx.multi_names == ["감마 프로"]
    assert [m["key"] for m in idx.task_models("TK-001")] == ["SM-S938B_KOR_SKT"]


def test_mirror_cache_reopens_when_file_changes(tmp_path):
    root = tmp_path / "plm"
    assert P.mirror(root) is None
    P.write_snapshot(root, {}, {"plm_task": [{"key": "TK-1", "label": "a"}], "plm_model": [], "plm_project": []})
    assert len(P.mirror(root, check_every_s=0)) == 1
    P.write_snapshot(root, {}, {"plm_task": [{"key": f"TK-{i}", "label": "a"} for i in range(3)],
                                "plm_model": [], "plm_project": []})
    os.utime(root / P.MIRROR, (1, 1))                    # 같은 초에 두 번 쓰면 mtime 이 같을 수 있다
    assert len(P.mirror(root, check_every_s=0)) == 3


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


def test_rootdesign_style_key_is_name_code_in_task_code_and_aliases(tmp_path):
    """코어(rootdesign)는 plm_task key = 과제명, 과제코드는 properties.task_code·별칭, 개명은 renamed_from,
    개발모델 코드는 model_code 다(cae00 2026-10-27, SG-03·09·12)."""
    root = tmp_path / "plm"
    data = {
        "plm_project": [],
        "plm_task": [
            {"key": "감마_C2", "label": "감마_C2", "properties": {"task_code": "TK-77"},
             "aliases": [{"alias": "TK-77A"}, "GAMMA-C2"], "renamed_from": "감마_C1"},
            {"key": "델타", "label": "델타", "properties": {}}],          # task_code 없는 과제도 이름으로 찾힌다
        "plm_model": [{"key": "SM-S938N_KOR", "properties": {"task": "감마_C2", "model_code": "SM-S938N"}}],
    }
    P.write_snapshot(root, {}, data)
    idx = P.PlmIndex(root / P.MIRROR)
    assert idx.key_is_name
    assert idx.by_code["tk-77"] == "감마_C2" and idx.by_code["tk-77a"] == "감마_C2" and idx.by_code["gamma-c2"] == "감마_C2"
    assert "감마_c2" not in idx.by_code, "key(과제명)를 과제코드로 보지 않는다"
    assert idx.by_name["감마_c2"] == ["감마_C2"] and idx.by_name["감마_c1"] == ["감마_C2"] and idx.by_name["감마"] == ["감마_C2"]
    assert idx.by_name["델타"] == ["델타"]
    assert idx.models["SM-S938N_KOR"]["model_code"] == "SM-S938N" and idx.by_model["sm-s938n"] == "SM-S938N_KOR"
    assert idx.tasks["감마_C2"]["display"] == "TK-77(감마_C2)"


def test_old_mirror_file_without_aliases_column_still_reads(tmp_path):
    import sqlite3
    root = tmp_path / "plm"
    root.mkdir()
    con = sqlite3.connect(root / P.MIRROR)
    con.executescript("""CREATE TABLE objects (type TEXT, key TEXT, label TEXT, properties TEXT, previous_keys TEXT,
                         merged_into TEXT, updated_at TEXT, PRIMARY KEY (type, key));
                         CREATE TABLE meta (k TEXT PRIMARY KEY, v TEXT);
                         INSERT INTO objects VALUES ('plm_task','TK-1','알파','{}','[]',NULL,NULL);""")
    con.commit()
    con.close()
    idx = P.PlmIndex(root / P.MIRROR)
    assert idx.by_code["tk-1"] == "TK-1" and idx.tasks["TK-1"]["aliases"] == []
