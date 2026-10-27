# dev 카탈로그(products·platforms)를 seed 가 읽는 JSON 으로 내보낸다 — 새로 까는 서버가 dev 와 같은 카탈로그를 갖게
"""
카탈로그 내보내기 — seed_master 가 읽는 catalog_products.json · catalog_platforms.json 을 만든다.

왜 — dev 카탈로그의 일부(GS26 계열 · Fold8/Flip8 · Buds4 · 공식 커뮤니티 앱 플랫폼 …)는 마이그레이션도 seed 도
아닌 길로 들어와, 새로 깐 서버(cae00)에는 그 행이 없었다. 해석과 태깅에서 지금 파는 제품이 통째로 빠졌다
(2026-10-27, SG-04). 카탈로그가 바뀌면 dev 에서 이것을 다시 돌려 커밋한다 — backend/tests/test_catalog_seed.py
가 DB 와 파일이 갈라지면 알린다.

실행(dev):
  apptainer exec --bind "$PWD/backend:/app" --env DATABASE_URL=... apptainer/sif/backend.sif \\
    sh -c "cd /app && python -m app.seeds.export_catalog"
"""
import asyncio
import json
import pathlib
import sys

from sqlalchemy import text

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from app.database import AsyncSessionLocal  # noqa: E402

OUT = pathlib.Path(__file__).resolve().parent
TABLES = {
    "products": ("catalog_products.json",
                 ["code", "series_code", "name_en", "name_ko", "released_at", "is_active",
                  "predecessor_code", "brand", "category"]),
    "platforms": ("catalog_platforms.json", ["code", "name", "region", "base_url", "is_active", "kind"]),
}


def dump(rows: list) -> str:
    """한 줄에 한 행 — 카탈로그가 바뀌면 diff 가 그 행만 보인다."""
    return "[\n" + ",\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n]\n"


async def main() -> None:
    async with AsyncSessionLocal() as db:
        for table, (fname, cols) in TABLES.items():
            rows = (await db.execute(text(f"SELECT {', '.join(cols)} FROM {table} ORDER BY code"))).mappings().all()
            data = [{k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in r.items()} for r in rows]
            (OUT / fname).write_text(dump(data), encoding="utf-8")
            print(f"  {fname}: {len(data)}행")


if __name__ == "__main__":
    asyncio.run(main())
