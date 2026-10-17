# 활성 VOC 본문을 bge-m3 로 임베딩해 벡터 저장소에 덧붙인다 — 커서는 실제로 쓴 만큼만 전진
"""임베딩 적재.

실행:  DATABASE_URL=... python3 scripts/build_embeddings.py
환경변수
  EMB_MODEL      임베딩 모델 (기본 bge-m3 — 한·영 교차 검색 실측 0.79)
  EMB_URL        ollama 주소 (기본 http://127.0.0.1:11434)
  EMB_DIR        저장소 루트 (기본 crawler/data/vectors)
  EMB_CHARS      본문 앞 몇 자를 넣나 (기본 800 — 500 으로 줄여도 5% 빨라질 뿐)
  EMB_BATCH      요청당 문서 수 (기본 64)
  EMB_WORKERS    동시 요청 수 (기본 4 — 1개 165건/초 → 4개 200건/초)
  EMB_FLUSH      몇 행마다 디스크에 쓰나 (기본 20000)
  EMB_BUDGET_SEC 이번 실행 시간 상한 (0=무제한). 주기 실행은 상한을 둔다.
  EMB_LIMIT      이번 실행 최대 행 수 (0=무제한)

커서는 manifest 의 cursor 다. 묶음 하나라도 임베딩에 실패하면 그 앞까지만 쓰고 멈춘다 —
다음 실행이 실패한 지점부터 다시 한다. 빈 본문은 벡터 없이 건너뛰되 커서는 지나간다
(그러지 않으면 빈 본문만 있는 구간에서 영원히 제자리다).
"""
import asyncio
import json
import logging
import os
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from typing import List

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402

from base.vector_store import VectorStore  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("build_embeddings")

MODEL = os.getenv("EMB_MODEL", "bge-m3")
URL = os.getenv("EMB_URL", "http://127.0.0.1:11434").rstrip("/")
CHARS = int(os.getenv("EMB_CHARS", "800"))
BATCH = int(os.getenv("EMB_BATCH", "64"))
WORKERS = int(os.getenv("EMB_WORKERS", "4"))
FLUSH = int(os.getenv("EMB_FLUSH", "20000"))
BUDGET = float(os.getenv("EMB_BUDGET_SEC", "0"))
LIMIT = int(os.getenv("EMB_LIMIT", "0"))
FETCH = 2000

SELECT_SQL = text("""
    SELECT id, LEFT(content_original, :chars) AS body   -- 't' 는 안 된다: SQLAlchemy 2 Row.t 가 가린다
    FROM voc_records
    WHERE archived_at IS NULL AND content_original IS NOT NULL AND id > :after
    ORDER BY id LIMIT :n
""")


def embed_batch(texts: List[str]) -> np.ndarray:
    body = json.dumps({"model": MODEL, "input": texts, "keep_alive": "30m"}).encode()
    req = urllib.request.Request(f"{URL}/api/embed", data=body,
                                 headers={"Content-Type": "application/json"})
    last = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=300) as r:
                out = json.loads(r.read())["embeddings"]
            if len(out) != len(texts):
                raise ValueError(f"임베딩 수가 다르다 {len(out)} != {len(texts)}")
            return np.asarray(out, dtype=np.float32)
        except (urllib.error.URLError, ValueError, KeyError, TimeoutError) as e:
            last = e
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"임베딩 실패(3회): {last!r}")


def clean(t: str) -> str:
    return " ".join((t or "").split())


def embed_rows(rows, pool) -> tuple:
    """rows → (ids, vecs, 마지막으로 처리한 id). 실패한 묶음이 있으면 그 앞까지만."""
    keep = [(r.id, clean(r.body)) for r in rows]
    nonempty = [(i, t) for i, t in keep if t]
    batches = [nonempty[i:i + BATCH] for i in range(0, len(nonempty), BATCH)]
    futs = [pool.submit(embed_batch, [t for _, t in b]) for b in batches]
    ids, vecs, ok_until = [], [], rows[-1].id
    for b, f in zip(batches, futs):
        try:
            v = f.result()
        except Exception as e:  # noqa: BLE001
            # 이 묶음 앞까지만 인정한다 — 빈 본문 건너뛰기도 이 지점에서 멈춘다
            ok_until = b[0][0] - 1
            log.warning("임베딩 실패 — id %s 앞까지만 쓴다: %s", b[0][0], e)
            for g in futs:
                g.cancel()
            break
        ids.extend(i for i, _ in b)
        vecs.append(v)
    return ids, (np.concatenate(vecs) if vecs else np.zeros((0, 0), np.float32)), ok_until


async def main() -> dict:
    db = os.getenv("DATABASE_URL", "")
    if not db:
        log.error("DATABASE_URL 미설정")
        return {"error": "no DATABASE_URL"}
    store = VectorStore(model=MODEL)
    t0 = time.monotonic()
    done = written = 0
    stopped = None
    engine = create_async_engine(db, pool_pre_ping=True)
    try:
        with store.writer():
            after = int(store.manifest.get("cursor", 0))
            dim = store.manifest.get("dim")
            log.info("시작 — 저장소 %s · 기존 %s건 · 커서 %s", store.dir, store.count, after)
            buf_ids, buf_vecs, buf_cursor = [], [], after
            with ThreadPoolExecutor(WORKERS) as pool:
                while True:
                    if BUDGET and time.monotonic() - t0 > BUDGET:
                        stopped = "budget"
                        break
                    if LIMIT and done >= LIMIT:
                        stopped = "limit"
                        break
                    async with engine.connect() as c:
                        rows = (await c.execute(SELECT_SQL, {"after": after, "n": FETCH,
                                                             "chars": CHARS})).all()
                    if not rows:
                        stopped = "end"
                        break
                    ids, vecs, ok_until = embed_rows(rows, pool)
                    if len(ids):
                        dim = dim or int(vecs.shape[1])
                        buf_ids.extend(ids)
                        buf_vecs.append(vecs)
                    buf_cursor = after = ok_until
                    done += len(rows)
                    if ok_until < rows[-1].id:
                        stopped = "embed_error"
                    if sum(len(v) for v in buf_vecs) >= FLUSH or stopped:
                        if buf_vecs:
                            store.append(buf_ids, np.concatenate(buf_vecs), buf_cursor, dim=dim, chars=CHARS)
                            written += len(buf_ids)
                            buf_ids, buf_vecs = [], []
                        log.info("  누적 %s건 처리 · %s건 저장 · 커서 %s · %.0f건/초",
                                 done, written, buf_cursor, done / max(1e-6, time.monotonic() - t0))
                    if stopped:
                        break
            if buf_vecs:
                store.append(buf_ids, np.concatenate(buf_vecs), buf_cursor, dim=dim, chars=CHARS)
                written += len(buf_ids)
            elif dim and buf_cursor > int(store.manifest.get("cursor", 0)):
                store.append([], np.zeros((0, dim), np.float32), buf_cursor, dim=dim, chars=CHARS)
    except BlockingIOError:
        log.info("다른 적재가 돌고 있다 — 이번 실행은 건너뛴다")
        return {"skipped": "locked"}
    finally:
        await engine.dispose()
    res = {"scanned": done, "written": written, "total": store.count,
           "cursor": int(store.manifest.get("cursor", 0)), "stopped": stopped,
           "elapsed_sec": round(time.monotonic() - t0, 1)}
    log.info("완료 %s", res)
    return res


if __name__ == "__main__":
    r = asyncio.run(main())
    sys.exit(1 if r.get("stopped") == "embed_error" or r.get("error") else 0)
