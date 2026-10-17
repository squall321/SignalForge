# 임베딩 벡터를 조각 파일로 쌓고 mmap 으로 읽는 저장소 — 쓰기(crawler)와 읽기(MCP)가 같은 형식을 쓴다
"""벡터 저장소.

pgvector 가 없다(PostgreSQL 16 Alpine). DB 에 넣으면 덤프가 수 GB 씩 커져 30분마다 도는
Drive 백업이 무거워진다. 그래서 파일로 둔다 — 다시 만들 수 있는 파생 데이터다.

형식 (root/<model>/)
  manifest.json   {"model", "dim", "chars", "cursor", "count", "shards": [{"name","n","min_id","max_id"}]}
  <name>.vec.npy  float32 (n, dim), L2 정규화 — 코사인 = 내적
  <name>.ids.npy  int64 (n,), 오름차순

쓰기 규칙
  · 조각은 임시 이름으로 쓴 뒤 rename — 읽는 쪽이 반쯤 쓴 파일을 보지 않는다
  · manifest 는 조각을 다 쓴 **뒤에만** 바꾼다. cursor 는 그 시점까지 실제로 처리한 마지막 id —
    실패한 묶음을 넘어가지 않는다(창을 고정하면 꼬리만 긁고, 실패에 커서를 밀면 구멍이 남는다)
  · 마지막 조각(tail)은 SHARD 행이 찰 때까지 덧붙여 다시 쓴다 — 증분 실행마다 작은 조각이
    수천 개 쌓이지 않게
  · 쓰기는 한 번에 하나(파일 잠금). 백그라운드 전량 실행과 주기 실행이 겹쳐도 안전하다
"""
import fcntl
import json
import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

import numpy as np

SHARD = 50_000
_GATHER = 8192          # 범위 검색에서 한 번에 복사해 읽는 행 수(32MB)


def default_root() -> Path:
    return Path(os.getenv("EMB_DIR") or Path(__file__).resolve().parents[1] / "data" / "vectors")


