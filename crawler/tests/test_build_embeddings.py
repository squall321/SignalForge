# 임베딩 적재가 실패한 묶음을 건너뛰지 않고, 빈 본문에서 제자리걸음하지 않는지 고정한다 — 맞춰 보기가 다른 DB 의 저장소를 가리는지도 본다.
"""이 프로젝트에서 반복된 고장 두 가지를 막는다.

  · 실패에 커서를 밀면 구멍이 영원히 남는다(백필이 실패해도 커서가 전진했다 — 649eb0c).
  · 아무것도 못 쓴 구간에서 커서가 안 움직이면 같은 구간을 영원히 다시 읽는다.
"""
import pathlib
import sys
import zlib
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import scripts.build_embeddings as be  # noqa: E402
from base import vector_store as vs  # noqa: E402


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


# ── 맞춰 보기 — 저장소가 이 DB 의 id 로 지은 것인가 (cae00 은 병합으로 받아 dev 와 id 가 다르다, 2026-11-11) ──
def _text_embed(texts):
    """본문마다 정해진 벡터 — 같은 글이면 같은 벡터, 다른 글이면 거의 직교."""
    return np.stack([np.random.default_rng(zlib.crc32(t.encode())).standard_normal(64)
                     for t in texts]).astype(np.float32)


def _store(tmp_path, monkeypatch):
    monkeypatch.setattr(vs, "SHARD", 10)                    # 조각 셋(1-10 · 11-20 · 21-30)
    monkeypatch.setattr(be, "embed_batch", _text_embed)
    s = vs.VectorStore(root=tmp_path)
    bodies = {i: f"본문 {i} — 배터리가 빨리 닳는다" for i in range(1, 31)}
    with s.writer():
        s.append(list(bodies), _text_embed(list(bodies.values())), 30, dim=64, chars=800)
    return s, bodies


def test_sample_reaches_middle_and_last_shard(tmp_path, monkeypatch):
    s, _ = _store(tmp_path, monkeypatch)
    ids = [i for i, _ in be.sample_ids(s)]
    assert ids[0] == 1 and ids[-1] == 30 and any(11 <= i <= 20 for i in ids)


def test_store_built_from_this_db_matches(tmp_path, monkeypatch):
    s, bodies = _store(tmp_path, monkeypatch)
    r = be.alignment(be.sample_ids(s), bodies)
    assert r["checked"] == 12 and r["same"] == 12 and not r["differ"] and not r["missing"]


def test_store_from_another_db_is_caught(tmp_path, monkeypatch):
    """dev 에서 옮겨 온 저장소 — 같은 id 에 다른 글. 뒤쪽만 갈라져도(덤프 복원 뒤 병합) 잡는다."""
    s, bodies = _store(tmp_path, monkeypatch)
    moved = {i: (t if i <= 20 else f"다른 글 {i}") for i, t in bodies.items()}
    r = be.alignment(be.sample_ids(s), moved)
    assert r["differ"] == [21, 24, 27, 30] and r["same"] == 8
    gone = {i: t for i, t in bodies.items() if i <= 20}      # 이 DB 에 없는 id
    assert be.alignment(be.sample_ids(s), gone)["missing"] == [21, 24, 27, 30]
