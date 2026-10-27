# seed 가 읽는 dev 카탈로그 파일(catalog_products·platforms.json)의 꼴과, 이 서버 DB 와 갈라지지 않았는지를 본다
import asyncio
import datetime
import json
import pathlib

import pytest

SEEDS = pathlib.Path(__file__).resolve().parents[1] / "app" / "seeds"


def _load(name):
    return json.loads((SEEDS / name).read_text(encoding="utf-8"))


def test_catalog_files_are_well_formed():
    prods, plats = _load("catalog_products.json"), _load("catalog_platforms.json")
    codes = [p["code"] for p in prods]
    assert len(codes) == len(set(codes)) and len(codes) > 800
    for p in prods:
        assert p["code"] and p["series_code"] and p["name_en"] and p["brand"] and p["category"], p
        assert len(p["code"]) <= 10 and len(p["series_code"]) <= 4, p          # products 열 길이
        if p["released_at"]:
            datetime.date.fromisoformat(p["released_at"])
        assert p["predecessor_code"] is None or p["predecessor_code"] in set(codes), p
    pcodes = [p["code"] for p in plats]
    assert len(pcodes) == len(set(pcodes)) and all(p["code"] and p["name"] for p in plats)
    # cae00 에서 빠져 있던 행(2026-10-27, SG-02·04) — 지금 파는 제품군과 공식 커뮤니티 앱
    assert {"GS26", "GS26P", "GS26U", "GZF8", "GZFL8"} <= set(codes)
    assert "samsung_community" in pcodes


def test_catalog_files_match_this_db():
    """카탈로그가 바뀌었는데 파일을 다시 내보내지 않으면 새로 까는 서버가 또 빠진 채로 뜬다 —
    python -m app.seeds.export_catalog 로 다시 내보낸다. DB 가 없는 환경이면 건너뛴다."""
    from sqlalchemy import text
    from app.database import AsyncSessionLocal

    async def codes():
        async with AsyncSessionLocal() as db:
            p = {r[0] for r in (await db.execute(text("SELECT code FROM products"))).all()}
            q = {r[0] for r in (await db.execute(text("SELECT code FROM platforms"))).all()}
            return p, q
    try:
        db_products, db_platforms = asyncio.run(codes())
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"DB 없음: {e!r}"[:120])
    f_products = {p["code"] for p in _load("catalog_products.json")}
    f_platforms = {p["code"] for p in _load("catalog_platforms.json")}
    assert db_products <= f_products, f"파일에 없는 제품 — 다시 내보낸다: {sorted(db_products - f_products)[:10]}"
    assert db_platforms <= f_platforms, f"파일에 없는 플랫폼 — 다시 내보낸다: {sorted(db_platforms - f_platforms)[:10]}"


def test_tool_description_examples_exist_in_the_catalog():
    """도구 설명에 예로 든 제품 코드가 카탈로그에 있어야 한다 — 없으면 예시를 그대로 넣은 질의가 0건이다
    (cae00 2026-10-27 OT-15 — 예시 영문명이 카탈로그에 없어 resolved null). 저장소 배치에서만 돈다."""
    import re
    server = pathlib.Path(__file__).resolve().parents[2] / "mcp-server" / "server.py"
    if not server.exists():
        pytest.skip("mcp-server 가 없는 배치(컨테이너 /app 만)")
    src = server.read_text(encoding="utf-8")
    used = set(re.findall(r"\b(G[A-Z]{1,5}\d{1,3}[A-Z]{0,2}|AP\d{2}[A-Z]{0,2}|PX\d{1,2}[A-Z]?)\b", src))
    codes = {p["code"] for p in _load("catalog_products.json")}
    assert used and used <= codes, f"카탈로그에 없는 예시 코드: {sorted(used - codes)}"
