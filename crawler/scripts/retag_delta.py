# 매처를 고친 뒤, 고치기 전 매처와 지금 매처의 판정이 갈리는 활성 글만 그 차이대로 다시 태깅한다(되돌리기 CSV 를 남긴다)
"""
매처 고침 반영 — 차이만.

매처를 고쳐도 지난 글의 태그는 옛 판정 그대로다. 일일 링크 백필(backfill_product_links)은 링크를 더하기만 하고 떼지
않아, 오탐을 고쳐도 지난 오탐 태그가 남는다. 그렇다고 전 코퍼스를 지금 매처로 다시 판정하면 이번 고침과 무관한 옛 판정
차이(크롤러 명시 매핑 · 예전 매처 판)까지 덮인다(backfill_rival_retag 의 교훈). 그래서 **고치기 전 매처**를 함께 돌려
같은 본문에서 둘이 갈리는 코드만 바꾼다.

  - 후보: 활성 글 중 본문(소문자)이 --prefilter 정규식에 걸리는 것. 크롤러가 제품을 직접 매기는 플랫폼은 건너뛴다
  - 차이: 옛 매처 출력 O, 새 매처 출력 N(둘 다 레거시 폴백 포함 — 수집 길목과 같다). 뗄 것 R = O − N, 더할 것 A = N − O
  - 링크: 저장된 링크에서 R 을 떼고 A 를 더한다(역할은 N 의 것). 그 밖의 링크는 손대지 않는다
  - 대표: 저장된 대표가 R 에 들면(그 판정이 바뀌었다) N 의 대표로. 비었으면 A 가운데 N 순서로 첫째. 저장된 대표가 O 의
    첫째였고 N 도 그 제품을 보면서 다른 것을 첫째로 두면 N 의 첫째로(새 글이 받을 판정). 아니면 그대로(다른 출처)
  - 구형 남기기: 레거시 폴백은 라이브가 빈손일 때만 돌아, 새 라이브 코드가 생기면 같은 글의 다른 구형 모델
    태그가 O 에만 남는다('A05s … my A54'). 판정이 바뀐 게 아니므로 R 에서 뺀다(legacy_kept)
  - 되돌리기: 고친 행마다 (voc_id, 옛 대표, 옛 링크, 새 대표, 새 링크, 뗀 것, 더한 것, 남긴 구형)

옛 매처는 --old 디렉터리의 product_match.py 와 model_codes.py(고치기 전 판, git show 로 떠 둔다)로 짓는다.

실행: DATABASE_URL=... python3 -m scripts.retag_delta --old logs/matcher-before-<날짜> --prefilter '<정규식>' [--apply] [--log 경로]
      (기본은 dry-run — 무엇이 바뀌는지 세고 CSV 만 쓴다)
"""
import argparse
import asyncio
import csv
import datetime
import importlib.util
import logging
import os
import pathlib
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine  # noqa: E402

from base import product_match as new_pm  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("retag_delta")

# 크롤러가 제품을 직접 매기는 플랫폼(meta product_code) — 본문 판정으로 덮지 않는다
SKIP_PLATFORMS = "amazon_de,amazon_jp,amazon_kr,amazon_us,bestbuy,gsmarena,gsmarena_forum"

CANDIDATES_SQL = text("""
    SELECT v.id, v.content_original, pp.code AS primary_code,
           COALESCE((SELECT string_agg(p2.code || ':' || l2.role, ';' ORDER BY p2.code)
                     FROM voc_product_links l2 JOIN products p2 ON p2.id = l2.product_id
                     WHERE l2.voc_id = v.id), '') AS links
    FROM voc_records v
    LEFT JOIN products pp ON pp.id = v.product_id
    JOIN platforms pf ON pf.id = v.platform_id
    WHERE v.archived_at IS NULL AND v.content_original IS NOT NULL AND v.id > :after
      AND pf.code <> ALL(:skip) AND lower(v.content_original) ~ :rx
    ORDER BY v.id
    LIMIT :batch
""")


