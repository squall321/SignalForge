"""category_expansion — 제품군 확장 116종 (XR·글래스·링·노트북·오디오·미커버 폰).

배경:
  0039 로 category 차원이 생기자 공백이 드러났다 —
    · tablet 58종이 **전량 삼성**(iPad 0종)
    · ring 2종이 **전량 삼성**(Oura·Whoop·Ultrahuman 0종)
    · **laptop·xr·glasses 는 값 자체가 없었다**
  자사 제품도 빠져 있었다 — Galaxy Book 797건 · Galaxy XR 164건 · Galaxy Glasses 121건 ·
  Gear VR 이 코퍼스에 있는데 카탈로그에 없다.

구성: laptop 25 · buds 23 · phone 18 · headphone 12 · ring 10 · xr 10 · glasses 8 · tablet 6 · band 4
브랜드: samsung 24 · apple 18 · anker 11 · meta 10 · jbl 7 · nubia 7 · fairphone 6 · whoop 4 · sennheiser 4 · microsoft 4 · oura 4 · ringconn 3 · nothing 2 · hmd 2 · zte 2 · ultrahuman 2 · rokid 1 · viture 1 · xreal 1 · sharp 1 · amazfit 1 · tcl 1

선정·검증:
  브랜드군 4트랙 병렬 조사 + 트랙별 독립 반증. 언급량은 단어경계로 재실측했다
  (부분문자열은 부풀려진다 — ZTE 653 → 실제 142, meta 1,402 중 하드웨어는 178).
  반증이 찾은 오탐 8건을 반영했다 —
    · APVP 무경계 'Vision Pro' 가 SwitchBot 스마트도어록 'Keypad Vision Pro' 를 흡수
    · MGL 이 'Ray-Ban Meta Glasses' 를 가로챔 → 부정 후방탐색 추가
    · WHP 가 'whoop-de-doo'·'whoop and cheer'(영어 동사)를 잡음
    · OURA 가 오키나와 지명 'Henoko-Oura Bay' 를 잡음
    · ANKSLEEP 이 'Anker SleepLab Pro'(레이더 수면모니터, 제품군 다름)를 흡수
    · NB 의 한국어 '누비아' 는 87.5% 가 Nuvia(퀄컴 CPU 설계사) → 별칭 제거
    · GBK4E 가 세대 없는 총칭 'Galaxy Book Edge' 를 4세대로 귀속
    · HMD 가 'Nokia HMD Global' 에서 브랜드 인접 가드에 오폐기 → nokia 접두를 매칭에 흡수
  무번호 라인 대표 IPAD·MBOOK 은 **의도적으로 제외**했다 — 스치는 언급까지 잡아
  환산 5,200건을 primary 로 만들지만 모델이 특정되지 않아 결함 분석에 쓸 수 없다.

predecessor_code:
  0031·0037 의 코드규칙 유도는 실행 시점 1회성이라 신규 행에 적용되지 않는다.
  여기서 같은 규칙(PREFIX+숫자+SUFFIX 의 숫자 -1)을 다시 돌린다.
"""
import re
from datetime import date

import sqlalchemy as sa
from alembic import op

revision = "0040"
down_revision = "0039"
branch_labels = None
depends_on = None

_CODE_RE = re.compile(r"^([A-Z]+)(\d+)([A-Z]*)$")

