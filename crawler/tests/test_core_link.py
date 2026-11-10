# 코어와 잇기 — 정확 일치만 rule 선 · 짐작은 후보 · 사람 선은 다시 지어도 남고 바뀐 코어를 따라가거나 '사용 안 함 · 확인 필요' 가 된다
import importlib.util
import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from base import core_link as C  # noqa: E402
from base import plm_mirror as P  # noqa: E402

NOW = "2026-11-10T00:00:00+00:00"
SF = {"part": {"battery": ["battery", "배터리", "Battery"], "charging_port": ["charging_port", "충전 포트", "Charging port"],
               "hinge": ["hinge", "힌지", "Hinge"], "s_pen": ["s_pen", "S펜", "S Pen"]},
      "symptom": {"fire": ["fire", "발화·폭발", "발화", "폭발", "Fire"], "swelling": ["swelling", "배터리 부풀음", "Battery swelling"],
                  "lag": ["lag", "버벅임", "Lag"]}}


def _obj(key, label=None, deleted=False, merged_into=None, previous_keys=(), **props):
    return {"key": key, "label": label or key, "deleted": deleted, "merged_into": merged_into,
            "properties": props, "previous_keys": list(previous_keys)}


CORE = {"part": [_obj("BATT", "배터리"), _obj("PORT-USB", "USB 포트", aliases=["charging port"]),
                 _obj("HNG-A", "힌지 A", aliases=["hinge"]), _obj("HNG-B", "힌지 B", aliases=["hinge"])],
        "failure_mode": [_obj("FM-IGN", "발화"), _obj("FM-SWELL", "battery swell")]}


def _rule(rows, kind, sf_key):
    return next(r for r in rows if r["kind"] == kind and r["sf_key"] == sf_key)


def test_only_an_exact_name_that_points_at_one_core_object_becomes_a_rule_line():
    rows, review = C.build(SF, CORE, [], NOW)
    assert _rule(rows, "part", "battery")["core_key"] == "BATT"                    # 한국어 이름 '배터리' 가 label 과 같다
    assert _rule(rows, "part", "charging_port")["core_key"] == "PORT-USB"          # key 'charging_port' = 별칭 'charging port'
    assert _rule(rows, "symptom", "fire")["core_key"] == "FM-IGN"                  # '발화·폭발' 의 낱말 '발화'
    assert all(r["origin"] == "rule" for r in rows)
    cands = {c["sf_key"]: c for c in review["candidates"]}
    assert cands["hinge"]["why"] == "ambiguous" and {o["core_key"] for o in cands["hinge"]["options"]} == {"HNG-A", "HNG-B"}
    assert cands["swelling"]["why"] == "no_exact" and cands["swelling"]["options"][0]["core_key"] == "FM-SWELL", \
        "가까운 이름은 후보로만 — 잇지 않는다"
    assert cands["s_pen"]["options"] == [] and cands["lag"]["why"] == "no_exact"
    assert review["counts"]["rule"] == 3 and review["counts"]["candidates"] == 4


def test_rebuild_replaces_rule_lines_and_keeps_human_lines():
    human = {"kind": "part", "sf_key": "hinge", "core_type": "part", "core_key": "HNG-B", "origin": "human",
             "status": "active", "at": NOW}
    none = {"kind": "symptom", "sf_key": "lag", "core_type": "failure_mode", "core_key": None, "origin": "human",
            "reason": "성능 체감 — 코어 고장 모드에 맞는 것이 없다", "status": "active", "at": NOW}
    stale_rule = {"kind": "part", "sf_key": "battery", "core_type": "part", "core_key": "OLD", "origin": "rule"}
    rows, review = C.build(SF, CORE, [human, none, stale_rule], NOW)
    assert _rule(rows, "part", "hinge") == human, "사람이 고른 것은 다시 지어도 그대로다"
    assert _rule(rows, "symptom", "lag")["core_key"] is None and _rule(rows, "symptom", "lag")["origin"] == "human"
    assert _rule(rows, "part", "battery")["core_key"] == "BATT", "옛 rule 선은 버리고 지금 코어로 다시 짓는다"
    assert "hinge" not in {c["sf_key"] for c in review["candidates"]}
    assert review["counts"]["human"] == 2 and review["counts"]["human_none"] == 1