def load_old_matcher(old_dir: pathlib.Path):
    """고치기 전 product_match 를 따로 짓는다 — 그 판의 model_codes 를 물려 모델코드 사전 차이도 잡는다."""
    def _load(name, path):
        spec = importlib.util.spec_from_file_location(name, path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    old_mc = _load("model_codes_before", old_dir / "model_codes.py")
    old_pm = _load("product_match_before", old_dir / "product_match.py")
    old_pm._mc = old_mc
    return old_pm


def _non_primary(role: str, roles) -> str:
    if role != "primary":
        return role
    return "compared" if "compared" in roles else "mentioned"


def legacy_kept(pm, text, old_out, new_out):
    """새 매처 출력에서 빠진 구형(레거시) 코드 가운데 근거가 새 매칭과 다른 곳에 있는 것 — (남길 코드, 대표를 넘길지).

    새 매처가 찾은 구간을 지운 본문에서 레거시가 같은 코드를 다시 내면 근거가 다른 곳에 있다 — 남긴다.
    근거가 그 구간이었다면(변형을 이제 알아본 'S20 FE' → GS20) 지운 본문에서 사라진다 — 뗀다.
    남긴 코드가 저장된 대표일 때, 제목 줄에 새 대표만 있고 남긴 코드는 없으면 대표를 새 대표로 넘긴다
    ('… on my mom's Galaxy A05s?' + 본문 'my A54'). 그 밖에는 저장된 대표를 지킨다.
    """
    new_codes = [c for c, _ in new_out]
    gone = {c for c, _ in old_out if c not in new_codes and c not in pm._LIVE_CODES}
    # 폰 S6~S10 은 탭 S6~S10 과 번호가 같다(_PHONE_S_SHARED). 새 매처가 탭을 찾은 글에서 레거시가 낸 폰 S6~S10 은
    # 'tab' 을 빼고 쓴 탭이었다 — 2026-11-07 남기기 후보 14행 모두('S10 Fe plus or S10 plus?'). 남기지 않는다.
    if any(c.startswith(("GTAB", "GTS")) for c in new_codes):
        gone = {c for c in gone if not pm._PHONE_S_SHARED.match(c)}
    if not gone:
        return set(), False
    patterned = {c for c, _ in pm._COMPILED}
    spans = pm._spans_by_code(text, [c for c in new_codes if c in patterned])
    chars = list(text)
    for sp in spans.values():
        for s, e in sp:
            chars[s:e] = " " * (e - s)
    masked = "".join(chars)
    kept = {pm.legacy_fallback_code(masked)} & gone
    nl = text.find("\n")
    if not kept or not new_out or not 0 < nl <= pm._TITLE_MAX:
        return kept, False
    new_in_title = any(s < nl for s, _ in spans.get(new_out[0][0], []))
    return kept, new_in_title and pm.legacy_fallback_code(masked[:nl]) not in kept


def plan(primary_code, links, old_out, new_out, kept=frozenset(), title_prefers_new=False):
    """한 글의 (새 대표, 새 링크[(code, role)], 뗀 것, 더한 것) — 바꿀 것이 없으면 None.

    kept · title_prefers_new 는 legacy_kept 의 결과다 — kept 는 R 에서 빼고, 저장된 대표가 kept 이고
    title_prefers_new 면 대표를 N 의 대표로 넘긴다(kept 는 링크로 남는다).
    """
    old_codes = [c for c, _ in old_out]
    new_roles = dict(new_out)
    removed = [c for c in old_codes if c not in new_roles and c not in kept]
    added = [c for c, _ in new_out if c not in old_codes]
    if not removed and not added:
        return None
    stored = dict(x.split(":", 1) for x in links.split(";") if x)
    new = {c: r for c, r in stored.items() if c not in removed}
    for c in added:
        new.setdefault(c, new_roles[c])
    if primary_code in removed or (title_prefers_new and primary_code in kept):
        new_primary = new_out[0][0] if new_out else None
    elif primary_code is None:
        new_primary = next((c for c, _ in new_out if c in added), None)
    elif old_codes and primary_code == old_codes[0] and primary_code in new_roles and new_out[0][0] != primary_code:
        # 저장된 대표가 옛 매처의 첫째였고, 새 매처도 그 제품을 보면서 다른 것을 첫째로 둔다 — 새 판정을 따른다.
        # 옛 매처는 변형을 몰라 'S26 FE' 기사를 GS26 으로 두었다(새 글은 수집 때 이 판정을 받는다). 표본 44건 판정 —
        # 제목 있는 글 나아짐 9 · 나빠짐 1 · 같음 12, 제목 없는 글 13 · 3 · 6(2026-11-07)
        new_primary = new_out[0][0]
    else:
        new_primary = primary_code                         # 다른 출처의 대표(명시 매핑 · 예전 판) — 지킨다
    if new_primary is not None:
        new[new_primary] = "primary"
    for c, r in list(new.items()):
        if c != new_primary:
            new[c] = _non_primary(r, new_roles.values())
    if new_primary == primary_code and new == stored:
        return None
    return new_primary, sorted(new.items()), removed, added


async def main(args) -> dict:
    url = os.getenv("DATABASE_URL", "")
    if not url:
        raise SystemExit("DATABASE_URL 미설정")
    old_pm = load_old_matcher(pathlib.Path(args.old))
    engine = create_async_engine(url, pool_pre_ping=True)
    Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with Session() as db:
        pmap = {r.code.upper(): r.id for r in (await db.execute(text("SELECT code, id FROM products"))).all()}
    skip = [s.strip() for s in args.skip_platforms.split(",") if s.strip()]
    logp = pathlib.Path(args.log or f"logs/retag-delta-{datetime.date.today().isoformat()}.csv")
    logp.parent.mkdir(parents=True, exist_ok=True)
    stats: Counter = Counter()
    after = 0
    try:
        with logp.open("w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["voc_id", "old_primary", "old_links", "new_primary", "new_links", "removed", "added",
                        "kept_legacy"])
            while True:
                async with Session() as db:
                    rows = (await db.execute(CANDIDATES_SQL, {"after": after, "skip": skip, "rx": args.prefilter,
                                                              "batch": args.batch})).all()
                    if not rows:
                        break
                    for r in rows:
                        after = r.id
                        stats["candidates"] += 1
                        o = old_pm.infer_all_product_codes(r.content_original)
                        n = new_pm.infer_all_product_codes(r.content_original)
                        kept, title_new = legacy_kept(new_pm, r.content_original, o, n)
                        p = plan(r.primary_code, r.links, o, n, kept, title_new)
                        if p is None:
                            continue
                        new_primary, new_links, removed, added = p
                        unknown = [c for c, _ in new_links if c.upper() not in pmap]
                        if unknown:
                            stats["skip_unknown_code"] += 1        # 카탈로그 밖 코드 — 적용하지 않는다
                            continue
                        stats["changed"] += 1
                        stats[f"primary {r.primary_code}->{new_primary}"] += 1
                        for c in removed:
                            stats[f"-{c}"] += 1
                        for c in added:
                            stats[f"+{c}"] += 1
                        for c in kept:
                            stats[f"={c}"] += 1
                        w.writerow([r.id, r.primary_code or "", r.links, new_primary or "",
                                    ";".join(f"{c}:{role}" for c, role in new_links),
                                    ";".join(removed), ";".join(added), ";".join(sorted(kept))])
                        if not args.apply:
                            continue
                        await db.execute(text("UPDATE voc_records SET product_id = :p WHERE id = :v"),
                                         {"p": pmap[new_primary.upper()] if new_primary else None, "v": r.id})
                        await db.execute(text("DELETE FROM voc_product_links WHERE voc_id = :v"), {"v": r.id})
                        for code, role in new_links:
                            await db.execute(text(
                                "INSERT INTO voc_product_links (voc_id, product_id, role) VALUES (:v, :p, :r)"),
                                {"v": r.id, "p": pmap[code.upper()], "r": role})
                    if args.apply:
                        await db.commit()
    finally:
        await engine.dispose()
    log.info(f"=== 차이 반영: 후보 {stats['candidates']} · 고침 {stats['changed']} (apply={args.apply}) — {logp} ===")
    for k, v in stats.most_common(60):
        if k[0] in "+-=" or k.startswith("primary") or k == "skip_unknown_code":
            log.info(f"    {k}: {v}")
    return dict(stats)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="매처 고침 반영 — 옛 매처와 갈리는 글만")
    ap.add_argument("--old", required=True, help="고치기 전 product_match.py · model_codes.py 가 든 디렉터리")
    ap.add_argument("--prefilter", required=True, help="후보를 고를 소문자 본문 정규식(바뀐 꼴을 덮게)")
    ap.add_argument("--skip-platforms", default=SKIP_PLATFORMS, help="건너뛸 플랫폼 코드(쉼표)")
    ap.add_argument("--apply", action="store_true", help="실제로 고친다(없으면 dry-run)")
    ap.add_argument("--log", help="되돌리기 CSV 경로")
    ap.add_argument("--batch", type=int, default=2000)
    asyncio.run(main(ap.parse_args()))
