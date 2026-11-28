# MCP 공통 계약(포털 docs/mcp-modernize/contract.md)의 stateless · 인자 설명 · 읽기 표지를 tools/list JSON 으로 고정한다
"""왜 이 시험인가 (2026-11-28).

게이트웨이에는 앱 도구가 2,000개 가까이 모이고 모델은 이름 · 설명 · 인자 스키마로만 고른다. 인자 설명이 산문 「Args:」 에만
있으면 스키마에는 빈칸이고, 읽기 표지가 없으면 읽기 도구를 쓰기로 막는다. SDK 1.x 의 표지 칸은 camelCase 라 read_only_hint=
로 쓰면 예외도 경고도 없이 모르는 칸으로 나간다 — 파이썬 속성이 아니라 tools/list 로 나가는 JSON 의 열쇠를 본다.
"""
import asyncio
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
pytest.importorskip("mcp", reason="mcp SDK 가 없는 환경")


@pytest.fixture(scope="module")
def server():
    import server as srv          # conftest 가 DATABASE_URL 을 정한다 — 접속은 하지 않는다
    return srv


@pytest.fixture(scope="module")
def tools(server):
    listed = asyncio.run(server.mcp.list_tools())
    return {t.name: t.model_dump(mode="json", by_alias=True, exclude_none=True) for t in listed}


def test_server_is_stateless(server):
    assert server.mcp.settings.stateless_http is True


def test_every_tool_carries_camel_case_read_only_annotations(tools):
    assert len(tools) >= 37
    for name, t in tools.items():
        ann = t.get("annotations") or {}
        assert ann.get("readOnlyHint") is True and ann.get("openWorldHint") is False, (name, ann)
        assert not any("_" in k for k in ann), f"{name}: 표지 열쇠가 camelCase 가 아니다 {sorted(ann)}"


def test_every_argument_has_a_schema_description_and_no_prose_args(tools):
    missing = [(n, k) for n, t in tools.items() for k, v in t["inputSchema"].get("properties", {}).items()
               if not v.get("description")]
    assert not missing, missing
    assert not [n for n, t in tools.items() if "Args:" in t["description"]], "설명에 산문 Args 절이 남았다"


def test_args_parser_keeps_continuations_and_skips_shorthand_lines(server):
    doc = """한 줄 설명.

    Args:
        interval: day | week | month
            (기본 week)
        나머지: 공통 필터
        limit: 상위 N개

    반환에 total 이 있다.
    """
    desc, args = server._split_args(doc)
    assert args == {"interval": "day | week | month (기본 week)", "limit": "상위 N개"}
    assert desc == "한 줄 설명.\n\n반환에 total 이 있다."
