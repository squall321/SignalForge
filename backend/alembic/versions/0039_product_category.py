"""product_category — products.category 신설 (제품군 차원). 769종 전수 분류.

배경:
  products 에 제품군 차원이 없어 "워치끼리", "버즈끼리" 비교가 불가능했다.
  series_code 가 대신하는 듯 보이지만 브랜드와 라인이 섞여 있고 일관성이 없다.
  실측된 혼합 사례 —
    · series 'GW' 안에 Galaxy **Fit**(밴드) 4종과 Gear Fit 2종이 워치와 섞여 있다
    · series 'OPW'(OnePlus Watch) 안에 OnePlus **Buds** 4종이 들어 있다
    · series 'NT'(Nothing Phone) 안에 Nothing **Ear**(NTEAR) 가 있다
    · series 'AB'·'SNA'·'BSE' 는 이어버즈와 헤드폰을 한 시리즈에 섞어 놨다
  그래서 category 는 series_code 로 유도할 수 없고 별도 컬럼이어야 한다.

분류 값 (769종 전수, 미분류 0):
#   phone=548 — 스마트폰 및 피처폰
#   watch= 96 — 손목 착용 스마트워치
#   tablet= 58 — 태블릿
#   buds= 42 — 무선 이어버드(인이어/커널형/오픈형)
#   band= 15 — 손목 피트니스 트래커(밴드)
#   headphone=  8 — 오버이어/온이어 헤드폰
#   ring=  2 — 스마트링

규칙:
  code 접두사 우선, 안 되면 name_en 정규식. **위에서부터 첫 매칭**을 적용한다.
  순서가 본질이다 — ABMAX(헤드폰)가 AB(버즈)보다 앞이어야 AirPods Max 가 버즈로
  가지 않고, GMN(가민)이 삼성 G catch-all 보다 앞이어야 하며, GBEAM(Galaxy Beam,
  폰)이 GB(버즈)보다 앞이어야 한다.

  Postgres 의 `\b` 는 정규식 word boundary 가 아니라 **backspace** 다. 그래서
  name_en 규칙에 `\b` 를 쓰지 않았고, 판정도 SQL 이 아니라 Python 에서 한다.
"""
import re

import sqlalchemy as sa
from alembic import op

revision = "0039"
down_revision = "0038"
branch_labels = None
depends_on = None

