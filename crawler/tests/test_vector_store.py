# 벡터 저장소의 조각 쓰기·커서·범위 검색이 약속대로 동작하는지 고정한다 (실제 임베딩 없이).
import json
import pathlib
import sys

import numpy as np
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import base.vector_store as vs  # noqa: E402
from base.vector_store import VectorStore  # noqa: E402

DIM = 8


def _vecs(n, seed=0):
    return np.random.default_rng(seed).normal(size=(n, DIM)).astype(np.float32)


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(vs, "SHARD", 10)          # 조각 경계를 작게 — 덧붙이기·넘침을 본다
    return VectorStore(tmp_path, model="m")


def test_append_fills_tail_then_opens_new_shard(store):
    with store.writer():
        store.append(range(1, 8), _vecs(7), cursor=7, dim=DIM, chars=10)
        store.append(range(8, 21), _vecs(13, 1), cursor=25, dim=DIM, chars=10)
    man = json.loads(store.manifest_path.read_text())
    assert [s["n"] for s in man["shards"]] == [10, 10], "tail 을 채운 뒤 새 조각을 열어야 한다"
    assert man["count"] == 20 and man["cursor"] == 25
    assert man["shards"][0]["max_id"] == 10 and man["shards"][1]["min_id"] == 11


def test_cursor_only_advance_keeps_vectors(store):
    """빈 본문 구간을 지나갈 때 — 벡터는 그대로, 커서만 전진."""
    with store.writer():
        store.append([1, 2], _vecs(2), cursor=2, dim=DIM, chars=10)
        store.append([], np.zeros((0, DIM), np.float32), cursor=50, dim=DIM, chars=10)
    assert store.count == 2 and store.manifest["cursor"] == 50


def test_cursor_never_moves_back(store):
    with store.writer():
        store.append([5], _vecs(1), cursor=9, dim=DIM, chars=10)
        store.append([], np.zeros((0, DIM), np.float32), cursor=3, dim=DIM, chars=10)
    assert store.manifest["cursor"] == 9


def test_ids_must_increase(store):
    with store.writer():
        store.append([5, 6], _vecs(2), cursor=6, dim=DIM, chars=10)
        with pytest.raises(ValueError):
            store.append([6], _vecs(1), cursor=7, dim=DIM, chars=10)


def test_dimension_mismatch_is_refused(store):
    with store.writer():
        store.append([1], _vecs(1), cursor=1, dim=DIM, chars=10)
        with pytest.raises(ValueError):
            store.append([2], np.ones((1, 4), np.float32), cursor=2, dim=4, chars=10)


def test_search_matches_brute_force(store):
    v = _vecs(35, 3)
    with store.writer():
        store.append(range(100, 135), v, cursor=134, dim=DIM, chars=10)
    q = _vecs(1, 9)[0]
    ids, sims = store.search(q, k=5)
    vn = v / np.linalg.norm(v, axis=1, keepdims=True)
    exp = np.argsort(-(vn @ (q / np.linalg.norm(q))))[:5] + 100
    assert list(ids) == list(exp)
    assert np.all(np.diff(sims) <= 1e-6), "유사도 내림차순이어야 한다"


def test_search_restricted_to_allowed_ids(store):
    v = _vecs(35, 4)
    with store.writer():
        store.append(range(100, 135), v, cursor=134, dim=DIM, chars=10)
    allowed = np.array([101, 115, 129, 999], dtype=np.int64)   # 999 는 저장소에 없다
    ids, _ = store.search(_vecs(1, 5)[0], k=10, allowed=allowed)
    assert set(ids) == {101, 115, 129}, ids


def test_scoped_search_reads_in_chunks_and_matches_brute_force(store, monkeypatch):
    """범위 검색은 행을 나눠 복사해 읽는다 — 묶음 경계와 무관하게 전수 계산과 같아야 한다."""
    monkeypatch.setattr(vs, "_GATHER", 3)
    v = _vecs(35, 6)
    with store.writer():
        store.append(range(100, 135), v, cursor=134, dim=DIM, chars=10)
    allowed = np.arange(100, 135, 2, dtype=np.int64)            # 조각 4개에 걸친 18행
    q = _vecs(1, 7)[0]
    ids, sims = store.search(q, k=6, allowed=allowed)
    vn = v / np.linalg.norm(v, axis=1, keepdims=True)
    full = vn @ (q / np.linalg.norm(q))
    pick = allowed - 100
    exp = allowed[np.argsort(-full[pick])[:6]]
    assert list(ids) == list(exp)
    assert np.allclose(sims, np.sort(full[pick])[::-1][:6], atol=1e-6)


def test_reader_reloads_when_manifest_changes(store, tmp_path):
    reader = VectorStore(tmp_path, model="m")
    assert not reader.load()
    with store.writer():
        store.append([1, 2], _vecs(2), cursor=2, dim=DIM, chars=10)
    assert reader.load() and reader.count == 2


def test_second_writer_is_refused(store):
    """백그라운드 전량 실행과 주기 실행이 겹치면 하나는 비켜야 한다."""
    with store.writer():
        other = VectorStore(store.dir.parent, model="m")
        with pytest.raises(BlockingIOError):
            with other.writer():
                pass
