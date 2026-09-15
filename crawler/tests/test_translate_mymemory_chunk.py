# MyMemory 폴백이 500자 상한 때문에 긴 글을 통째로 버리지 않는지 검증한다.
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from nlp import translator as T


def test_split_chunks_never_exceeds_size():
    text = ("Ala ma kota. " * 400)          # 약 5,200자
    for size in (500, 2000):
        for c in T._split_chunks(text, size):
            assert len(c) <= size, f"청크가 {size}자를 넘는다: {len(c)}"


def test_split_chunks_loses_nothing():
    text = "abc def ghi " * 100
    assert "".join(T._split_chunks(text, 500)) == text


def test_mymemory_chunk_is_under_api_limit():
    """MyMemory 는 500자를 넘기면 거부한다. Google 기준 _CHUNK 를 쓰면 안 된다."""
    assert T._MYMEMORY_CHUNK <= 500
    assert T._MYMEMORY_CHUNK < T._CHUNK


@pytest.mark.asyncio
async def test_long_text_falls_back_in_500_char_pieces(monkeypatch):
    """Google 이 죽어도 MyMemory 가 500자씩 나눠 전부 번역해야 한다."""
    long_pl = ("Nowe panele OLED trafia do laptopow premium. " * 40)   # 약 1,800자
    seen = []

    class _Boom:
        def __init__(self, *a, **kw):
            pass

        def translate(self, text):
            raise RuntimeError("google down")

    class _MM:
        def __init__(self, *a, **kw):
            pass

        def translate(self, text):
            seen.append(len(text))
            if len(text) > 500:
                raise ValueError(
                    "Text length need to be between 0 and 500 characters")
            return "TRANSLATED "

    monkeypatch.setattr(T, "GoogleTranslator", _Boom)
    monkeypatch.setattr(T, "MyMemoryTranslator", _MM)
    monkeypatch.setattr(T, "MYMEMORY_MAP", {"pl": "pl-PL"})
    monkeypatch.setattr(T, "_throttle", lambda: __import__("asyncio").sleep(0))

    out = await T._translate_chunk(long_pl, "pl", "pl")

    assert seen, "MyMemory 를 부르지도 않았다"
    assert max(seen) <= 500, f"500자 초과 요청이 있다: {max(seen)}"
    assert out != long_pl, "번역되지 않고 원문이 그대로 남았다"
    assert "TRANSLATED" in out
