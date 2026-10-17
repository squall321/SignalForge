# 임베딩 적재가 실패한 묶음을 건너뛰지 않고, 빈 본문에서 제자리걸음하지 않는지 고정한다.
"""이 프로젝트에서 반복된 고장 두 가지를 막는다.

  · 실패에 커서를 밀면 구멍이 영원히 남는다(백필이 실패해도 커서가 전진했다 — 649eb0c).
  · 아무것도 못 쓴 구간에서 커서가 안 움직이면 같은 구간을 영원히 다시 읽는다.
"""
import pathlib
import sys
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import scripts.build_embeddings as be  # noqa: E402


def _rows(spec):
    return [SimpleNamespace(id=i, body=t) for i, t in spec]


def _fake_embed(fail_on=None):
    def f(texts):
        if fail_on and fail_on in texts:
            raise RuntimeError("boom")
        return np.ones((len(texts), 4), np.float32)
    return f


def test_all_ok_cursor_is_last_row(monkeypatch):
    monkeypatch.setattr(be, "BATCH", 2)
    monkeypatch.setattr(be, "embed_batch", _fake_embed())
    with ThreadPoolExecutor(2) as pool:
        ids, vecs, ok = be.embed_rows(_rows([(1, "a"), (2, "b"), (3, ""), (4, "c")]), pool)
    assert ids == [1, 2, 4] and vecs.shape == (3, 4)
    assert ok == 4, "빈 본문(3)은 벡터 없이 지나가야 한다"


def test_failed_batch_stops_cursor_before_it(monkeypatch):
    monkeypatch.setattr(be, "BATCH", 2)
    monkeypatch.setattr(be, "embed_batch", _fake_embed(fail_on="c"))
    with ThreadPoolExecutor(2) as pool:
        ids, vecs, ok = be.embed_rows(_rows([(1, "a"), (2, "b"), (5, "c"), (6, "d"), (9, "e")]), pool)
    assert ids == [1, 2], "실패 묶음 뒤의 성공분을 받으면 구멍이 생긴다"
    assert ok == 4, "실패한 묶음의 첫 id(5) 앞까지만 전진해야 한다"


def test_whitespace_only_is_empty(monkeypatch):
    monkeypatch.setattr(be, "embed_batch", _fake_embed())
    with ThreadPoolExecutor(1) as pool:
        ids, _, ok = be.embed_rows(_rows([(7, "  \n\t "), (8, "x")]), pool)
    assert ids == [8] and ok == 8
