"""platform_kind — 플랫폼을 매체/커뮤니티/마켓 등으로 분류.

배경:
  결함 급등 탐지의 독립성 가드로 유효 플랫폼 수(1/HHI)를 썼는데, 적대적 검증에서
  **정반대로 작동**함이 드러났다. 뉴스 1건("중국 칭다오 S25+ 충전 중 발화")이 10개
  매체·5개 언어로 복제 수집되면 플랫폼 분포가 넓어져 eff_platforms=8.16 으로 가드를
  8배 여유로 통과하고, severity=safety 라 critical 로 승격돼 나갔다. 즉 매체가 많이
  받아쓸수록 가드를 더 확실히 통과한다 — 막으려던 것을 오히려 통과시킨 것이다.

  실제 서로 다른 사고는 3~4건인데 14건으로 계수됐다. 이를 가르려면 '몇 개 매체가
  받아썼나'(신디케이션)와 '몇 명이 직접 겪었나'(독립 제보)를 구분해야 하고,
  platforms 테이블에 그 축이 없었다.

분류
  media       — 뉴스·리뷰 매체. 한 사건을 여러 곳이 받아쓰므로 건수가 사건 수가 아니다.
  aggregator  — 뉴스 집계(Google News 등). 매체보다 더 강한 복제원.
  community   — 사용자 포럼·SNS·Q&A. 1건 ≈ 1명의 독립 제보에 가깝다.
  marketplace — 상품 리뷰(아마존·앱스토어 등). 구매자 직접 경험.
  official    — 제조사 공식 커뮤니티. 사용자 글이지만 채널이 하나뿐.
  regulatory  — 규제기관 리콜 정보. 권위 있으나 단일 출처.
  research    — 논문 등.
"""
from alembic import op
import sqlalchemy as sa

revision = "0034"
down_revision = "0033"
branch_labels = None
depends_on = None

_KINDS = {
    "media": [
        "9to5google", "anandtech", "androidcentral", "androidpolice", "arageek",
        "ausdroid", "computerbase", "donanimhaber", "dpreview", "droidsans",
        "engadget", "frandroid", "gadgets360", "gigazine", "gizmodo_au",
        "gizmodo_jp", "gsmarena", "gsmchoice", "hipertextual", "hwupgrade",
        "inside_handy", "iphoneincanada", "ithome", "jagatreview", "kompas",
        "macrumors", "mobil_se", "mobile_review", "mobilesyrup", "mybroadband",
        "mysmartprice", "notebookcheck", "nu_nl", "phandroid", "phonearena",
        "sammobile", "sammyfans", "sanook", "shiftdelete", "sspai", "sweclockers",
        "techcabal", "techinafrica", "tecnoblog", "telepolis", "theverge",
        "tomsguide", "tudocelular", "tweakers", "xataka", "xataka_mx", "xda",
        "zdnet_kr",
    ],
    "aggregator": ["googlenews", "wpnews", "waybacknews"],
    "community": [
        "4pda", "bluesky", "bobaedream", "clien", "dcinside", "dogdrip",
        "fmkorea", "fourchan_g", "gsmarena_forum", "hackernews", "hackerone",
        "hardware_fr", "ifixit", "instiz", "kaskus", "lemmy", "lowyat",
        "mastodon", "misskey", "mlbpark", "naver_cafe", "pikabu", "ppomppu",
        "quasarzone", "quora", "reddit", "reddit_rss", "resetera", "ruliweb",
        "slrclub", "stackexchange", "theqoo", "tinhte", "twitter", "youtube",
    ],
    "marketplace": [
        "amazon_de", "amazon_jp", "amazon_kr", "amazon_us", "appstore",
        "bestbuy", "danawa",
    ],
    "official": ["samsung_community"],
    "regulatory": ["recalls"],
    "research": ["arxiv"],
}


def upgrade():
    op.add_column("platforms", sa.Column("kind", sa.String(16), nullable=True))
    for kind, codes in _KINDS.items():
        op.execute(sa.text(
            "UPDATE platforms SET kind = :k WHERE code = ANY(:codes)"
        ).bindparams(k=kind, codes=codes))
    # 미분류는 보수적으로 community 취급(독립 제보로 세되 매체 특혜는 주지 않음)
    op.execute("UPDATE platforms SET kind = 'community' WHERE kind IS NULL")
    op.create_index("ix_platforms_kind", "platforms", ["kind"])


def downgrade():
    op.drop_index("ix_platforms_kind", table_name="platforms")
    op.drop_column("platforms", "kind")
