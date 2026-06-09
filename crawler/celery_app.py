from celery import Celery
from celery.schedules import crontab
import os

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

# @lat: app — [[crawler#Platform Strategy]]의 beat_schedule 포함.
app = Celery(
    "signalforge",
    broker=REDIS_URL,
    backend=REDIS_URL,
    include=["tasks"],
)

app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    # 재시도 설정
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    # 워커 동시성
    worker_prefetch_multiplier=1,
)

# 주기적 스케줄 (Celery Beat)
app.conf.beat_schedule = {
    # Reddit: 1시간마다
    "crawl-reddit-hourly": {
        "task": "tasks.crawl_platform",
        "schedule": 3600.0,
        "args": ("reddit", None, None),
    },
    # Twitter: 2시간마다
    "crawl-twitter-2h": {
        "task": "tasks.crawl_platform",
        "schedule": 7200.0,
        "args": ("twitter", None, None),
    },
    # 2026-06-03 Track D: Bluesky — Twitter 무료 대안 1순위, AT Protocol XRPC.
    # BLUESKY_HANDLE/PASSWORD 가 .env 에 없으면 crawler 가 graceful skip.
    "crawl-bluesky-2h": {
        "task": "tasks.crawl_platform",
        "schedule": 7200.0,
        "args": ("bluesky", None, None),
    },
    # Amazon US/DE/JP/KR: 6시간마다
    "crawl-amazon-us-6h": {
        "task": "tasks.crawl_platform",
        "schedule": 21600.0,
        "args": ("amazon_us", None, None),
    },
    "crawl-amazon-de-6h": {
        "task": "tasks.crawl_platform",
        "schedule": 21600.0,
        "args": ("amazon_de", None, None),
    },
    "crawl-amazon-jp-6h": {
        "task": "tasks.crawl_platform",
        "schedule": 21600.0,
        "args": ("amazon_jp", None, None),
    },
    "crawl-amazon-kr-6h": {
        "task": "tasks.crawl_platform",
        "schedule": 21600.0,
        "args": ("amazon_kr", None, None),
    },
    # Best Buy: 6시간마다
    "crawl-bestbuy-6h": {
        "task": "tasks.crawl_platform",
        "schedule": 21600.0,
        "args": ("bestbuy", None, None),
    },
    # Clien: 2시간마다
    "crawl-clien-2h": {
        "task": "tasks.crawl_platform",
        "schedule": 7200.0,
        "args": ("clien", None, None),
    },
    # ppomppu: 2시간마다
    "crawl-ppomppu-2h": {
        "task": "tasks.crawl_platform",
        "schedule": 7200.0,
        "args": ("ppomppu", None, None),
    },
    # DCInside: 2시간마다 (멱등·심층 크롤이라 신규 글만 누적)
    "crawl-dcinside-2h": {
        "task": "tasks.crawl_platform",
        "schedule": 7200.0,
        "args": ("dcinside", None, None),
    },
    # GSMArena: 6시간마다 (디바이스별 사용자 리뷰, 글로벌 영문 VOC)
    "crawl-gsmarena-6h": {
        "task": "tasks.crawl_platform",
        "schedule": 21600.0,
        "args": ("gsmarena", None, None),
    },
    # Harvest 4 H4: GSMArena Forum — samsung-phones-9.php 동적 발견 (S26/Z Trifold/A57 등)
    "crawl-gsmarena_forum-6h": {
        "task": "tasks.crawl_platform",
        "schedule": 21600.0,
        "args": ("gsmarena_forum", None, None),
    },
    # 신규 한국 커뮤니티 — 로테이션 분산 (트래픽 분산, 각 사이트 부하 최소화)
    "crawl-fmkorea-3h":    {"task":"tasks.crawl_platform","schedule":10800.0,"args":("fmkorea",None,None)},
    "crawl-mlbpark-3h":    {"task":"tasks.crawl_platform","schedule":10800.0,"args":("mlbpark",None,None)},
    "crawl-theqoo-3h":     {"task":"tasks.crawl_platform","schedule":10800.0,"args":("theqoo",None,None)},
    "crawl-bobaedream-6h": {"task":"tasks.crawl_platform","schedule":21600.0,"args":("bobaedream",None,None)},
    # 신규 글로벌·삼성·국내 (분산 주기 — 트래픽 분산으로 차단 회피)
    "crawl-samsung_community-4h": {"task":"tasks.crawl_platform","schedule":14400.0,"args":("samsung_community",None,None)},
    "crawl-hackernews-2h":        {"task":"tasks.crawl_platform","schedule": 7200.0,"args":("hackernews",None,None)},
    "crawl-stackexchange-6h":     {"task":"tasks.crawl_platform","schedule":21600.0,"args":("stackexchange",None,None)},
    "crawl-lemmy-4h":             {"task":"tasks.crawl_platform","schedule":14400.0,"args":("lemmy",None,None)},
    "crawl-ruliweb-3h":           {"task":"tasks.crawl_platform","schedule":10800.0,"args":("ruliweb",None,None)},
    # XDA: 4시간마다
    "crawl-xda-4h": {
        "task": "tasks.crawl_platform",
        "schedule": 14400.0,
        "args": ("xda", None, None),
    },
    # 9to5Google: 6시간마다
    "crawl-9to5google-6h": {
        "task": "tasks.crawl_platform",
        "schedule": 21600.0,
        "args": ("9to5google", None, None),
    },
    # Naver Cafe: 4시간마다
    "crawl-naver-cafe-4h": {
        "task": "tasks.crawl_platform",
        "schedule": 14400.0,
        "args": ("naver_cafe", None, None),
    },
    # 2026-05-29 신규 추가 (한국 활성 2h, 영문 포럼 4h, RSS 6h)
    "crawl-danawa-2h":         {"task":"tasks.crawl_platform","schedule": 7200.0,"args":("danawa",None,None)},
    "crawl-instiz-2h":         {"task":"tasks.crawl_platform","schedule": 7200.0,"args":("instiz",None,None)},
    "crawl-slrclub-2h":        {"task":"tasks.crawl_platform","schedule": 7200.0,"args":("slrclub",None,None)},
    "crawl-androidcentral-4h": {"task":"tasks.crawl_platform","schedule":14400.0,"args":("androidcentral",None,None)},
    "crawl-phonearena-6h":     {"task":"tasks.crawl_platform","schedule":21600.0,"args":("phonearena",None,None)},
    # 2026-05-30 2차 신규 추가
    "crawl-quasarzone-2h": {"task":"tasks.crawl_platform","schedule": 7200.0,"args":("quasarzone",None,None)},
    "crawl-dogdrip-2h":    {"task":"tasks.crawl_platform","schedule": 7200.0,"args":("dogdrip",None,None)},
    "crawl-theverge-4h":   {"task":"tasks.crawl_platform","schedule":14400.0,"args":("theverge",None,None)},
    "crawl-engadget-3h":   {"task":"tasks.crawl_platform","schedule":10800.0,"args":("engadget",None,None)},
    "crawl-macrumors-4h":  {"task":"tasks.crawl_platform","schedule":14400.0,"args":("macrumors",None,None)},
    "crawl-androidpolice-4h": {"task":"tasks.crawl_platform","schedule":14400.0,"args":("androidpolice",None,None)},
    # 2026-05-30 3차 신규 추가
    "crawl-dpreview-6h":   {"task":"tasks.crawl_platform","schedule":21600.0,"args":("dpreview",None,None)},
    "crawl-tomsguide-4h":  {"task":"tasks.crawl_platform","schedule":14400.0,"args":("tomsguide",None,None)},
    "crawl-gizmodo-jp-4h": {"task":"tasks.crawl_platform","schedule":14400.0,"args":("gizmodo_jp",None,None)},
    # 2026-05-31 4차 신규 추가 (AU+IN+ES)
    "crawl-ausdroid-12h":  {"task":"tasks.crawl_platform","schedule":43200.0,"args":("ausdroid",None,None)},
    "crawl-gizmodo-au-4h": {"task":"tasks.crawl_platform","schedule":14400.0,"args":("gizmodo_au",None,None)},
    "crawl-gadgets360-4h": {"task":"tasks.crawl_platform","schedule":14400.0,"args":("gadgets360",None,None)},
    "crawl-xataka-3h":     {"task":"tasks.crawl_platform","schedule":10800.0,"args":("xataka",None,None)},
    # 2026-05-31 5차 신규 추가
    "crawl-tecnoblog-4h":    {"task":"tasks.crawl_platform","schedule":14400.0,"args":("tecnoblog",None,None)},
    "crawl-tudocelular-4h":  {"task":"tasks.crawl_platform","schedule":14400.0,"args":("tudocelular",None,None)},
    "crawl-computerbase-4h": {"task":"tasks.crawl_platform","schedule":14400.0,"args":("computerbase",None,None)},
    "crawl-lowyat-4h":       {"task":"tasks.crawl_platform","schedule":14400.0,"args":("lowyat",None,None)},
    # 2026-05-31 6차 신규 추가
    "crawl-shiftdelete-4h": {"task":"tasks.crawl_platform","schedule":14400.0,"args":("shiftdelete",None,None)},
    "crawl-frandroid-4h":   {"task":"tasks.crawl_platform","schedule":14400.0,"args":("frandroid",None,None)},
    "crawl-xataka-mx-6h":   {"task":"tasks.crawl_platform","schedule":21600.0,"args":("xataka_mx",None,None)},
    # 2026-05-31 6차 신규 추가
    "crawl-xataka-mx-6h":    {"task":"tasks.crawl_platform","schedule":21600.0,"args":("xataka_mx",None,None)},
    # 2026-05-31 6차 신규 추가 (FR)
    "crawl-frandroid-4h":    {"task":"tasks.crawl_platform","schedule":14400.0,"args":("frandroid",None,None)},
    # 2026-06-01 7차 신규 추가 (IT)
    "crawl-hwupgrade-6h":    {"task":"tasks.crawl_platform","schedule":21600.0,"args":("hwupgrade",None,None)},
    # 2026-06-01 7차 신규 추가 (AE — 아랍어 첫 사이트)
    "crawl-arageek-6h":      {"task":"tasks.crawl_platform","schedule":21600.0,"args":("arageek",None,None)},
    # 2026-06-01 7차 신규 추가 (TR)
    "crawl-donanimhaber-4h": {"task":"tasks.crawl_platform","schedule":14400.0,"args":("donanimhaber",None,None)},
    # 2026-06-01 7차 신규 추가 (CA)
    "crawl-mobilesyrup-4h":  {"task":"tasks.crawl_platform","schedule":14400.0,"args":("mobilesyrup",None,None)},
    # 2026-06-01 8차 신규 추가 (VN)
    "crawl-tinhte-4h":       {"task":"tasks.crawl_platform","schedule":14400.0,"args":("tinhte",None,None)},
    # 2026-06-01 9차 신규 추가 (NL)
    "crawl-tweakers-6h":     {"task":"tasks.crawl_platform","schedule":21600.0,"args":("tweakers",None,None)},
    # 2026-06-01 10차 신규 추가 (ZA — 아프리카 첫 사이트)
    "crawl-mybroadband-6h":  {"task":"tasks.crawl_platform","schedule":21600.0,"args":("mybroadband",None,None)},
    # 2026-06-01 11차 신규 추가 (PL)
    "crawl-telepolis-6h":    {"task":"tasks.crawl_platform","schedule":21600.0,"args":("telepolis",None,None)},
    # 2026-06-01 13차 신규 추가 (SE) — Cloudflare 우회: 공식 RSS 3채널
    "crawl-sweclockers-6h":  {"task":"tasks.crawl_platform","schedule":21600.0,"args":("sweclockers",None,None)},
    # 2026-06-01 14차 신규 추가 (NG — 나이지리아/범아프리카 영문 IT)
    "crawl-techcabal-6h":    {"task":"tasks.crawl_platform","schedule":21600.0,"args":("techcabal",None,None)},
    # 2026-06-01 15차 신규 추가 (TH — 태국 Sanook Hitech)
    "crawl-sanook-6h":       {"task":"tasks.crawl_platform","schedule":21600.0,"args":("sanook",None,None)},
    # 2026-06-01 16차 신규 추가 (SE — Mobil.se 스웨덴 모바일 전문지)
    "crawl-mobil-se-6h":     {"task":"tasks.crawl_platform","schedule":21600.0,"args":("mobil_se",None,None)},
    # 2026-06-01 17차 신규 추가 (KE — Tech In Africa 케냐/범아프리카 영문 IT)
    "crawl-techinafrica-6h": {"task":"tasks.crawl_platform","schedule":21600.0,"args":("techinafrica",None,None)},
    # 2026-06-01 19차 신규 추가 (ID — Kompas Tekno 인도네시아 1위 매체 IT 섹션, kaskus 차단 대안)
    "crawl-kompas-4h":       {"task":"tasks.crawl_platform","schedule":14400.0,"args":("kompas",None,None)},
    # 2026-06-06 R28-harvest 트랙 E: beat 누락 9건 보완 (PLATFORM_MAP + DB active 인데 fire 안되던 항목)
    # Discovery 에서 100h+ 미실행으로 식별 → schedule 등록.
    # 발행 빈도/지역 특성 따라 4h~12h 분산 (RSS 적은 곳은 6h, 활발 곳은 4h, 발행 드문 곳은 12h).
    "crawl-gigazine-3h":     {"task":"tasks.crawl_platform","schedule":10800.0,"args":("gigazine",None,None)},
    "crawl-gsmchoice-4h":    {"task":"tasks.crawl_platform","schedule":14400.0,"args":("gsmchoice",None,None)},
    "crawl-hipertextual-4h": {"task":"tasks.crawl_platform","schedule":14400.0,"args":("hipertextual",None,None)},
    "crawl-inside-handy-6h": {"task":"tasks.crawl_platform","schedule":21600.0,"args":("inside_handy",None,None)},
    "crawl-ithome-6h":       {"task":"tasks.crawl_platform","schedule":21600.0,"args":("ithome",None,None)},
    "crawl-mobile-review-6h":{"task":"tasks.crawl_platform","schedule":21600.0,"args":("mobile_review",None,None)},
    "crawl-mysmartprice-3h": {"task":"tasks.crawl_platform","schedule":10800.0,"args":("mysmartprice",None,None)},
    "crawl-sammobile-3h":    {"task":"tasks.crawl_platform","schedule":10800.0,"args":("sammobile",None,None)},
    "crawl-sammyfans-3h":    {"task":"tasks.crawl_platform","schedule":10800.0,"args":("sammyfans",None,None)},
    # 2026-06-06 신규 사이트 2개 추가 (R28-harvest 트랙 C: 신규 collector)
    # NotebookCheck (DE/영문, 모바일 디바이스 전문) — Cloudflare → Google News RSS
    "crawl-notebookcheck-3h":{"task":"tasks.crawl_platform","schedule":10800.0,"args":("notebookcheck",None,None)},
    # ZDNet Korea (KR, 한국 IT 매체) — 검색 페이지 + 기사 OG meta
    "crawl-zdnet-kr-4h":     {"task":"tasks.crawl_platform","schedule":14400.0,"args":("zdnet_kr",None,None)},
    # 2026-06-06 Track A 복구 — reddit_rss (OAuth 미통과 환경의 graceful 대안).
    # platforms 행 active=true 이고 r28까지 251건 누적, 직전 24h 0건 → beat 누락 확인.
    # 4h 주기 — reddit RSS Atom feed 8개 subreddit fan-out, 각 ~25 post + ~25 comment.
    "crawl-reddit-rss-4h":   {"task":"tasks.crawl_platform","schedule":14400.0,"args":("reddit_rss",None,None)},
    # 2026-06-06 Data Harvest 2 트랙 C: ResetEra (US 영문 게이밍·일반 IT 포럼)
    # Cloudflare 차단 → Google News RSS 3쿼리 fan-out (notebookcheck 패턴). 4h.
    # 첫 수집 10건 (Mario Galaxy 등 오탐 제거 후) — Reddit 영문권 차단 보완.
    "crawl-resetera-4h":     {"task":"tasks.crawl_platform","schedule":14400.0,"args":("resetera",None,None)},
    # 2026-06-06 Data Harvest 2 트랙 C: iFixit (US 수리·분해 영문 커뮤니티)
    # News RSS + Answers search API (7개 키워드 fan-out). 4h.
    # 첫 수집 83건 — Galaxy 배터리/화면/포트 수리 VOC 시그널 풍부.
    "crawl-ifixit-4h":       {"task":"tasks.crawl_platform","schedule":14400.0,"args":("ifixit",None,None)},
    # 2026-06-06 Harvest 3 트랙 B: Hardware.fr (FR — forum.hardware.fr gsmgpspda)
    # PHP 게시판 직접 HTML 파싱.  봇 우회: UA + Accept-Language 회전, delay 2-4s.
    # 6시간 주기 (instructions 명시).
    "crawl-hardware-fr-6h":  {"task":"tasks.crawl_platform","schedule":21600.0,"args":("hardware_fr",None,None)},
    # 2026-06-07 Harvest 7 트랙 X4: Phandroid (US, 영문 Android 전문 뉴스).
    # sammobile 패턴 — WordPress RSS /feed/?paged=N. 일반 Android 사이트라 Galaxy 비중 25~40%,
    # 키워드 필터 엄격 (galaxy/samsung/zfold/zflip/oneui/exynos…). 6h.
    "crawl-phandroid-6h":    {"task":"tasks.crawl_platform","schedule":21600.0,"args":("phandroid",None,None)},
    # 2026-06-08 Stage 5: 4PDA (RU, 러시아 최대 모바일/IT 커뮤니티) — Cloudflare 차단 → /feed/ windows-1251.
    # 코드 11KB 이미 완성 (FourPDACrawler in platforms/4pda.py, 2026-05-31). 3시간 주기 (속도 강화).
    "crawl-4pda-3h":         {"task":"tasks.crawl_platform","schedule":10800.0,"args":("4pda",None,None)},
    # 2026-06-08 Stage 5B R4: Kaskus (ID, 인도네시아 최대 종합 포럼) — JSON API 비인증.
    # 코드 12KB 완성 (KaskusCrawler). nginx 403 → 5/15/45s 백오프 + UA 회전 내장. delay 2.5-5.0s 보수.
    # 6h 주기 (가장 느린 delay 감안, kaskus 차단 대안으로 kompas 운영 중이라 보조 채널).
    "crawl-kaskus-6h":       {"task":"tasks.crawl_platform","schedule":21600.0,"args":("kaskus",None,None)},
    # 2026-06-08 Stage 5B R2: AnandTech Forums (US, XenForo 2.3 영문 IT 포럼) — 게스트 tag/thread page-N.
    # 코드 11KB 완성 (AnandTechCrawler, 2026-05-31 사장). 4pda 패턴 등록. 4h 주기 (영문 신호 빈도 보통).
    "crawl-anandtech-4h":    {"task":"tasks.crawl_platform","schedule":14400.0,"args":("anandtech",None,None)},
    # 2026-06-08 Stage 5B R5: DroidSans (TH, 태국 Android 전문 매체) — WordPress RSS sammobile 패턴.
    # 200 OK 무차단, TH voc 29 → 보강. 6h 주기 (TH 신호 빈도 보통, sanook 보조).
    "crawl-droidsans-6h":    {"task":"tasks.crawl_platform","schedule":21600.0,"args":("droidsans",None,None)},
    # 2026-06-08 Stage 5C T1: NL/CA/CN 공백 3국 보강 — 모두 직접 RSS 200 OK, 6h 주기 보수.
    # nu.nl: NL 종합지 Tech, tweakers GN 우회 보완.
    "crawl-nu-nl-6h":        {"task":"tasks.crawl_platform","schedule":21600.0,"args":("nu_nl",None,None)},
    # iPhone in Canada: CA Apple/통신 매체, mobilesyrup 보완.
    "crawl-iphoneincanada-6h":{"task":"tasks.crawl_platform","schedule":21600.0,"args":("iphoneincanada",None,None)},
    # sspai: CN 디지털 매체, ithome 보완 (중·영문 매칭).
    "crawl-sspai-6h":        {"task":"tasks.crawl_platform","schedule":21600.0,"args":("sspai",None,None)},
    # 2026-06-08 Stage 5C T3: JagatReview (ID, kaskus 우회 ID 보강) — WP REST API 무차단. 6h.
    "crawl-jagatreview-6h":  {"task":"tasks.crawl_platform","schedule":21600.0,"args":("jagatreview",None,None)},
    # 2026-06-09 data_grow H4: Mastodon (Global fediverse) — 익명 hashtag API, Bluesky 보조.
    # 3 instance × 6 tag = 18 fan-out, MAX_POSTS=240. 4h 주기 (관대 rate limit + 영문권 활동량 보통).
    "crawl-mastodon-4h":     {"task":"tasks.crawl_platform","schedule":14400.0,"args":("mastodon",None,None)},
    # 2026-06-09 Data Grow R2 I4: arXiv 학술 (cs.HC/CY/MM mobile/wearable). rate limit 매우 관대.
    # 12h 주기 (학술은 변동 느림). MX 필터 강제.
    "crawl-arxiv-12h":       {"task":"tasks.crawl_platform","schedule":43200.0,"args":("arxiv",None,None)},
    # 2026-06-09 Data Grow R3 J3: HackerOne disclosed reports — 모바일 보안 인사이트.
    # 4 query × 25 페이지, 5초 1요청 보수. 12h 주기 (disclosed 갱신 느림).
    "crawl-hackerone-12h":   {"task":"tasks.crawl_platform","schedule":43200.0,"args":("hackerone",None,None)},
    # 2026-06-09 Data Grow R3 J6: Misskey (fediverse JP) — notes/search POST 익명 API.
    # 3 instance × 7 query = 21 fan-out, MAX_POSTS=240. 4h 주기 (mastodon 동일).
    "crawl-misskey-4h":      {"task":"tasks.crawl_platform","schedule":14400.0,"args":("misskey",None,None)},
    # 2026-06-09 Data Grow R3 J4: 4chan /g/ Mobile thread — catalog 1회 + matched thread fan-out.
    # 1 req/s 보수, MAX_THREADS=15, MIN_LEN=20, MX 필터, HTML sanitize. 2h 주기 (활동량 빠른 보드).
    "crawl-fourchan-g-2h":   {"task":"tasks.crawl_platform","schedule":7200.0,"args":("fourchan_g",None,None)},
    # 2026-06-09 Data Grow R4 K2: Pikabu (RU 종합 게시판) — /search HTML 스크래핑.
    # DDoS-Guard 검색 endpoint 만 우회, 5 쿼리 × 10 story, MAX_POSTS=80, MX 필터. 6h 주기 (4PDA 와 동급).
    "crawl-pikabu-6h":       {"task":"tasks.crawl_platform","schedule":21600.0,"args":("pikabu",None,None)},
    # 2026-06-09 Data Grow R4 K3: Quora — 영문 QA (Cloudflare 차단으로 graceful skeleton).
    # probe 403 시 빈 결과 반환. _fetch_topic 만 향후 교체. 6h 주기 (라이브화 시 의미).
    "crawl-quora-6h":        {"task":"tasks.crawl_platform","schedule":21600.0,"args":("quora",None,None)},
    # 2026-06-01 Track G: 품질 모니터링 — 매시 30분
    "health-check-hourly": {
        "task": "tasks.run_health_check",
        "schedule": crontab(minute=30),
    },
    # 2026-06-01 Track E: CSV/Excel Export — 매주 월요일 01:00 UTC
    "csv-export-weekly": {
        "task": "tasks.run_csv_export",
        "schedule": crontab(hour=1, minute=0, day_of_week=1),
        "args": (None,),  # days=None → 전체
    },
    # 2026-06-01 Track D: 알림 — 매시 +05 분에 sentiment_drop / site_dead / issue_spike 평가
    "alert-check-hourly": {
        "task": "tasks.run_alert_check",
        "schedule": crontab(minute=5),
        "args": (False,),  # run_daily=False
    },
    # 2026-06-01 Track D: 일일 요약 — 09 KST (= 00:00 UTC) 에 daily_summary 추가 발송
    "alert-daily-summary": {
        "task": "tasks.run_alert_check",
        "schedule": crontab(hour=0, minute=0),
        "args": (True,),  # run_daily=True
    },
    # 2026-06-01 Track A: Daily / Weekly markdown 리포트
    # 매일 00:00 UTC (= 09 KST) → 어제 UTC 윈도우 집계
    "report-daily-09kst": {
        "task": "tasks.run_daily_report",
        "schedule": crontab(hour=0, minute=0),
    },
    # 매주 월요일 01:00 UTC (= 10 KST) → 최근 7일 윈도우
    "report-weekly-mon": {
        "task": "tasks.run_weekly_report",
        "schedule": crontab(hour=1, minute=0, day_of_week=1),
    },
    # 2026-06-01 Track B: LLM 인사이트 — 매일 00:30 UTC (= 09:30 KST)
    # ANTHROPIC_API_KEY 또는 OPENAI_API_KEY 가 있으면 LLM 호출, 없으면 raw 요약만
    "insight-daily-0930kst": {
        "task": "tasks.run_daily_insight",
        "schedule": crontab(hour=0, minute=30),
    },
    # 2026-06-01 P1-3: mv_voc_daily 자동 REFRESH — 30분마다 CONCURRENTLY
    # Dashboard / Analytics API 가 raw 대신 mv 를 읽음 → p95 응답 단축.
    "refresh-mv-voc-daily-30m": {
        "task": "tasks.refresh_mv_voc_daily",
        "schedule": crontab(minute="*/30"),
    },
    # R11 트랙 D — galaxy_master_timeline MV (History 페이지 응답 가속, 1h 주기)
    "refresh-galaxy-master-timeline-1h": {
        "task": "tasks.refresh_galaxy_master_timeline",
        "schedule": crontab(minute=15),  # 매시 15분
    },
    # R16 트랙 C — kpi_overview MV (dashboard 응답 가속, 10분 주기)
    "refresh-kpi-overview-10m": {
        "task": "tasks.refresh_kpi_overview",
        "schedule": crontab(minute="*/10"),
    },
    # P2 신규 (2026-06-01)
    "ingest-keywords-30m":  {"task":"tasks.run_ingest_keywords","schedule": 1800.0, "args":(1000,20)},
    "refresh-cat-daily-30m":{"task":"tasks.run_refresh_p2_mvs","schedule": 1800.0},
    # P3 신규 (2026-06-02): platform_health + country_daily 30분 refresh
    "refresh-p3-mvs-30m":   {"task":"tasks.run_refresh_p3_mvs","schedule": crontab(minute="*/30")},
    # 2026-06-02 Track E: 운영 품질 일일 보고 — 매일 09:30 KST (= 00:30 UTC)
    # daily_insight (00:30 UTC) 와 동일 슬롯이라 워커 풀에서 병렬 실행되지만
    # quality_report 의 grounding 점수 수집은 어제(target-1) insight 파일을 읽으므로 의존성 없음.
    "quality-report-daily-0930kst": {
        "task": "tasks.run_quality_report",
        "schedule": crontab(hour=0, minute=30),
    },
    # 2026-06-03 Track E: Drive 백업 검증 — 매일 20:00 UTC (= 05:00 KST 다음날)
    # backup-to-drive 가 일일 정해진 시각에 dump → upload 한 뒤 verify-backup.sh 가
    # 최신 dump 의 신선도(24h)·sha256·크기(>1MB) 를 검증. 실패 시 alert_rules.backup_fail
    # (system.backup_ok < 1) 룰을 통해 alert_events INSERT 로 운영자에게 알린다.
    "verify-backup-daily-2000utc": {
        "task": "tasks.verify_backup",
        "schedule": crontab(hour=20, minute=0),
    },
    # 2026-06-04 R10 Track D: 운영 1주 모니터링 — 매일 09:30 KST (= 00:30 UTC).
    # daily_insight / quality_report 와 동일 슬롯이라 워커 풀에서 병렬 실행되지만,
    # weekly_monitor 는 endpoint·history 파일을 읽기만 하므로 의존성 없음.
    "weekly-monitor-daily-0930kst": {
        "task": "tasks.run_weekly_monitor",
        "schedule": crontab(hour=0, minute=30),
    },
    # 2026-06-04 R14 Track E: 운영 실시간 모니터링 — 매시 30분.
    # health_check (매시 30분) 와 같은 슬롯이라 워커 풀에서 병렬 실행되지만 의존성 없음.
    # 위반 발생 시 alert_events 에 INSERT (operations_monitor 룰, cooldown 3600s).
    "operations-monitor-hourly": {
        "task": "tasks.run_operations_monitor",
        "schedule": crontab(minute=30),
    },
    # 2026-06-05 R18 Track D: 운영 상태 일별 적재 — 매일 09:30 KST (= 00:30 UTC).
    # operations-monitor 가 *실시간 위반 감지* 라면 이 task 는 *일별 추세 누적*.
    # reports/ops_status_YYYY-MM-DD.json 한 개씩 저장 → /ops-trend 가 소비.
    "ops-history-daily-0930kst": {
        "task": "tasks.run_ops_history",
        "schedule": crontab(hour=0, minute=30),
    },
    # 2026-06-05 R20 Track C: ops_status 파일 기반 위반 알림 — 매시 35분.
    # operations-monitor (매시 30분, live DB) 직후 5분 오프셋 → 파일 갱신 안정 후 점검.
    # ops_status_TODAY.json 의 violations 를 ops_status_violation 룰(id 80)로 alert_events INSERT.
    "ops-alerts-hourly": {
        "task": "tasks.run_ops_alerts",
        "schedule": crontab(minute=35),
    },
    # 2026-06-05 R21 Track C: ops_status backlog 일괄 처리 — 매시 45분.
    # ops_alerts (매시 35분, TODAY 1일) 직후 10분 오프셋 — 윈도우 처리 (최근 7일).
    # backfill_from_db 가 만든 헤더-only 파일을 alert_events 에서 재구성·dedupe →
    # critical INSERT, warning 누적 요약, info 무시.
    "ops-backlog-processor-hourly": {
        "task": "tasks.run_ops_backlog_processor",
        "schedule": crontab(minute=45),
    },
    # 2026-06-06 R29 Track D: 수집 자동 모니터링 — 매시 50분.
    # 활성 사이트 24h 수집량을 직전 7일 일평균과 비교 → 0건이면 critical,
    # 평소 10% 미만이면 warning. collection_health 룰 (id 81) 로 alert_events INSERT
    # + reports/collection_health_TODAY.json 스냅샷 적재.
    "collection-health-hourly": {
        "task": "tasks.run_collection_health",
        "schedule": crontab(minute=50),
    },
    # 2026-06-06 Harvest 3 Track C: 수집 7일 트렌드 일별 보고 — 매일 09:30 KST.
    # collection_health (매시 30분, 시점 24h) 와 보완 관계 — *기간* 7일 트렌드를
    # 누적·분류·markdown 보고. reports/collection_trend_YYYY-MM-DD.{json,md}.
    # backend /_internal/collection-trend-history?days=14 가 누적 json 소비.
    "collection-trend-daily-0930kst": {
        "task": "tasks.run_collection_trend",
        "schedule": crontab(hour=0, minute=30),
        "args": (7,),
    },
    # 2026-06-05 R19 Track B: /dashboard/overview 자동 워밍업 — 매 5분.
    # DashboardService.get_overview 는 @redis_cache(ttl=120s) 인데, 사용자 첫
    # 진입 시 MISS 면 50~60ms 소요.  5 분 주기 (= ttl 의 2.5배) 로 8 case 호출 →
    # 항상 HIT 유지 → 첫 진입 < 5ms 목표.  실패해도 본 운영엔 영향 없음.
    "warm-dashboard-overview-5m": {
        "task": "tasks.warm_dashboard_cache",
        "schedule": crontab(minute="*/5"),
    },
    # 2026-06-05 R20 Track E: 백필 안전장치 실 운영 모니터링 — 매일 09:30 KST.
    # reports/backfill_audit.jsonl 의 최근 7일 row 를 스캔하여 PRESERVE_EXISTING
    # 미설정 / BACKUP_BEFORE 비활성 / DRY_RUN 우회 / status=error 를 자동 탐지.
    # R18 사고 재발 방지 — 운영 정책 준수 여부를 자동 보증.
    "backfill-audit-monitor-daily-0930kst": {
        "task": "tasks.run_backfill_audit_monitor",
        "schedule": crontab(hour=0, minute=30),
    },
    # 2026-06-06 R28-harvest 트랙 D: alert_events → Slack 자동 송출 — 매 5분.
    # operations_monitor / ops_alerts / collection_health 가 INSERT 한 alert_events 중
    # dispatched_channels 에 slack 라벨이 없는 행을 24h 룩백으로 모아 Incoming Webhook
    # POST. ALERT_WEBHOOK_URL 미설정 → dry-run (라벨 'slack:dry' 만 추가).
    "alert-slack-dispatch-5m": {
        "task": "tasks.run_alert_slack_dispatch",
        "schedule": crontab(minute="*/5"),
        "args": (50, 24),
    },
    # 2026-06-05 R26: validator 후크 폴링 — 매 5분.
    # reports/ + docs/dashboard/ 의 R*.md 를 mtime 기준 스캔하여 새 보고서
    # 발견 즉시 workflow_validator 를 자동 적용.  R25 회고 (archive 부재
    # self-report drift) 같은 사고를 *작성 시점에* 캡처한다.  watchdog 미설치
    # 환경 호환 위해 polling 방식.  state: reports/validator_hook_state.json.
    "validator-hook-5m": {
        "task": "tasks.run_validator_hook",
        "schedule": crontab(minute="*/5"),
    },
    # 2026-06-07 Stage 4.5 Y1: 송신 측 양방향 자동 동기화 — 매 30분.
    # backup-to-drive (DB dump → Drive) + LATEST.json 갱신.  수신 측은 LATEST.json
    # sha256 변동만 보고 delta pull → 서비스에 즉시 반영 (Y2 트랙).
    # 환경: AUTO_SYNC_DRY_RUN=true 로 시뮬레이션 / AUTO_SYNC_WITH_SIF=1 로 SIF 포함.
    # 잠금: /tmp/sf_sync_to.lock (수동 sync-to-drive.sh 와 경합 방지).
    # audit: logs/audit/auto_sync.jsonl (round=auto_sync track=Y1).
    "auto-sync-to-drive-30m": {
        "task": "tasks.run_auto_sync_to_drive",
        "schedule": crontab(minute="*/30"),
    },
}