class VectorStore:
    def __init__(self, root: Optional[Path] = None, model: str = "bge-m3"):
        self.dir = Path(root or default_root()) / model.replace(":", "_").replace("/", "_")
        self.model = model
        self._mtime = None
        self._shards: List[Tuple[np.ndarray, np.ndarray]] = []
        self.manifest: dict = {}

    # ── 읽기 ────────────────────────────────────────────────────────────
    @property
    def manifest_path(self) -> Path:
        return self.dir / "manifest.json"

    def _read_manifest(self) -> dict:
        try:
            return json.loads(self.manifest_path.read_text())
        except (OSError, ValueError):
            return {}

    def load(self, force: bool = False) -> bool:
        """manifest 가 바뀌었으면 조각을 다시 연다. 벡터가 하나라도 있으면 True."""
        try:
            mt = self.manifest_path.stat().st_mtime
        except OSError:
            self._shards, self.manifest, self._mtime = [], {}, None
            return False
        if not force and mt == self._mtime:
            return bool(self._shards)
        man = self._read_manifest()
        shards = []
        for s in man.get("shards", []):
            vec = np.load(self.dir / f"{s['name']}.vec.npy", mmap_mode="r")
            ids = np.load(self.dir / f"{s['name']}.ids.npy", mmap_mode="r")
            shards.append((ids, vec))
        self._shards, self.manifest, self._mtime = shards, man, mt
        return bool(shards)

    @property
    def count(self) -> int:
        return int(self.manifest.get("count", 0))

    def search(self, q: np.ndarray, k: int = 200,
               allowed: Optional[np.ndarray] = None) -> Tuple[np.ndarray, np.ndarray]:
        """코사인 상위 k → (ids, sims) 내림차순. allowed(오름차순 id 배열)가 있으면 그 안에서만."""
        q = np.asarray(q, dtype=np.float32).ravel()
        q = q / (np.linalg.norm(q) or 1.0)
        all_ids, all_sims = [], []
        for ids, vec in self._shards:
            if allowed is not None:
                # 이 조각의 id 구간에 드는 허용 id 만 → 조각 안 위치 → 실제로 있는 것만
                lo, hi = np.searchsorted(allowed, [ids[0], ids[-1] + 1])
                want = allowed[lo:hi]
                if not len(want):
                    continue
                pos = np.searchsorted(ids, want)
                pos = pos[ids[np.minimum(pos, len(ids) - 1)] == want]
                if not len(pos):
                    continue
                # 골라 읽기는 행을 복사한다 — 범위 5만 행이면 200MB 라 나눠 읽는다
                sims = np.concatenate([np.asarray(vec[pos[i:i + _GATHER]]) @ q
                                       for i in range(0, len(pos), _GATHER)])
                sid = np.asarray(ids[pos])
            else:
                sims = np.asarray(vec) @ q
                sid = ids
            if len(sims) > k:
                top = np.argpartition(-sims, k)[:k]
                sims, sid = sims[top], np.asarray(sid)[top]
            all_ids.append(np.asarray(sid))
            all_sims.append(sims)
        if not all_ids:
            return np.array([], dtype=np.int64), np.array([], dtype=np.float32)
        ids = np.concatenate(all_ids)
        sims = np.concatenate(all_sims)
        order = np.argsort(-sims)[:k]
        return ids[order], sims[order]

    # ── 쓰기 ────────────────────────────────────────────────────────────
    @contextmanager
    def writer(self):
        """쓰기 잠금. 이미 다른 프로세스가 쓰고 있으면 BlockingIOError."""
        self.dir.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.dir / ".lock", os.O_CREAT | os.O_RDWR, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.load(force=True)
            yield self
        finally:
            os.close(fd)

    def _save_array(self, name: str, arr: np.ndarray):
        tmp = self.dir / f".{name}.tmp.npy"
        np.save(tmp, arr)
        os.replace(tmp, self.dir / f"{name}.npy")

    def _write_manifest(self, man: dict):
        man["updated"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        tmp = self.dir / ".manifest.tmp"
        tmp.write_text(json.dumps(man, ensure_ascii=False, indent=1))
        os.replace(tmp, self.manifest_path)

    def append(self, ids: Iterable[int], vecs: np.ndarray, cursor: int, *, dim: int, chars: int):
        """벡터를 덧붙이고 cursor(처리한 마지막 id, 빈 본문으로 건너뛴 것 포함)를 기록한다."""
        ids = np.asarray(list(ids), dtype=np.int64)
        vecs = np.asarray(vecs, dtype=np.float32)
        man = self._read_manifest() or {"model": self.model, "dim": dim, "chars": chars,
                                        "cursor": 0, "count": 0, "shards": []}
        if man.get("dim") != dim:
            raise ValueError(f"차원이 다르다 — 저장소 {man.get('dim')} · 새 벡터 {dim}")
        if len(ids):
            if man["shards"] and int(ids[0]) <= man["shards"][-1]["max_id"]:
                raise ValueError("id 는 늘어나는 순서로만 덧붙인다")
            norms = np.linalg.norm(vecs, axis=1, keepdims=True)
            vecs = vecs / np.where(norms == 0, 1, norms)
            shards = man["shards"]
            while len(ids):
                if shards and shards[-1]["n"] < SHARD:
                    tail = shards[-1]
                    room = SHARD - tail["n"]
                    old_ids = np.load(self.dir / f"{tail['name']}.ids.npy")
                    old_vec = np.load(self.dir / f"{tail['name']}.vec.npy")
                    take_ids, take_vec = ids[:room], vecs[:room]
                    new_ids = np.concatenate([old_ids, take_ids])
                    new_vec = np.concatenate([old_vec, take_vec])
                else:
                    tail = {"name": f"s{len(shards):05d}", "n": 0}
                    shards.append(tail)
                    take_ids, take_vec = ids[:SHARD], vecs[:SHARD]
                    new_ids, new_vec = take_ids, take_vec
                self._save_array(f"{tail['name']}.vec", new_vec)
                self._save_array(f"{tail['name']}.ids", new_ids)
                tail.update(n=int(len(new_ids)), min_id=int(new_ids[0]), max_id=int(new_ids[-1]))
                ids, vecs = ids[len(take_ids):], vecs[len(take_ids):]
            man["count"] = int(sum(s["n"] for s in shards))
        man["cursor"] = max(int(man.get("cursor", 0)), int(cursor))
        self._write_manifest(man)
        self.load(force=True)
