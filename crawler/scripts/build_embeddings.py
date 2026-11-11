# 활성 VOC 본문을 bge-m3 로 임베딩해 벡터 저장소에 덧붙인다 — 커서는 실제로 쓴 만큼만 전진
"""임베딩 적재.

실행:  DATABASE_URL=... python3 scripts/build_embeddings.py
       DATABASE_URL=... python3 scripts/build_embeddings.py --status   # 읽기만 — 남은 행 · 이 DB 로 지은 저장소인가
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


def sample_ids(store: VectorStore, per_shard: int = 4) -> list:
    """첫 · 가운데 · 마지막 조각에서 고르게 고른 (id, 저장된 벡터) — 덤프 복원 뒤 갈라진 뒤쪽 구간까지 보도록."""
    sh = store._shards
    picks = {}
    for ids, vec in (sh[0], sh[len(sh) // 2], sh[-1]):
        for pos in np.linspace(0, len(ids) - 1, per_shard).astype(int):
            picks[int(ids[pos])] = np.asarray(vec[pos])
    return sorted(picks.items())


def alignment(picks: list, bodies: dict, min_cos: float = 0.98) -> dict:
    """저장된 벡터가 지금 DB 의 같은 id 본문에서 나온 것인가.

    벡터는 voc_records.id 로 붙는다 — id 가 다른 서버에서 옮겨 온 저장소면(병합으로 받는 cae00 은 dev 와 id 가 다르다)
    다른 글에 엉뚱한 벡터가 붙는다. 같은 본문을 다시 임베딩하면 코사인이 1 에 가깝고 다른 글이면 한참 낮다.
    DB 에 없는 id 는 missing. 본문이 바뀐 행도 differ 로 나온다 — 드물고, 그때 다시 채우는 쪽이 안전하다.
    """
    missing = [i for i, _ in picks if i not in bodies]
    have = [(i, v, clean(bodies[i])) for i, v in picks if i in bodies and clean(bodies[i])]
    out = {"checked": len(have), "same": 0, "missing": missing, "differ": []}
    if have:
        new = embed_batch([t for _, _, t in have])
        new = new / np.linalg.norm(new, axis=1, keepdims=True)
        cos = [float(np.dot(v, n)) for (_, v, _), n in zip(have, new)]
        out["differ"] = [i for (i, _, _), c in zip(have, cos) if c < min_cos]
        out["same"] = len(have) - len(out["differ"])
        out["min_cos"] = round(min(cos), 4)
    return out


async def status() -> dict:
    """읽기만 — 벡터 수 · 커서 · 남은 행(다음 실행이 훑을 행 — 0 이면 다 따라잡았다) · 맞춰 보기(alignment)."""
    store = VectorStore(model=MODEL)
    picks = sample_ids(store) if store.load() else []
    cursor = int(store.manifest.get("cursor", 0))
    engine = create_async_engine(os.environ["DATABASE_URL"])
    try:
        async with engine.connect() as c:
            left = (await c.execute(text(
                "SELECT count(*) FROM voc_records WHERE archived_at IS NULL "
                "AND content_original IS NOT NULL AND id > :c"), {"c": cursor})).scalar()
            rows = (await c.execute(text(       # 보관된 행도 — 임베딩한 본문은 그대로다
                "SELECT id, LEFT(content_original, :chars) AS body FROM voc_records WHERE id = ANY(:ids)"),
                {"ids": [i for i, _ in picks], "chars": int(store.manifest.get("chars", CHARS))})).all()
    finally:
        await engine.dispose()
    res = {"vectors": store.count, "cursor": cursor, "remaining": int(left)}
    if picks:
        try:
            res["alignment"] = alignment(picks, {r.id: r.body for r in rows})
        except RuntimeError as e:          # ollama · bge-m3 가 없다
            res["alignment"] = {"error": str(e)[:200]}
    return res


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
    if sys.argv[1:] == ["--status"]:
        print(json.dumps(asyncio.run(status()), ensure_ascii=False))
        sys.exit(0)
    r = asyncio.run(main())
    sys.exit(1 if r.get("stopped") == "embed_error" or r.get("error") else 0)
