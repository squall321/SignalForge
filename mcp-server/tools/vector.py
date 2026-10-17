# 질의를 bge-m3 로 임베딩해 VOC 벡터 저장소에서 가까운 글을 찾는다 — 없거나 실패하면 빈손
"""벡터 검색.

저장소는 crawler/base/vector_store(조각 파일 + mmap). 적재는 crawler/scripts/build_embeddings.
질의 임베딩은 ollama(bge-m3)에 묻는다 — 첫 호출만 모델 적재로 1~3초, 이후 수십 ms.
한·영 교차가 된다(실측: '하루를 못 버텨요 배터리' → 'Battery drains so fast' 0.79).

저장소가 없거나(아직 적재 전이거나 색인을 만들지 않은 서버) ollama 가 없으면 None 을 돌려준다.
시맨틱 검색은 그때 사전·LLM 만으로 계속 간다.
"""
import logging
import os
import time
from collections import OrderedDict
from typing import Optional, Tuple

import httpx
import numpy as np

import ontology  # noqa: F401,E402 — /crawler 경로를 잡는다(base.vector_store)
from base.vector_store import VectorStore  # noqa: E402

log = logging.getLogger(__name__)

MODEL = os.getenv("EMB_MODEL", "bge-m3")
URL = os.getenv("EMB_URL", "http://127.0.0.1:11434").rstrip("/")
TIMEOUT = float(os.getenv("EMB_TIMEOUT", "8"))

_store: Optional[VectorStore] = None
_checked = 0.0
_qcache: "OrderedDict[str, np.ndarray]" = OrderedDict()


def store() -> Optional[VectorStore]:
    """저장소를 연다. manifest 가 바뀌면 다시 연다(30초마다만 확인)."""
    global _store, _checked
    if _store is None:
        _store = VectorStore(model=MODEL)
    now = time.monotonic()
    if now - _checked > 30 or not _store.count:
        _checked = now
        try:
            _store.load()
        except Exception as e:  # noqa: BLE001
            log.warning("벡터 저장소를 열지 못했다: %r", e)
            return None
    return _store if _store.count else None


async def embed_query(query: str) -> Optional[np.ndarray]:
    key = " ".join((query or "").split())
    if not key:
        return None
    if key in _qcache:
        _qcache.move_to_end(key)
        return _qcache[key]
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as c:
            r = await c.post(f"{URL}/api/embed",
                             json={"model": MODEL, "input": [key], "keep_alive": "30m"})
            r.raise_for_status()
            v = np.asarray(r.json()["embeddings"][0], dtype=np.float32)
    except Exception as e:  # noqa: BLE001
        log.warning("질의 임베딩 실패(%s): %r", MODEL, e)
        return None
    v /= (np.linalg.norm(v) or 1.0)
    _qcache[key] = v
    if len(_qcache) > 512:
        _qcache.popitem(last=False)
    return v


def search(qvec: np.ndarray, k: int, allowed: Optional[np.ndarray] = None
           ) -> Tuple[np.ndarray, np.ndarray]:
    st = store()
    if st is None or qvec is None:
        return np.array([], np.int64), np.array([], np.float32)
    return st.search(qvec, k=k, allowed=allowed)