# (code, brand, series_code, category, name_en, name_ko, released_at, predecessor_code)
PRODUCTS = [
    ("WHP", "whoop", "WHP", "band", "Whoop", "훕", "2015-05-01", None),
    ("WHP4", "whoop", "WHP", "band", "Whoop 4.0", "훕 4.0", "2021-09-01", "WHP"),
    ("WHP5", "whoop", "WHP", "band", "Whoop 5.0", "훕 5.0", "2025-05-01", "WHP4"),
    ("WHPMG", "whoop", "WHP", "band", "Whoop MG", "훕 MG", "2025-05-01", "WHP4"),
    ("ANKAERO", "anker", "ANK", "buds", "Soundcore AeroFit", "사운드코어 에어로핏", "2023-11-01", None),
    ("ANKLIB4", "anker", "ANK", "buds", "Soundcore Liberty 4", "사운드코어 리버티 4", "2022-10-01", None),
    ("ANKLIB4P", "anker", "ANK", "buds", "Soundcore Liberty 4 Pro", "사운드코어 리버티 4 프로", "2024-09-01", "ANKLIB4"),
    ("ANKLIB5", "anker", "ANK", "buds", "Soundcore Liberty 5", "사운드코어 리버티 5", "2026-05-01", "ANKLIB4"),
    ("ANKLIB5P", "anker", "ANK", "buds", "Soundcore Liberty 5 Pro", "사운드코어 리버티 5 프로", "2026-05-01", "ANKLIB4P"),
    ("ANKP", "anker", "ANK", "buds", "Soundcore P Series", "사운드코어 P 시리즈", "2023-03-01", None),
    ("ANKR", "anker", "ANK", "buds", "Soundcore R Series", "사운드코어 R 시리즈", "2024-08-01", None),
    ("ANKSLEEP", "anker", "ANK", "buds", "Soundcore Sleep", "사운드코어 슬립", "2024-09-01", None),
    ("ANKSPORTX", "anker", "ANK", "buds", "Soundcore Sport X20", "사운드코어 스포츠 X20", "2024-05-01", None),
    ("BTSFP", "apple", "BTS", "buds", "Beats Fit Pro", "비츠 핏 프로", "2021-11-01", None),
    ("BTSPB", "apple", "BTS", "buds", "Powerbeats", "파워비츠", "2016-10-01", None),
    ("BTSPBF", "apple", "BTS", "buds", "Powerbeats Fit", "파워비츠 핏", "2026-02-01", "BTSFP"),
    ("BTSPBP", "apple", "BTS", "buds", "Powerbeats Pro", "파워비츠 프로", "2019-05-01", "BTSPB"),
    ("BTSPBP2", "apple", "BTS", "buds", "Powerbeats Pro 2", "파워비츠 프로 2", "2025-02-01", "BTSPBP"),
    ("BTSSOLOB", "apple", "BTS", "buds", "Beats Solo Buds", "비츠 솔로 버즈", "2024-06-01", None),
    ("BTSSTB", "apple", "BTS", "buds", "Beats Studio Buds", "비츠 스튜디오 버즈", "2021-06-01", None),
    ("BTSSTBP", "apple", "BTS", "buds", "Beats Studio Buds +", "비츠 스튜디오 버즈 플러스", "2023-05-01", "BTSSTB"),
    ("JBLEND", "jbl", "JBL", "buds", "JBL Endurance", "JBL 인듀어런스", "2018-06-01", None),
    ("JBLLIVE", "jbl", "JBL", "buds", "JBL Live", "JBL 라이브", "2019-05-01", None),
    ("JBLTOURP", "jbl", "JBL", "buds", "JBL Tour Pro", "JBL 투어 프로", "2022-10-01", None),
    ("JBLVIBE", "jbl", "JBL", "buds", "JBL Vibe", "JBL 바이브", "2022-05-01", None),
    ("JBLWAVE", "jbl", "JBL", "buds", "JBL Wave", "JBL 웨이브", "2021-09-01", None),
    ("SENMTW4", "sennheiser", "SEN", "buds", "Sennheiser Momentum True Wireless 4", "젠하이저 모멘텀 트루 와이어리스 4", "2024-02-01", "SENMOM"),
    ("MGL", "meta", "MGL", "glasses", "Meta Glasses (Meta own-brand AI glasses)", "메타 글래스", "2026-06-23", None),
    ("MRB", "meta", "MRB", "glasses", "Ray-Ban Meta", "레이밴 메타", "2023-10-17", None),
    ("MRBD", "meta", "MRB", "glasses", "Meta Ray-Ban Display", "메타 레이밴 디스플레이", "2025-09-30", None),
    ("OKM", "meta", "OKM", "glasses", "Oakley Meta (HSTN / Vanguard)", "오클리 메타", "2025-07-11", None),
    ("RKD", "rokid", "RKD", "glasses", "Rokid AR glasses (Max / Max 2)", "로키드 AR 글래스", "2023-05-01", None),
    ("GGL", "samsung", "GGL", "glasses", "Galaxy Glasses", "갤럭시 글래스", "2026-09-01", None),
    ("VTR", "viture", "VTR", "glasses", "Viture XR glasses (One / Pro / Beast)", "Viture XR 글래스", "2023-05-01", None),
    ("XRL", "xreal", "XRL", "glasses", "Xreal AR glasses (Air / Air 2 / One / Beam, ex-Nreal)", "엑스리얼 AR 글래스", "2023-05-01", None),
    ("ANKQ", "anker", "ANK", "headphone", "Soundcore Life Q Series", "사운드코어 라이프 Q 시리즈", "2020-08-01", None),
    ("ANKSPACE", "anker", "ANK", "headphone", "Soundcore Space", "사운드코어 스페이스", "2022-09-01", None),
    ("BTS360", "apple", "BTS", "headphone", "Beats 360", "비츠 360", "2026-08-01", "BTSSTP"),
    ("BTSSOLO4", "apple", "BTS", "headphone", "Beats Solo 4", "비츠 솔로 4", "2024-05-01", None),
    ("BTSSTP", "apple", "BTS", "headphone", "Beats Studio Pro", "비츠 스튜디오 프로", "2023-07-01", None),
    ("JBLTOUR1", "jbl", "JBL", "headphone", "JBL Tour One", "JBL 투어 원", "2021-08-01", None),
    ("JBLTUNE", "jbl", "JBL", "headphone", "JBL Tune", "JBL 튠", "2019-01-01", None),
    ("NTHP1", "nothing", "NTH", "headphone", "Nothing Headphone (1)", "낫싱 헤드폰 (1)", "2025-07-01", None),
    ("NTHPA", "nothing", "NTH", "headphone", "Nothing Headphone (a)", "낫싱 헤드폰 (a)", "2026-06-01", "NTHP1"),
    ("SENACC", "sennheiser", "SEN", "headphone", "Sennheiser Accentum", "젠하이저 액센텀", "2023-11-01", None),
    ("SENM4", "sennheiser", "SEN", "headphone", "Sennheiser Momentum 4 Wireless", "젠하이저 모멘텀 4 와이어리스", "2022-08-01", "SENMOM"),
    ("SENMOM", "sennheiser", "SEN", "headphone", "Sennheiser Momentum", "젠하이저 모멘텀", "2012-09-01", None),
    ("MBAIR", "apple", "MB", "laptop", "MacBook Air", "맥북 에어 (시리즈)", "2008-01-29", None),
    ("MBPRO", "apple", "MB", "laptop", "MacBook Pro", "맥북 프로 (시리즈)", "2006-01-10", None),
    ("MSSFB", "microsoft", "MSSF", "laptop", "Microsoft Surface Book", "서피스 북 (시리즈)", "2015-10-26", None),
    ("MSSFL", "microsoft", "MSSF", "laptop", "Microsoft Surface Laptop", "서피스 랩탑 (시리즈)", "2017-06-15", None),
    ("GBK", "samsung", "GBK", "laptop", "Galaxy Book", "갤럭시 북 (시리즈)", "2017-04-01", None),
    ("GBK2", "samsung", "GBK", "laptop", "Galaxy Book2", "갤럭시 북2", "2022-04-01", "GBK"),
    ("GBK2P", "samsung", "GBK", "laptop", "Galaxy Book2 Pro", "갤럭시 북2 프로", "2022-04-01", None),
    ("GBK3", "samsung", "GBK", "laptop", "Galaxy Book3", "갤럭시 북3", "2023-02-17", "GBK2"),
    ("GBK3P", "samsung", "GBK", "laptop", "Galaxy Book3 Pro", "갤럭시 북3 프로", "2023-02-17", "GBK2P"),
    ("GBK3P3", "samsung", "GBK", "laptop", "Galaxy Book3 Pro 360", "갤럭시 북3 프로 360", "2023-02-17", None),
    ("GBK3U", "samsung", "GBK", "laptop", "Galaxy Book3 Ultra", "갤럭시 북3 울트라", "2023-02-17", None),
    ("GBK4", "samsung", "GBK", "laptop", "Galaxy Book4", "갤럭시 북4", "2023-12-15", "GBK3"),
    ("GBK4E", "samsung", "GBK", "laptop", "Galaxy Book4 Edge", "갤럭시 북4 엣지", "2024-06-18", None),
    ("GBK4P", "samsung", "GBK", "laptop", "Galaxy Book4 Pro", "갤럭시 북4 프로", "2023-12-15", "GBK3P"),
    ("GBK4U", "samsung", "GBK", "laptop", "Galaxy Book4 Ultra", "갤럭시 북4 울트라", "2023-12-15", "GBK3U"),
    ("GBK5", "samsung", "GBK", "laptop", "Galaxy Book5", "갤럭시 북5", "2025-03-11", "GBK4"),
    ("GBK5P", "samsung", "GBK", "laptop", "Galaxy Book5 Pro", "갤럭시 북5 프로", "2025-03-11", "GBK4P"),
    ("GBK5P3", "samsung", "GBK", "laptop", "Galaxy Book5 Pro 360", "갤럭시 북5 프로 360", "2024-12-02", "GBK3P3"),
    ("GBK6", "samsung", "GBK", "laptop", "Galaxy Book6", "갤럭시 북6", "2026-02-18", "GBK5"),
    ("GBK6E", "samsung", "GBK", "laptop", "Galaxy Book6 Edge", "갤럭시 북6 엣지", "2026-06-15", "GBK4E"),
    ("GBK6P", "samsung", "GBK", "laptop", "Galaxy Book6 Pro", "갤럭시 북6 프로", "2026-02-18", "GBK5P"),
    ("GBK6U", "samsung", "GBK", "laptop", "Galaxy Book6 Ultra", "갤럭시 북6 울트라", "2026-02-18", "GBK4U"),
    ("GBKFLX", "samsung", "GBK", "laptop", "Galaxy Book Flex", "갤럭시 북 플렉스", "2020-02-13", None),
    ("GBKGO", "samsung", "GBK", "laptop", "Galaxy Book Go", "갤럭시 북 고", "2021-06-10", None),
    ("GBKS", "samsung", "GBK", "laptop", "Galaxy Book S", "갤럭시 북 S", "2020-02-13", None),
    ("FP", "fairphone", "FP", "phone", "Fairphone", "페어폰 (시리즈)", "2013-12-12", None),
    ("FP2", "fairphone", "FP", "phone", "Fairphone 2", "페어폰 2", "2015-12-18", None),
    ("FP3", "fairphone", "FP", "phone", "Fairphone 3", "페어폰 3", "2019-09-03", "FP2"),
    ("FP4", "fairphone", "FP", "phone", "Fairphone 4", "페어폰 4", "2021-10-25", "FP3"),
    ("FP5", "fairphone", "FP", "phone", "Fairphone 5", "페어폰 5", "2023-09-14", "FP4"),
    ("FP6", "fairphone", "FP", "phone", "Fairphone 6", "페어폰 6", "2025-06-25", "FP5"),
    ("HMD", "hmd", "HMD", "phone", "HMD Global", "HMD 글로벌 (시리즈)", "2024-04-08", None),
    ("HMDSKY", "hmd", "HMD", "phone", "HMD Skyline", "HMD 스카이라인", "2024-07-16", None),
    ("MSSFD", "microsoft", "MSSF", "phone", "Microsoft Surface Duo", "서피스 듀오", "2020-09-10", None),
    ("NB", "nubia", "NB", "phone", "nubia", "누비아 (시리즈)", "2012-12-26", None),
    ("NBNEO", "nubia", "NB", "phone", "nubia Neo", "누비아 네오", "2023-11-01", None),
    ("NBRM", "nubia", "NBRM", "phone", "RedMagic", "레드매직 (시리즈)", "2018-04-25", None),
    ("NBRM10", "nubia", "NBRM", "phone", "RedMagic 10", "레드매직 10", "2024-11-14", None),
    ("NBRM11P", "nubia", "NBRM", "phone", "RedMagic 11 Pro", "레드매직 11 프로", "2025-10-16", "NBRM10"),
    ("NBZ", "nubia", "NB", "phone", "nubia Z", "누비아 Z (시리즈)", "2012-12-26", None),
    ("SHAQ", "sharp", "SH", "phone", "Sharp Aquos", "샤프 아쿠오스 (시리즈)", "2011-02-01", None),
    ("ZTEAXON", "zte", "ZTE", "phone", "ZTE Axon", "ZTE 액손 (시리즈)", "2015-07-14", None),
    ("ZTEBLADE", "zte", "ZTE", "phone", "ZTE Blade", "ZTE 블레이드 (시리즈)", "2010-11-01", None),
    ("AZHELIO", "amazfit", "XMW", "ring", "Amazfit Helio Ring", "어메이즈핏 헬리오 링", "2024-06-01", None),
    ("OURA", "oura", "OUR", "ring", "Oura Ring", "오우라 링", "2018-10-01", None),
    ("OURA3", "oura", "OUR", "ring", "Oura Ring Gen3", "오우라 링 3세대", "2021-10-01", "OURA"),
    ("OURA4", "oura", "OUR", "ring", "Oura Ring 4", "오우라 링 4", "2024-10-01", "OURA3"),
    ("OURA5", "oura", "OUR", "ring", "Oura Ring 5", "오우라 링 5", "2026-05-01", "OURA4"),
    ("RGC", "ringconn", "RGC", "ring", "RingConn", "링콘", "2023-06-01", None),
    ("RGC2", "ringconn", "RGC", "ring", "RingConn Gen 2", "링콘 젠2", "2024-01-01", "RGC"),
    ("RGC3", "ringconn", "RGC", "ring", "RingConn Gen 3", "링콘 젠3", "2026-06-01", "RGC2"),
    ("ULHR", "ultrahuman", "ULH", "ring", "Ultrahuman Ring", "울트라휴먼 링", "2023-08-01", None),
    ("ULHRA", "ultrahuman", "ULH", "ring", "Ultrahuman Ring Air", "울트라휴먼 링 에어", "2023-08-01", "ULHR"),
    ("IPADAIR", "apple", "IPAD", "tablet", "iPad Air", "아이패드 에어 (시리즈)", "2013-11-01", None),
    ("IPADMINI", "apple", "IPAD", "tablet", "iPad mini", "아이패드 미니 (시리즈)", "2012-11-02", None),
    ("IPADPRO", "apple", "IPAD", "tablet", "iPad Pro", "아이패드 프로 (시리즈)", "2015-11-11", None),
    ("MSSFP", "microsoft", "MSSF", "tablet", "Microsoft Surface Pro", "서피스 프로 (시리즈)", "2013-02-09", None),
    ("NBRMAST", "nubia", "NBRM", "tablet", "RedMagic Astra", "레드매직 아스트라", "2025-10-01", None),
    ("TCLNXT", "tcl", "TCL", "tablet", "TCL NXTPAPER", "TCL 넥스트페이퍼 (시리즈)", "2021-06-01", None),
    ("APVP", "apple", "APV", "xr", "Apple Vision Pro", "애플 비전 프로", "2024-02-02", None),
    ("APVPM5", "apple", "APV", "xr", "Apple Vision Pro (M5)", "애플 비전 프로 (M5)", "2025-10-22", "APVP"),
    ("MQ", "meta", "MQ", "xr", "Meta Quest (Oculus Quest)", "메타 퀘스트", "2019-05-21", None),
    ("MQ2", "meta", "MQ", "xr", "Meta Quest 2", "메타 퀘스트 2", "2020-10-13", "MQ"),
    ("MQ3", "meta", "MQ", "xr", "Meta Quest 3", "메타 퀘스트 3", "2023-10-10", "MQ2"),
    ("MQ3S", "meta", "MQ", "xr", "Meta Quest 3S", "메타 퀘스트 3S", "2024-10-15", "MQ2"),
    ("MQP", "meta", "MQ", "xr", "Meta Quest Pro", "메타 퀘스트 프로", "2022-10-25", None),
    ("MRFT", "meta", "MRFT", "xr", "Oculus Rift", "오큘러스 리프트", "2016-03-28", None),
    ("GVR", "samsung", "GVR", "xr", "Gear VR", "기어 VR", "2015-11-20", None),
    ("GXR", "samsung", "GXR", "xr", "Galaxy XR (Project Moohan)", "갤럭시 XR", "2025-10-22", None),
]


