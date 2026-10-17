# 사전에 없는 표현(은유·완곡)을 로컬 LLM 으로 온톨로지 증상 키에 옮긴다 — 키는 검증하고, 실패하면 빈손
"""LLM 개념 추론.

온톨로지 사전은 '발열'·'뜨겁' 은 알아도 '폰이 손난로 같아요' 는 모른다. 임베딩도 은유에는
약했다(실측: 손난로 질의에서 발열 글이 4·5위, 유사도 차이 0.07). 로컬 qwen2.5:7b 에 증상 키
목록을 주고 고르게 하면 새 표현 홀드아웃 19/19, 평균 151ms 였다(2026-10-17).

원칙
  · 사전에서 증상을 하나도 못 찾았을 때만 부른다 — 사전이 이기면 LLM 은 필요 없다
  · 출력은 온톨로지 키로만 받는다(없는 키는 버림, 최대 2개). 개념을 지어내지 못하게
  · temperature 0 + 질의별 캐시 — 같은 질의에 같은 해석
  · 6초 안에 못 받거나 실패하면 빈손 — 검색은 사전·벡터만으로 계속 간다
"""
import json
import logging
import os
from collections import OrderedDict
from typing import List

import httpx

import ontology as O

log = logging.getLogger(__name__)

MODEL = os.getenv("CONCEPT_LLM_MODEL", "qwen2.5:7b")
URL = os.getenv("CONCEPT_LLM_URL", os.getenv("EMB_URL", "http://127.0.0.1:11434")).rstrip("/")
TIMEOUT = float(os.getenv("CONCEPT_LLM_TIMEOUT", "6"))
ENABLED = os.getenv("CONCEPT_LLM", "1") != "0"

_EXAMPLES = [
    ("폰이 손난로 같아요", ["overheat"]),
    ("doesn't last a day", ["drain"]),
    ("화면에 초록 실선", ["green_line"]),
    ("버튼을 눌러도 아무 반응이 없어요", ["not_working"]),
    ("렌즈 안에 먼지 같은 게 보여요", ["dust_ingress"]),
    ("카톡 알림이 늦게 와요", []),
    ("가격이 비싸요", []),
    ("언제 출시돼요", []),
]


def system_prompt() -> str:
    keys = "\n".join(
        f"- {k}: {v[0]} / {v[1]}" + (f" — {O.SYMPTOM_HINTS[k]}" if k in O.SYMPTOM_HINTS else "")
        for k, v in O.SYMPTOM_LABELS.items())
    ex = "\n".join(f"'{q}' -> {json.dumps(a)}" for q, a in _EXAMPLES)
    return ("You map a user's search about a phone, tablet, watch or earbuds to defect symptom keys.\n"
            'Return JSON {"symptoms": [keys]} using ONLY these keys:\n' + keys +
            "\nUse at most 2 keys. If the text is not about a defect or matches none, "
            'return {"symptoms": []}.\nInterpret metaphors and everyday phrasing. Examples:\n' + ex)


def parse(content: str) -> List[str]:
    """LLM 응답 → 검증된 키 목록. 깨진 JSON·없는 키·3개 이상은 걸러낸다."""
    try:
        got = json.loads(content).get("symptoms", [])
    except (ValueError, AttributeError):
        return []
    if not isinstance(got, list):
        return []
    out = []
    for k in got:
        if isinstance(k, str) and k in O.SYMPTOM_LABELS and k not in out:
            out.append(k)
    return out[:2]


_cache: "OrderedDict[str, List[str]]" = OrderedDict()
_CACHE_MAX = 512


async def infer_symptoms(query: str) -> List[str]:
    if not ENABLED or not (query or "").strip():
        return []
    key = O.normalize(query)
    if key in _cache:
        _cache.move_to_end(key)
        return list(_cache[key])
    body = {"model": MODEL, "stream": False, "format": "json", "keep_alive": "30m",
            "options": {"temperature": 0, "num_predict": 60},
            "messages": [{"role": "system", "content": system_prompt()},
                         {"role": "user", "content": query}]}
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as c:
            r = await c.post(f"{URL}/api/chat", json=body)
            r.raise_for_status()
            keys = parse(r.json()["message"]["content"])
    except Exception as e:  # noqa: BLE001 — 추론은 덤이다. 실패해도 검색은 계속
        log.warning("LLM 개념 추론 실패(%s): %r", MODEL, e)
        return []
    _cache[key] = keys
    if len(_cache) > _CACHE_MAX:
        _cache.popitem(last=False)
    return list(keys)