def test_human_line_follows_a_renamed_key_and_marks_deleted_or_merged_keys():
    core = {"part": [_obj("BATT-2", "배터리", previous_keys=["BATT"]), _obj("PORT-USB", deleted=True),
                     _obj("HNG-A", deleted=True, merged_into="HNG-X"), _obj("HNG-X", "힌지")],
            "failure_mode": []}
    current = [{"kind": "part", "sf_key": k, "core_type": "part", "core_key": v, "origin": "human", "status": "active"}
               for k, v in (("battery", "BATT"), ("charging_port", "PORT-USB"), ("hinge", "HNG-A"))]
    rows, review = C.build(SF, core, current, NOW)
    batt, port, hinge = (_rule(rows, "part", k) for k in ("battery", "charging_port", "hinge"))
    assert (batt["core_key"], batt["status"], batt["renamed_from"]) == ("BATT-2", "active", "BATT"), "같은 객체 — key 만 바뀌었다"
    assert port["status"] == "unused" and port["core_key"] == "PORT-USB", "사라졌다 — 지우지 않고 '사용 안 함'"
    assert (hinge["status"], hinge["merged_into"], hinge["core_key"]) == ("check", "HNG-X", "HNG-A"), \
        "합쳐졌다 — 사람 선은 저절로 옮기지 않고 '확인 필요'"
    assert [r["sf_key"] for r in review["check"]] == ["hinge"] and [r["sf_key"] for r in review["unused"]] == ["charging_port"]


def test_human_line_for_a_concept_signalforge_dropped_is_kept_as_unused():
    gone = {"kind": "part", "sf_key": "old_part", "core_type": "part", "core_key": "BATT", "origin": "human", "status": "active"}
    rows, _ = C.build(SF, CORE, [gone], NOW)
    assert _rule(rows, "part", "old_part")["status"] == "unused"


@pytest.fixture
def kit():
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


def test_rebuild_reads_the_kit_replica_and_keeps_human_lines_across_runs(tmp_path, kit):
    root = tmp_path / "plm"
    assert C.rebuild(root, SF)["status"] == "no_replica"
    cfg = {"out_dir": root / P.OUT}
    kit.save_rows(cfg, "plm_task", [{"key": "TK-1", "label": "알파"}])
    assert C.rebuild(root, SF)["status"] == "no_core_types" and not C.link_dir(root).exists(), "코어 타입이 없으면 쓰지 않는다"
    kit.save_rows(cfg, "part", [{"key": "BATT", "label": "배터리"}])
    kit.save_rows(cfg, "failure_mode", [{"key": "FM-IGN", "label": "발화"}])
    first = C.rebuild(root, SF)
    assert first["status"] == "ok" and first["rule"] == 2 and first["core"] == {"part": 1, "failure_mode": 1}
    rows = C.load_map(root)
    rows.append({"kind": "part", "sf_key": "hinge", "core_type": "part", "core_key": None, "origin": "human",
                 "reason": "코어에 힌지가 없다", "status": "active"})
    C.save_map(rows, root)
    second = C.rebuild(root, SF)
    assert second["human"] == 1 and _rule(C.load_map(root), "part", "hinge")["reason"] == "코어에 힌지가 없다"
    review = json.loads((C.link_dir(root) / C.REVIEW).read_text(encoding="utf-8"))
    assert review["counts"]["rule"] == 2 and "hinge" not in {c["sf_key"] for c in review["candidates"]}


def test_signalforge_vocabulary_comes_from_the_shared_ontology():
    sf = C.sf_vocab()
    assert len(sf["part"]) == 16 and len(sf["symptom"]) == 32
    assert sf["part"]["battery"] == ["battery", "배터리"], "영어 이름 'Battery' 는 key 와 같은 이름이라 한 번만"
    assert {"발화", "폭발"} <= set(sf["symptom"]["fire"]), "가운뎃점으로 묶은 이름은 낱말로도"


def test_operator_edits_replace_the_concept_line_and_need_a_reason_for_no_match():
    rows = [{"kind": "part", "sf_key": "battery", "core_type": "part", "core_key": "BATT", "origin": "rule"}]
    rows = C.set_human(rows, "part", "battery", "BATT-X", now=NOW)
    assert [(r["core_key"], r["origin"]) for r in rows] == [("BATT-X", "human")], "같은 개념의 rule 선은 빠진다"
    with pytest.raises(ValueError):
        C.set_human(rows, "symptom", "lag", None)                      # 맞는 것이 없다 — 까닭 없이는 안 된다
    with pytest.raises(ValueError):
        C.set_human(rows, "topic", "price", "X")
    rows = C.set_human(rows, "symptom", "lag", None, "성능 체감", now=NOW)
    assert rows[-1]["core_type"] == "failure_mode" and rows[-1]["reason"] == "성능 체감"
    assert [r["sf_key"] for r in C.unset_human(rows, "part", "battery")] == ["lag"]


def test_after_sync_runs_only_after_a_finished_pull_and_reports_failures(tmp_path, monkeypatch):
    assert C.after_sync({"status": "error"}, tmp_path) is None and C.after_sync({"status": "not_configured"}, tmp_path) is None
    assert C.after_sync({"status": "ok"}, tmp_path) == {"status": "no_replica"}

    def boom(root=None, sf=None):
        raise ImportError("No module named 'ontology'")
    monkeypatch.setattr(C, "rebuild", boom)
    got = C.after_sync({"status": "check"}, tmp_path)
    assert got["status"] == "error" and "ontology" in got["error"], "/shared 가 없으면 조용히 넘어가지 않는다"