def upgrade():
    conn = op.get_bind()
    for code, brand, series, cat, en, ko, rel, pred in PRODUCTS:
        conn.execute(
            sa.text("""
                INSERT INTO products
                    (code, series_code, name_en, name_ko, released_at,
                     predecessor_code, brand, category, is_active, created_at)
                VALUES (:c, :s, :en, :ko, :rel, :pred, :brand, :cat, true, now())
                ON CONFLICT (code) DO NOTHING
            """),
            # asyncpg 는 date 컬럼에 문자열 바인딩을 거부한다
            {"c": code, "s": series, "en": en, "ko": ko,
              "rel": date.fromisoformat(rel), "pred": pred,
              "brand": brand, "cat": cat},
        )

    have = {r[0] for r in conn.execute(sa.text("SELECT code FROM products")).fetchall()}
    for code, *_ in PRODUCTS:
        m = _CODE_RE.match(code)
        if not m:
            continue
        prev = f"{m.group(1)}{int(m.group(2)) - 1}{m.group(3)}"
        if prev in have and prev != code:
            conn.execute(
                sa.text("UPDATE products SET predecessor_code = :p "
                        "WHERE code = :c AND predecessor_code IS NULL"),
                {"p": prev, "c": code},
            )


def downgrade():
    conn = op.get_bind()
    conn.execute(
        sa.text("DELETE FROM products WHERE code = ANY(:codes) AND id NOT IN "
                "(SELECT DISTINCT product_id FROM voc_records WHERE product_id IS NOT NULL "
                " UNION SELECT DISTINCT product_id FROM voc_product_links)"),
        {"codes": [p[0] for p in PRODUCTS]},
    )
