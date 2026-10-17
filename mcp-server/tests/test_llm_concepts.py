# LLM 개념 추론의 응답 검증(없는 키·깨진 JSON·과다 키)과 프롬프트 구성을 고정한다 (LLM 불필요).
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
for p in ("/shared", str(ROOT.parent / "shared")):
    if p not in sys.path:
        sys.path.insert(0, p)
sys.path.insert(0, str(ROOT))
pytest.importorskip("ontology", reason="crawler 사전을 import 할 수 없음 — /crawler 바인드 필요")

from tools.llm_concepts import parse, system_prompt  # noqa: E402


@pytest.mark.parametrize("content,expected", [
    ('{"symptoms": ["overheat"]}', ["overheat"]),
    ('{"symptoms": ["overheat", "made_up_key"]}', ["overheat"]),     # 지어낸 개념은 버린다
    ('{"symptoms": ["drain", "drain", "lag", "crash"]}', ["drain", "lag"]),   # 중복 제거·최대 2개
    ('{"symptoms": []}', []),
    ('{"symptoms": "overheat"}', []),                                # 목록이 아니면 버린다
    ("not json at all", []),
    ('{"other": 1}', []),
])
def test_parse_only_accepts_ontology_keys(content, expected):
    assert parse(content) == expected


def test_prompt_lists_every_symptom_and_hints():
    import ontology as O
    sp = system_prompt()
    assert all(f"- {k}:" in sp for k in O.SYMPTOM_LABELS), "프롬프트에 빠진 증상 키가 있다"
    assert "INSIDE" in sp and "APP closes" in sp, "헷갈리는 쌍을 가르는 설명이 빠졌다"
