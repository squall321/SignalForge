# PLM 프로젝트가 다른 모델·마케팅명이 한 제품 코드로 풀릴 때 해석·resolve 가 주의를 싣는지(SG-04) 가짜 거울로 고정한다
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
for p in ("/shared", str(ROOT.parent / "shared"), "/crawler", str(ROOT.parent / "crawler")):
    if p not in sys.path:
        sys.path.insert(0, p)
sys.path.insert(0, str(ROOT))
pytest.importorskip("ontology", reason="crawler 사전을 import 할 수 없음 — /crawler 바인드 필요")

import ontology as O  # noqa: E402
from base import plm_mirror as P  # noqa: E402


def _mirror(tmp_path, a_model_code="SM-F976U"):
    """프로젝트 A(P8 — 마케팅명 'Galaxy Z Fold8 Wide')와 B(PH8 — 'Galaxy Z Fold8'). 코어 꼴 — 별칭은 properties.aliases,
    과제 key 는 과제명, 과제코드는 task_code, 과제→프로젝트는 properties.project, 개발모델 코드는 model_code."""
    data = {
        "plm_project": [
            {"key": "PRJ-A", "label": "P8", "properties": {"aliases": ["Galaxy Z Fold8 Wide", "갤럭시 Z 폴드8 와이드"]}},
            {"key": "PRJ-B", "label": "PH8", "properties": {"aliases": ["Galaxy Z Fold8"]},
             "previous_keys": ["PRJ-B-OLD"]}],
        "plm_task": [
            {"key": "과제A", "label": "과제A", "properties": {"task_code": "TK-A", "project": "PRJ-A",
                                                          "aliases": ["TKA-ALIAS"]}},
            {"key": "과제B", "label": "과제B", "properties": {"task_code": "TK-B", "project": "PRJ-B-OLD"}}],
        "plm_model": [
            {"key": f"{a_model_code}_A", "properties": {"task": "과제A", "model_code": a_model_code}},
            {"key": "SM-F976B_B", "properties": {"task": "과제B", "model_code": "SM-F976B"}}],
    }
    root = tmp_path / "plm"
    P.write_snapshot(root, {}, data)
    return P.PlmIndex(root / P.MIRROR)


def test_index_reads_core_aliases_projects_and_old_project_keys(tmp_path):
    idx = _mirror(tmp_path)
    assert idx.by_code["tka-alias"] == "과제A", "코어는 별칭을 properties.aliases 에 둔다"
    assert idx.project_names["galaxy z fold8 wide"] == ["PRJ-A"] and idx.project_names["galaxy z fold8"] == ["PRJ-B"]
    assert idx.project_of("과제A") == "PRJ-A" and idx.project_of("과제B") == "PRJ-B", "옛 프로젝트 key 도 지금 key 로"


def test_product_shared_by_two_projects_is_flagged(tmp_path):
    """두 프로젝트의 개발모델이 같은 기본 코드(F976 → GZF8) — GZF8 의 VOC 는 두 프로젝트를 가르지 않고 센다."""
    idx = _mirror(tmp_path)
    it = O.interpret("Galaxy Z Fold8 Wide 힌지 불만", plm=idx)
    assert "GZF8" in it.products
    c = it.plm_caution
    assert c and c["products"]["GZF8"] == ["P8", "PH8"] and "P8" in c["named_projects"] and not c["foreign_projects"]
    assert any(n.startswith("주의 — ") and "2개" in n for n in it.notes), it.notes
    it2 = O.interpret("SM-F976U 발열", plm=idx)                     # 모델코드로 짚어도 같다
    assert it2.plm_caution and it2.plm_caution["products"]["GZF8"] == ["P8", "PH8"]


def test_name_of_another_project_is_flagged_and_own_name_is_not(tmp_path):
    """A 의 모델이 사전에 없는 코드라 GZF8 은 B 것뿐 — A 의 마케팅명을 물으면 '다른 프로젝트' 주의, B 의 것을 물으면 조용."""
    idx = _mirror(tmp_path, a_model_code="SM-F971U")
    it = O.interpret("Galaxy Z Fold8 Wide 힌지", plm=idx)
    assert "GZF8" in it.products
    c = it.plm_caution
    assert c and c["foreign_projects"] == ["P8"] and "프로젝트 PH8 것이다" in c["note"], c
    assert O.interpret("Galaxy Z Fold8 힌지", plm=idx).plm_caution is None
    assert O.interpret("Galaxy Z Fold8 Wide 힌지", plm=None).plm_caution is None, "거울이 없으면 주의도 없다"


def test_resolve_carries_the_caution(tmp_path, monkeypatch):
    from tools import query as Q
    idx = _mirror(tmp_path, a_model_code="SM-F971U")
    monkeypatch.setattr(P, "mirror", lambda *a, **k: idx)
    c = Q._plm_caution_for("Galaxy Z Fold8 Wide", ["GZF8"])
    assert c and c["foreign_projects"] == ["P8"]
    assert Q._plm_caution_for("Galaxy Z Fold8", ["GZF8"]) is None