# (kind, match, category) — kind 'c'=code 접두사, 'n'=name_en 정규식. 첫 매칭 적용.
RULES = [
    ('c', 'ABMAX', 'headphone'),  # AirPods Max/Max 2 — AB(buds)보다 반드시 앞
    ('c', 'ABP', 'buds'),  # AirPods Pro 1~3
    ('c', 'AB', 'buds'),  # AirPods 1st~5
    ('c', 'AW', 'watch'),  # Apple Watch Series/SE/Ultra
    ('c', 'AP', 'phone'),  # iPhone 전량(APAIR=iPhone Air, APSE1~3 포함)
    ('c', 'AZ', 'watch'),  # Amazfit 6종 전부 스마트워치
    ('c', 'AS', 'phone'),  # Asus ROG Phone / Zenfone
    ('c', 'BSEQCE', 'buds'),  # Bose QuietComfort Earbuds — BSE(headphone)보다 앞
    ('c', 'BSE', 'headphone'),  # QuietComfort 35/45/Ultra
    ('c', 'FTBC', 'band'),  # Fitbit Charge 5/6 = 손목 트래커 밴드 — FTB(watch)보다 앞
    ('c', 'FTB', 'watch'),  # Fitbit Sense / Versa
    ('c', 'GMN', 'watch'),  # Garmin Forerunner/fenix/Venu
    ('c', 'HWFB', 'buds'),  # Huawei FreeBuds 7/Neo/Pro4/Pro5/SE
    ('c', 'HWFIT', 'watch'),  # Huawei Watch Fit 4/5(+Pro)
    ('c', 'HWB', 'band'),  # Huawei Band 10/11
    ('c', 'HWD', 'watch'),  # Huawei Watch D2/D3(혈압 측정)
    ('c', 'HWGT', 'watch'),  # Huawei Watch GT 6/7(+Pro), GT Runner 2
    ('c', 'HWULT', 'watch'),  # Huawei Watch Ultimate
    ('c', 'HW5', 'watch'),  # Huawei Watch 5
    ('c', 'HW6', 'watch'),  # Huawei Watch 6
    ('n', '^Huawei Watch', 'watch'),  # 미래 HW7/HW8(Huawei Watch 7/8)이 아래 HW→phone 에 잡아먹히는 것을 막는 가드
    ('c', 'HW', 'phone'),  # Huawei Mate/Mate X/nova/P/Pura
    ('c', 'HO', 'phone'),  # Honor 번호선/Magic/Magic V(폴더블)
    ('c', 'JBR', 'buds'),  # Jabra Elite
    ('c', 'MT', 'phone'),  # Moto E/G/X/Z, Motorola Edge, Razr
    ('c', 'NK', 'phone'),  # Nokia 14종 전부 폰
    ('c', 'NTE', 'buds'),  # Nothing Ear(NTE, NTE3A, NTEA, NTEAR)
    ('c', 'NT', 'phone'),  # Nothing Phone, CMF Phone
    ('c', 'OPB', 'buds'),  # OnePlus Buds 3/4/Pro 3, Nord Buds
    ('c', 'OPW', 'watch'),  # OnePlus Watch 2/2R/3/4
    ('c', 'OP', 'phone'),  # OnePlus 번호선/Nord/Open
    ('c', 'OO', 'phone'),  # Oppo Find N/Find X/Reno
    ('c', 'RL', 'phone'),  # realme 번호선/GT/P
    ('c', 'SNAWH', 'headphone'),  # Sony WH-1000XM4/5/6 = 오버이어
    ('c', 'SNAWF', 'buds'),  # Sony WF-1000XM4/5/6 = 커널형
    ('c', 'SN', 'phone'),  # Sony Xperia
    ('c', 'VV', 'phone'),  # iQOO / vivo V·X / X Fold
    ('c', 'XMB', 'band'),  # Xiaomi Smart Band 8~11 + XMBAND — XMW/XM 보다 반드시 앞
    ('c', 'XMW', 'watch'),  # Xiaomi Watch 5 / Watch S5
    ('c', 'XM', 'phone'),  # Xiaomi 11~18(14T/15T/17T/18 Fold 포함)
    ('c', 'RDW', 'watch'),  # Redmi Watch 4/5/6
    ('c', 'RM', 'phone'),  # Redmi 번호/Note/K/Turbo
    ('c', 'PC', 'phone'),  # POCO F/M/X
    ('c', 'PB', 'buds'),  # Pixel Buds 2a / Pro / Pro 2
    ('c', 'PW', 'watch'),  # Pixel Watch 1~5
    ('c', 'PX', 'phone'),  # Pixel(Fold, Pro Fold 포함)
    ('c', 'GBEAM', 'phone'),  # Galaxy Beam — 아래 GB→buds 에 잡아먹히는 함정
    ('c', 'GFIT', 'band'),  # Galaxy Fit / Fit2 / Fit3 / Fit e = 밴드
    ('c', 'GGEARFIT', 'band'),  # Gear Fit(1세대)
    ('c', 'GGFIT', 'band'),  # Gear Fit 2
    ('c', 'GGEAR', 'watch'),  # Galaxy Gear / Gear 2 / Gear 2 Neo = 초기 스마트워치
    ('c', 'GGS', 'watch'),  # Samsung Gear S/S2/S3 + Gear Sport(GGSPORT)
    ('c', 'GGRAND', 'phone'),  # Galaxy Grand / Grand 2
    ('c', 'GGRPRM', 'phone'),  # Galaxy Grand Prime / Prime+
    ('c', 'GICX', 'buds'),  # Gear IconX / IconX 2018 = 버즈
    ('c', 'GNT122', 'tablet'),  # Galaxy Note Pro 12
    ('c', 'GTREND', 'phone'),  # Galaxy Trend / Trend Lite — 아래 GT→tablet 함정
    ('c', 'GWIDE', 'phone'),  # Galaxy Wide 1~8 — 아래 GW→watch 함정
    ('c', 'GWIN', 'phone'),  # Galaxy Win / Win Pro — 아래 GW→watch 함정
    ('c', 'GR', 'ring'),  # Galaxy Ring / Ring2
    ('c', 'GB', 'buds'),  # Galaxy Buds 전 세대(Buds, Buds+, Buds2, Buds3, Buds4, FE, Live, Pro)
    ('c', 'GW', 'watch'),  # Galaxy Watch 전 세대(Watch1~9, Classic, Active1~3, FE, Ultra)
    ('c', 'GT', 'tablet'),  # Galaxy Tab / Tab 2~4 / Tab A / Tab S / TabPRO / Tab Active
    ('c', 'G', 'phone'),  # 삼성 나머지 전량 폰 catch-all (GA/GS/GM/GJ/GN/GF/GZ/GZF/GZFL/GXC/GY/GI/GC/GE/GO/
]


def _classify(code: str, name_en: str) -> str:
    up = (code or "").upper()
    for kind, pat, cat in RULES:
        if kind == "c":
            if up.startswith(pat.upper()):
                return cat
        else:
            if re.search(pat, name_en or "", re.IGNORECASE):
                return cat
    return "phone"          # 규칙 전수 적용 시 미분류 0 이므로 도달하지 않는다


def upgrade():
    op.add_column("products", sa.Column("category", sa.String(16), nullable=True))
    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT code, name_en FROM products")).fetchall()
    for code, name_en in rows:
        conn.execute(
            sa.text("UPDATE products SET category = :c WHERE code = :code"),
            {"c": _classify(code, name_en), "code": code},
        )
    op.alter_column("products", "category", nullable=False)
    op.create_index("ix_products_category", "products", ["category"])


def downgrade():
    op.drop_index("ix_products_category", table_name="products")
    op.drop_column("products", "category")
