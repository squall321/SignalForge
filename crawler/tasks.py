"""
크롤링 Celery 태스크
"""
import os
import sys

# crawler/ 디렉토리를 sys.path 에 보장 — Celery 워커가 어느 cwd 에서 기동되든
# platforms.* / base.* / nlp.* 동적 import 가 항상 동작하도록.
_CRAWLER_DIR = os.path.dirname(os.path.abspath(__file__))
if _CRAWLER_DIR not in sys.path:
    sys.path.insert(0, _CRAWLER_DIR)

import importlib
from celery_app import app
from typing import Optional
import asyncio
import logging

logger = logging.getLogger(__name__)

# 플랫폼 코드 → (모듈 경로, 클래스명)
_CRAWLER_SPECS = {
    "reddit":     ("platforms.reddit",        "RedditCrawler"),
    "twitter":    ("platforms.twitter",       "TwitterCrawler"),
    "amazon_us":  ("platforms.amazon",        "AmazonCrawler"),
    "amazon_de":  ("platforms.amazon",        "AmazonCrawler"),
    "amazon_jp":  ("platforms.amazon",        "AmazonCrawler"),
    "amazon_kr":  ("platforms.amazon",        "AmazonCrawler"),
    "bestbuy":    ("platforms.bestbuy",       "BestBuyCrawler"),
    "clien":      ("platforms.clien",         "ClienCrawler"),
    "ppomppu":    ("platforms.ppomppu",       "PpomppuCrawler"),
    "xda":        ("platforms.xda",           "XDACrawler"),
    "9to5google": ("platforms.nineto5google", "NineTo5GoogleCrawler"),
    "naver_cafe": ("platforms.naver_cafe",    "NaverCafeCrawler"),
    "dcinside":   ("platforms.dcinside",      "DCInsideCrawler"),
    "gsmarena":   ("platforms.gsmarena",      "GSMArenaCrawler"),
    "gsmarena_forum": ("platforms.gsmarena_forum", "GSMArenaForumCrawler"),
    "fmkorea":    ("platforms.fmkorea",       "FMKoreaCrawler"),
    "mlbpark":    ("platforms.mlbpark",       "MLBParkCrawler"),
    "theqoo":     ("platforms.theqoo",        "TheqooCrawler"),
    "bobaedream": ("platforms.bobaedream",    "BobaeDreamCrawler"),
    "samsung_community": ("platforms.samsung_community", "SamsungCommunityCrawler"),
    "hackernews":    ("platforms.hackernews",    "HackerNewsCrawler"),
    "stackexchange": ("platforms.stackexchange", "StackExchangeCrawler"),
    "lemmy":         ("platforms.lemmy",         "LemmyCrawler"),
    "ruliweb":       ("platforms.ruliweb",       "RuliwebCrawler"),
    # 2026-05-29 신규 추가
    "danawa":         ("platforms.danawa",         "DanawaCrawler"),
    "instiz":         ("platforms.instiz",         "InstizCrawler"),
    "slrclub":        ("platforms.slrclub",        "SLRClubCrawler"),
    "phonearena":     ("platforms.phonearena",     "PhoneArenaCrawler"),
    "androidcentral": ("platforms.androidcentral", "AndroidCentralCrawler"),
    # 2026-05-30 2차 신규 추가
    "quasarzone":     ("platforms.quasarzone",     "QuasarzoneCrawler"),
    "dogdrip":        ("platforms.dogdrip",        "DogdripCrawler"),
    "theverge":       ("platforms.theverge",       "TheVergeCrawler"),
    "engadget":       ("platforms.engadget",       "EngadgetCrawler"),
    "macrumors":      ("platforms.macrumors",      "MacRumorsCrawler"),
    "androidpolice":  ("platforms.androidpolice",  "AndroidPoliceCrawler"),
    # 2026-05-30 3차 신규 추가
    "dpreview":       ("platforms.dpreview",       "DPReviewCrawler"),
    "tomsguide":      ("platforms.tomsguide",      "TomsGuideCrawler"),
    "gizmodo_jp":     ("platforms.gizmodo_jp",     "GizmodoJPCrawler"),
    # 2026-05-31 4차 신규 추가 (AU+IN+ES)
    "ausdroid":       ("platforms.ausdroid",       "AusdroidCrawler"),
    "gizmodo_au":     ("platforms.gizmodo_au",     "GizmodoAUCrawler"),
    "gadgets360":     ("platforms.gadgets360",     "Gadgets360Crawler"),
    "xataka":         ("platforms.xataka",         "XatakaCrawler"),
    # 2026-05-31 5차 신규 추가 (BR+DE+MY)
    "tecnoblog":      ("platforms.tecnoblog",      "TecnoblogCrawler"),
    "tudocelular":    ("platforms.tudocelular",    "TudoCelularCrawler"),
    "computerbase":   ("platforms.computerbase",   "ComputerBaseCrawler"),
    "lowyat":         ("platforms.lowyat",         "LowyatCrawler"),
    # 2026-05-31 6차 신규 추가 (TR+FR+MX)
    "shiftdelete":    ("platforms.shiftdelete",    "ShiftDeleteCrawler"),
    "frandroid":      ("platforms.frandroid",      "FrandroidCrawler"),
    "xataka_mx":      ("platforms.xataka_mx",      "XatakaMXCrawler"),
    # 2026-05-31 6차 신규 추가 (MX)
    "xataka_mx":      ("platforms.xataka_mx",      "XatakaMXCrawler"),
    # 2026-05-31 6차 신규 추가 (FR)
    "frandroid":      ("platforms.frandroid",      "FrandroidCrawler"),
    # 2026-06-01 7차 신규 추가 (IT) — Cloudflare 우회: Google News RSS
    "hwupgrade":      ("platforms.hwupgrade",      "HWUpgradeCrawler"),
    # 2026-06-01 7차 신규 추가 (RU) — WP REST API
    "mobile_review":  ("platforms.mobile_review",  "MobileReviewCrawler"),
    # 2026-06-01 7차 신규 추가 (AE — 아랍어 첫 사이트) — WP REST API
    "arageek":        ("platforms.arageek",        "ArageekCrawler"),
    # 2026-06-01 7차 신규 추가 (TR) — 카테고리 페이지네이션 + JSON-LD + 댓글 API
    "donanimhaber":   ("platforms.donanimhaber",   "DonanimHaberCrawler"),
    # 2026-06-01 7차 신규 추가 (CA) — HTML 검색 페이지네이션 + article HTML
    "mobilesyrup":    ("platforms.mobilesyrup",    "MobileSyrupCrawler"),
    # 2026-06-01 8차 신규 추가 (VN) — XenForo + Next.js, RSS + 스레드 SSR 댓글
    "tinhte":         ("platforms.tinhte",         "TinhteCrawler"),
    # 2026-06-01 9차 신규 추가 (NL) — DPG WAF 우회: 공식 RSS 다채널
    "tweakers":       ("platforms.tweakers",       "TweakersCrawler"),
    # 2026-06-01 10차 신규 추가 (ZA — 아프리카 첫 사이트) — Cloudflare 우회: Googlebot UA + Disqus API
    "mybroadband":    ("platforms.mybroadband",    "MyBroadbandCrawler"),
    # 2026-06-01 11차 신규 추가 (PL) — 태그 페이지네이션 + 기사 JSON-LD
    "telepolis":      ("platforms.telepolis",      "TelepolisCrawler"),
    # 2026-06-01 12차 신규 추가 (GLOBAL) — WordPress RSS, Samsung 전문 영문 뉴스
    "sammobile":      ("platforms.sammobile",      "SamMobileCrawler"),
    # 2026-06-01 13차 신규 추가 (SE) — Cloudflare 우회: 공식 RSS 3채널 (nyheter/artiklar/forum)
    "sweclockers":    ("platforms.sweclockers",    "SweClockersCrawler"),
    # 2026-06-01 14차 신규 추가 (NG — 나이지리아/범아프리카 영문 IT) — WP REST API
    "techcabal":      ("platforms.techcabal",      "TechCabalCrawler"),
    # 2026-06-01 15차 신규 추가 (TH — 태국 최대 포털 Sanook 의 IT 섹션) — 태그 페이지네이션 + JSON-LD
    "sanook":         ("platforms.sanook",         "SanookCrawler"),
    # 2026-06-01 16차 신규 추가 (SE — Mobil.se 스웨덴 모바일 전문) — /tagg/samsung ItemList + JSON-LD NewsArticle
    "mobil_se":       ("platforms.mobil_se",       "MobilSeCrawler"),
    # 2026-06-01 17차 신규 추가 (KE — 케냐/범아프리카 영문 IT) — WP REST API + 검색 RSS 폴백
    "techinafrica":   ("platforms.techinafrica",   "TechInAfricaCrawler"),
    # 2026-06-01 18차 신규 추가 (JP — Gigazine 일본 IT 뉴스, gizmodo_jp 보완) — RSS + 일자 archive + 본문 preface 추출
    "gigazine":       ("platforms.gigazine",       "GigazineCrawler"),
    # 2026-06-01 19차 신규 추가 (ID — Kompas Tekno 인도네시아 1위 매체 IT 섹션, kaskus 차단 대안) — 태그 페이지네이션 + JSON-LD + 댓글 API
    "kompas":         ("platforms.kompas",         "KompasCrawler"),
    # 2026-06-01 20차 신규 추가 (CN — IT之家 중국 IT 뉴스) — 메인 RSS 전문 description + Comment JSON API (cmt.ithome.com)
    "ithome":         ("platforms.ithome",         "ITHomeCrawler"),
    # 2026-06-01 21차 신규 추가 (GLOBAL — SammyFans Samsung/Galaxy 전문 영문 뉴스) — Cloudflare 우회: Safari→Firefox UA 폴백 RSS + 글별 댓글 RSS
    "sammyfans":      ("platforms.sammyfans",      "SammyFansCrawler"),
    # 2026-06-01 22차 신규 추가 (GB — GSMchoice 영국 모바일 DB+뉴스) — Cloudflare 우회: Google News RSS (hwupgrade 패턴)
    "gsmchoice":      ("platforms.gsmchoice",      "GSMchoiceCrawler"),
    # 2026-06-01 23차 신규 추가 (ES — Hipertextual 스페인어 IT 매거진, xataka 보완) — tag HTML 410(Gone) → WP REST API search=samsung/galaxy + RSS dc:creator 보강
    "hipertextual":   ("platforms.hipertextual",   "HipertextualCrawler"),
    # 2026-06-01 24차 신규 추가 (DE — Inside-Handy → inside-digital.de 도메인 통합, areamobile 대체) — WordPress RSS 페이지네이션 + 기사 HTML td-post-content 본문 강화
    "inside_handy":   ("platforms.inside_handy",   "InsideHandyCrawler"),
    # 2026-06-01 25차 신규 추가 (IN — MySmartPrice 인도 전자제품 리뷰/뉴스, /gear 섹션) — Cloudflare WAF 차단 (UA/Referer 무관 403) → Google News RSS 9개 키워드 fan-out (hwupgrade 패턴)
    "mysmartprice":   ("platforms.mysmartprice",   "MySmartPriceCrawler"),
    # 2026-06-06 26차 신규 추가 (DE — NotebookCheck 모바일 디바이스 영문 전문지) — Cloudflare Turnstile → Google News RSS (hwupgrade 패턴)
    "notebookcheck":  ("platforms.notebookcheck",  "NotebookCheckCrawler"),
    # 2026-06-06 27차 신규 추가 (KR — ZDNet Korea, 한국 1세대 IT 매체) — 검색 페이지 + 기사 OG meta
    "zdnet_kr":       ("platforms.zdnet_kr",       "ZDNetKoreaCrawler"),
    # 2026-06-06 Track A 복구: reddit_rss — OAuth 키 없이 공개 Atom feed.  reddit (OAuth) 차단 환경의 graceful 대안.
    "reddit_rss":     ("platforms.reddit_rss",     "RedditRSSCrawler"),
    # 2026-06-06 Data Harvest 2 트랙 C: ResetEra — Cloudflare 차단 → Google News RSS (notebookcheck 패턴)
    "resetera":       ("platforms.resetera",       "ReseteraCrawler"),
    # 2026-06-06 Data Harvest 2 트랙 C: iFixit — News RSS + Answers search API (수리·분해 영문 커뮤니티)
    "ifixit":         ("platforms.ifixit",         "IFixitCrawler"),
    # 2026-06-06 Harvest 3 트랙 B: Hardware.fr — forum.hardware.fr (FR) PHP 게시판 직접 HTML 파싱
    # gsmgpspda 카테고리 listing → Galaxy/Samsung 스레드 → 최신 page 글 채집 (UA rotation 적용)
    "hardware_fr":    ("platforms.hardware_fr",    "HardwareFRCrawler"),
    # 2026-06-07 Harvest 7 트랙 X4: Phandroid (US, 영문 Android 전문 뉴스) — WordPress RSS
    # (sammobile 패턴). 일반 Android 사이트라 키워드 필터 엄격 (galaxy/samsung/zfold/zflip/oneui).
    "phandroid":      ("platforms.phandroid",      "PhandroidCrawler"),
    # 2026-06-08 Stage 5: 4PDA (RU, 러시아 최대 모바일 커뮤니티) — Cloudflare 차단 → 메인 RSS /feed/ windows-1251
    # 코드 11KB 이미 완성 (2026-05-31), tasks/celery/DB 3곳 미등록 발견 → 등록만으로 RU voc 165 → +30/cycle 추정
    "4pda":           ("platforms.4pda",           "FourPDACrawler"),
    # 2026-06-08 Stage 5B R4: Kaskus (ID, 인도네시아 최대 종합 포럼) — JSON API 인증 불필요
    # 코드 12KB 이미 완성 (KaskusCrawler in platforms/kaskus.py). nginx IP-rate 403 → 5/15/45s 백오프 + UA 회전 내장.
    # ID voc 158 → 250+ 목표. delay 2.5-5.0s 보수적 (가장 느림).
    "kaskus":         ("platforms.kaskus",         "KaskusCrawler"),
    # 2026-06-08 Stage 5B R3: Bluesky (Global SNS) — AT Protocol XRPC, Twitter 무료 대안 1순위.
    # 코드 8.6KB 사장 (2026-06-03 Track D 셀러리 스케줄만 등록, tasks/DB 누락) — 키 없으면 graceful skip.
    "bluesky":        ("platforms.bluesky",        "BlueskyCrawler"),
    # 2026-06-08 Stage 5B R2: AnandTech Forums (US, XenForo 2.3 영문 IT 포럼).
    # Mobile Devices 서브포럼 403 우회: /tags/{samsung,galaxy,android}/page-N 게스트 진입
    # + 스레드 본문/댓글 page-N. 코드 11KB 이미 완성 (2026-05-31). 4pda 패턴 등록.
    "anandtech":      ("platforms.anandtech",      "AnandTechCrawler"),
    # 2026-06-08 Stage 5B R5: DroidSans (TH, 태국 Android/모바일 전문 매체)
    # WordPress RSS (sammobile 패턴), 200 OK 무차단. TH voc 29 → +N 보강.
    # LIST_PAGES=2 보수 첫 수집 (OOM 안전).
    "droidsans":      ("platforms.droidsans",      "DroidSansCrawler"),
    # 2026-06-08 Stage 5C T1: NL/CA/CN 공백 3국 보강
    # nu.nl (NL, 네덜란드 종합지 Tech 섹션 RSS) — tweakers Cloudflare 우회 보완용 직접 RSS.
    "nu_nl":          ("platforms.nu_nl",          "NuNLCrawler"),
    # iPhone in Canada (CA, Apple 중심이지만 Samsung/통신 비교 기사) — WordPress RSS.
    "iphoneincanada": ("platforms.iphoneincanada", "IPhoneInCanadaCrawler"),
    # sspai 少数派 (CN, 디지털 생산성/소비전자 매체) — ithome 보완 직접 RSS, 중·영문 매칭.
    "sspai":          ("platforms.sspai",          "SspaiCrawler"),
    # 2026-06-08 Stage 5C T3: JagatReview (ID, 인도네시아 IT 리뷰 매체) — kaskus Cloudflare/rate-limit 우회 ID 보강
    # WP REST API /wp-json/wp/v2/posts?search=samsung|galaxy 무차단 200. ID voc 158 → +N 목표.
    "jagatreview":    ("platforms.jagatreview",    "JagatReviewCrawler"),
    # 2026-06-09 data_grow H4: Mastodon (Global fediverse SNS) — 공개 hashtag timeline API 키 불필요.
    # mastodon.social/world/fosstodon × galaxy/samsung/galaxys25/galaxyfold/pixel9/iphone16 fan-out.
    # Bluesky 보조, X.com 대안. rate limit 300req/5분 관대, MX 필터 자동 적용.
    "mastodon":       ("platforms.mastodon",       "MastodonCrawler"),
    # 2026-06-09 Data Grow R2 I4: arXiv 학술 논문 (cs.HC/CY/MM, mobile/wearable/foldable)
    # Atom XML, rate limit 1/3s, abstract 풍부 (mx_rich 상승), MX 필터 강제.
    "arxiv":          ("platforms.arxiv",          "ArxivCrawler"),
    # 2026-06-09 Data Grow R3 J3: HackerOne disclosed reports — 모바일 보안 인사이트 (Samsung/Galaxy/Android/Pixel)
    # api.hackerone.com 익명 v1 hacktivity 엔드포인트. 4 query × 25 = 최대 100/cycle, 5초 1요청 보수.
    # MX 필터 강제 → 모바일 관련 disclosed 만 채택. 12h 주기.
    "hackerone":      ("platforms.hackerone",      "HackerOneCrawler"),
    # 2026-06-09 Data Grow R3 J6: Misskey (fediverse, JP 중심) — 익명 notes/search POST API.
    # misskey.io/design/systems × 7 query (galaxy/samsung/Fold/S25/Z Flip/pixel9/iphone16) fan-out,
    # MAX_POSTS=240. mastodon 의 한·일 voc 보완. MX 필터 강제. 4h 주기.
    "misskey":        ("platforms.misskey",        "MisskeyCrawler"),
    # 2026-06-09 Data Grow R3 J4: 4chan /g/ Mobile thread (익명, /spg/·sgt 스마트폰 일반론)
    # catalog.json + thread/{no}.json 공개 API. 1 req/s 보수, MX 필터 + HTML sanitize 강제. 2h 주기.
    "fourchan_g":     ("platforms.fourchan_g",     "FourchanGCrawler"),
    # 2026-06-09 Data Grow R4 K2: Pikabu (RU 종합 게시판) — 검색 페이지 HTML 스크래핑.
    # DDoS-Guard 우회: /search?q= 200 OK 통과, 단일 세션 쿠키 재사용. 5 쿼리 × 10 story.
    # MAX_POSTS=80, 4PDA 보완 (모바일 전문 vs 일반 사용자). MX 필터 강제. 6h 주기.
    "pikabu":         ("platforms.pikabu",         "PikabuCrawler"),
    # 2026-06-09 Data Grow R4 K3: Quora — 영문 QA (Samsung-Galaxy / iPhone / Android / Smartphones).
    # 현재 Cloudflare managed challenge 로 모든 endpoint 403. graceful 스켈레톤만 등록 — 정책
    # 변경(브라우저 자동화 또는 상용 프록시) 시 _fetch_topic 교체로 즉시 라이브 가동. 6h 주기.
    "quora":          ("platforms.quora",          "QuoraCrawler"),
}

# 모듈을 워커 기동 시점(메인 프로세스)에 정적 로드 → prefork 자식이 sys.modules 상속.
# Celery 워커의 런타임 동적 import 가 cwd/path 문제로 실패하던 것을 회피.
# 깨진 모듈 1개가 워커 전체를 죽이지 않도록 코드별로 격리.
CRAWLER_MAP: dict = {}
for _code, (_mod, _cls) in _CRAWLER_SPECS.items():
    try:
        CRAWLER_MAP[_code] = getattr(importlib.import_module(_mod), _cls)
    except Exception as _e:
        logger.warning(f"크롤러 로드 실패 [{_code}] {_mod}.{_cls}: {_e}")


# @lat: crawl_platform — [[crawler#Celery Task]] 참조.
@app.task(bind=True, max_retries=3, default_retry_delay=300)
def crawl_platform(
    self,
    platform_code: str,
    product_code: Optional[str] = None,
    job_id: Optional[int] = None,
):
    """
    플랫폼 크롤러 실행 태스크
    
    Args:
        platform_code: 플랫폼 코드 (예: 'reddit', 'amazon_us')
        product_code: 특정 제품만 크롤링 (None이면 전 제품)
        job_id: crawl_jobs 레코드 ID (상태 업데이트용)
    """
    logger.info(f"[{platform_code}] 크롤링 시작 (product={product_code}, job_id={job_id})")

    CrawlerClass = CRAWLER_MAP.get(platform_code)
    if CrawlerClass is None:
        logger.error(f"알 수 없거나 로드 실패한 플랫폼: {platform_code}")
        return {"status": "error", "message": f"Unknown/unloaded platform: {platform_code}"}

    try:
        crawler = CrawlerClass(platform_code=platform_code, product_code=product_code, job_id=job_id)
        result = asyncio.run(crawler.run())
        logger.info(f"[{platform_code}] 크롤링 완료: {result.get('items_collected', 0)}건")
        return result
    except Exception as exc:
        logger.exception(f"[{platform_code}] 크롤링 실패: {exc}")
        raise self.retry(exc=exc)


# @lat: run_health_check — [[crawler#Quality Monitoring]] Track G.
@app.task(name="tasks.run_health_check")
def run_health_check():
    """품질 모니터링 1회 — reports/health_YYYY-MM-DD.md 생성 + 알림."""
    from monitoring.health_check import run_health_check as _run
    result = _run()
    logger.info(
        f"[health_check] platforms active/idle/dead = "
        f"{result['platforms_active']}/{result['platforms_idle']}/{result['platforms_dead']} "
        f"alerts={len(result['critical_alerts'])} → {result['report']}"
    )
    return result


# ---------------------------------------------------------------------------
# Track E — CSV Export
# 매주 월요일 01:00 UTC. 재시도 없음 (다음 주 자동 재실행).
# ---------------------------------------------------------------------------
@app.task(name="tasks.run_csv_export", max_retries=0)
def run_csv_export(days: Optional[int] = None) -> dict:
    """
    voc_records + 제품 통계 CSV 익스포트.

    Args:
        days: 최근 N일만 (None 이면 전체). 주간 작업은 None 권장.

    Returns:
        {"voc": {...}, "products": {...}}
    """
    from exports.csv_export import export_voc_csv
    from exports.products_csv import export_products_csv

    logger.info(f"[csv_export] 시작 days={days}")
    voc = asyncio.run(export_voc_csv(days=days))
    prods = asyncio.run(export_products_csv())
    logger.info(
        f"[csv_export] 완료 voc={voc['rows']}행/{voc['bytes']}B "
        f"products={prods['rows']}행/{prods['bytes']}B"
    )
    return {"voc": voc, "products": prods}


# ---------------------------------------------------------------------------
# Track D — Alerts (Slack/Discord webhook)
# beat: 매시 +05 분에 hourly check, 09 KST 트리거 시 daily_summary 포함
# ---------------------------------------------------------------------------
@app.task(name="tasks.run_alert_check", bind=True, max_retries=2, default_retry_delay=60)
def run_alert_check(self, run_daily: bool = False) -> dict:
    """알림 규칙 평가 + 디스패치.

    Args:
        run_daily: True 면 daily_summary 도 발송 (09 KST 트리거에서만 사용)
    """
    try:
        from alerts.rules import check_all_rules
        from alerts.dispatcher import send_alert
    except Exception as exc:  # noqa: BLE001
        logger.exception("alerts 모듈 import 실패: %s", exc)
        return {"status": "error", "message": str(exc)}

    try:
        alerts = check_all_rules(run_daily=run_daily)
    except Exception as exc:  # noqa: BLE001
        logger.exception("규칙 평가 실패: %s", exc)
        raise self.retry(exc=exc)

    sent = logged = failed = 0
    by_rule: dict = {}
    for a in alerts:
        rule = a.get("rule", "?")
        by_rule[rule] = by_rule.get(rule, 0) + 1
        res = send_alert(a["payload"], level=a.get("level", "info"))
        status = res.get("status")
        if status == "sent":
            sent += 1
        elif status == "logged":
            logged += 1
        else:
            failed += 1

    logger.info(
        "[alerts] total=%d sent=%d logged=%d failed=%d by_rule=%s",
        len(alerts), sent, logged, failed, by_rule,
    )
    return {
        "status": "ok",
        "total": len(alerts),
        "sent": sent,
        "logged": logged,
        "failed": failed,
        "by_rule": by_rule,
        "run_daily": run_daily,
    }


# ---------------------------------------------------------------------------
# R28-harvest 트랙 D — alert_events → Slack Incoming Webhook 자동 송출
# beat 스케줄: 매 5분 (crontab(minute='*/5'))
# ALERT_WEBHOOK_URL 미설정 → dry-run, 라벨 'slack:dry' 만 추가 (운영자가 키 입력 시 즉시 활성)
# ---------------------------------------------------------------------------
@app.task(name="tasks.run_alert_slack_dispatch", max_retries=0)
def run_alert_slack_dispatch(limit: int = 50, hours: int = 24) -> dict:
    """미전송 alert_events 를 Slack 으로 송출 + dispatched_channels 라벨 추가.

    Args:
        limit: 1회 tick 에서 처리할 최대 건수 (default 50).
        hours: 조회 룩백 시간 (default 24).
    """
    from insight.slack_notifier import run as slack_run
    result = asyncio.run(slack_run(lookback_hours=hours, limit=limit))
    logger.info(
        "[slack-notifier] found=%s sent=%s dry=%s failed=%s skipped=%s enabled=%s",
        result.get("found"), result.get("sent"), result.get("dry"),
        result.get("failed"), result.get("skipped"), result.get("enabled"),
    )
    return {"status": "ok", **result}


# ---------------------------------------------------------------------------
# Track A — Daily / Weekly Markdown Reports
# beat 스케줄: daily 00:00 UTC (=09 KST), weekly 월 01:00 UTC
# ---------------------------------------------------------------------------
@app.task(name="tasks.run_daily_report", max_retries=0)
def run_daily_report() -> dict:
    """어제 UTC 기준 daily 리포트 생성. 결과 파일 경로 반환."""
    from reports.daily import build_daily_report
    path = asyncio.run(build_daily_report())
    logger.info(f"[daily-report] 생성 완료: {path}")
    return {"status": "ok", "path": str(path)}


@app.task(name="tasks.run_weekly_report", max_retries=0)
def run_weekly_report() -> dict:
    """오늘 UTC 기준 weekly 리포트 (최근 7일) 생성. 결과 파일 경로 반환."""
    from reports.weekly import build_weekly_report
    path = asyncio.run(build_weekly_report())
    logger.info(f"[weekly-report] 생성 완료: {path}")
    return {"status": "ok", "path": str(path)}


# ---------------------------------------------------------------------------
# Track B — LLM 인사이트 (Anthropic / OpenAI)
# beat 스케줄: 00:30 UTC (= 09:30 KST) — daily-report 직후
# ---------------------------------------------------------------------------
@app.task(name="tasks.run_daily_insight", max_retries=0)
def run_daily_insight() -> dict:
    """어제 UTC 기준 VOC 데이터 → LLM 인사이트 .md 생성."""
    from insight.daily_insight import run as build_insight
    path = asyncio.run(build_insight())
    logger.info(f"[daily-insight] 생성 완료: {path}")
    return {"status": "ok", "path": str(path)}


# ---------------------------------------------------------------------------
# R10 Track D — 운영 1주 모니터링 자동화
# beat 스케줄: 매일 00:30 UTC (= 09:30 KST). insight / quality_report 와 같은 슬롯
# (서로 의존성 없음 — weekly_monitor 는 endpoint·history 파일을 읽기만 한다).
# ---------------------------------------------------------------------------
@app.task(name="tasks.run_weekly_monitor", max_retries=0)
def run_weekly_monitor() -> dict:
    """직전 7일 운영 지표 누적 → JSON + MD + Slack 다이제스트 (옵션).

    Harvest 3p (P4) 부터 일별 markdown ``reports/weekly_monitor_YYYY-MM-DD.md`` 추가,
    ALERT_WEBHOOK_URL / SLACK_WEBHOOK_URL 입력 시 Slack 1단 다이제스트 송출.
    """
    from insight.weekly_monitor import run as build_weekly_monitor
    result = asyncio.run(build_weekly_monitor())
    logger.info(
        "[weekly-monitor] 생성 완료: json=%s md=%s alerts=%d slack=%s",
        result.get("json_path"), result.get("md_path"),
        int(result.get("alerts") or 0),
        (result.get("slack") or {}).get("status"),
    )
    return {
        "status": "ok",
        "json_path": str(result.get("json_path")),
        "md_path": str(result.get("md_path")) if result.get("md_path") else None,
        "alerts": int(result.get("alerts") or 0),
        "slack": result.get("slack"),
    }


# ---------------------------------------------------------------------------
# R14 Track E — 운영 1주 모니터링 (매시 점검)
# beat 스케줄: 매시 +30 분 (crontab(minute=30)). weekly_monitor 가 일일 누적이라면
# 이 task 는 *실시간* SLO 위반 감지 — 6 metric → alert_events INSERT.
# ---------------------------------------------------------------------------
@app.task(name="tasks.run_operations_monitor", max_retries=0)
def run_operations_monitor() -> dict:
    """6 metric 점검 + 위반 시 alert_events INSERT (operations_monitor 룰)."""
    from insight.operations_monitor import run as ops_run
    payload = asyncio.run(ops_run(insert=True))
    logger.info(
        "[operations-monitor] status=%s violations=%d inserted=%d",
        payload.get("status"),
        len(payload.get("violations") or []),
        int(payload.get("alert_events_inserted") or 0),
    )
    return {
        "status": payload.get("status"),
        "violations": len(payload.get("violations") or []),
        "inserted": int(payload.get("alert_events_inserted") or 0),
    }


# ---------------------------------------------------------------------------
# R18 Track D — 운영 상태 일별 적재 (ops-status 일별 스냅샷)
# beat 스케줄: 매일 09:30 KST (= 00:30 UTC). operations_monitor 매시 점검과 별개로
# 일 1회 슬림 요약을 reports/ops_status_YYYY-MM-DD.json 으로 누적 → /ops-trend 가 소비.
# ---------------------------------------------------------------------------
@app.task(name="tasks.run_ops_history", max_retries=0)
def run_ops_history() -> dict:
    """ops-status 결과를 reports/ops_status_YYYY-MM-DD.json 으로 적재."""
    from insight.ops_history import run as ops_history_run
    path = asyncio.run(ops_history_run())
    logger.info("[ops-history] 생성 완료: %s", path)
    return {"status": "ok", "path": str(path)}


# ---------------------------------------------------------------------------
# R20 Track C — ops_status 파일 기반 위반 알림 (매시 점검)
# beat 스케줄: 매시 +30 분 (crontab(minute=30)). operations_monitor (live DB 조회) 가
# *실시간* 위반 감지라면 이 task 는 *파일 기반 이중 안전망* — reports/ops_status_TODAY.json
# 의 violations 를 ops_status_violation 룰 (id 80) 로 alert_events INSERT.
# metric 단위 cooldown 1h 적용 → 중복 발화 최소.
# ---------------------------------------------------------------------------
@app.task(name="tasks.run_ops_alerts", max_retries=0)
def run_ops_alerts() -> dict:
    """ops_status_TODAY.json 의 위반을 alert_events 로 전파."""
    from insight.ops_alerts import run as ops_alerts_run
    result = asyncio.run(ops_alerts_run(insert=True))
    logger.info(
        "[ops-alerts] target=%s found=%s violations=%d inserted=%d skipped=%d dist=%s",
        result.get("target_date"),
        result.get("found"),
        int(result.get("violations_count") or 0),
        int(result.get("inserted") or 0),
        int(result.get("skipped_by_cooldown") or 0),
        result.get("severity_distribution"),
    )
    return {
        "status": result.get("status"),
        "found": result.get("found"),
        "violations": int(result.get("violations_count") or 0),
        "inserted": int(result.get("inserted") or 0),
        "skipped": int(result.get("skipped_by_cooldown") or 0),
        "severity_distribution": result.get("severity_distribution"),
    }


# ---------------------------------------------------------------------------
# R21 Track C — ops_status backlog 일괄 처리기
# beat 스케줄: 매시 45분 (crontab(minute=45)).  ops_alerts (매시 35분, TODAY 1일치)
# 직후 10분 오프셋 → 윈도우 처리 충돌 회피.  최근 7일 ops_status_*.json 의 헤더
# violations_count 와 본문 array 차이 (backfill_from_db 헤더만 파일) 를 alert_events
# 에서 재구성·dedupe → critical INSERT, warning 누적 요약, info 무시.
# ---------------------------------------------------------------------------
@app.task(name="tasks.run_ops_backlog_processor", max_retries=0)
def run_ops_backlog_processor() -> dict:
    """ops_status backlog (최근 7일) 일괄 처리 — severity 분류 + 자동 처리."""
    from insight.ops_backlog_processor import process_backlog
    result = asyncio.run(process_backlog(days=7, insert=True))
    actions = result.get("actions") or {}
    logger.info(
        "[ops-backlog] run=%s files=%d window=%s actions=%s",
        result.get("run_id"),
        int(result.get("files_scanned") or 0),
        result.get("window_severity"),
        actions,
    )
    return {
        "status": result.get("status"),
        "run_id": result.get("run_id"),
        "files_scanned": int(result.get("files_scanned") or 0),
        "window_severity": result.get("window_severity"),
        "actions": actions,
    }


# ---------------------------------------------------------------------------
# R29 Track D — 수집 자동 모니터링 (사이트 24h 0건 시 자동 alert)
# beat 스케줄: 매시 +50 분 (crontab(minute=50)).  ops 알림 슬롯 (30/35/45) 와 충돌
# 회피.  각 활성 사이트의 24h voc 카운트를 직전 7일 일평균 베이스라인과 비교 →
# critical (0건) / warning (10% 미만) 위반을 collection_health 룰 (id 81) 로
# alert_events INSERT.  reports/collection_health_YYYY-MM-DD.json 스냅샷 적재.
# ---------------------------------------------------------------------------
@app.task(name="tasks.run_collection_health", max_retries=0)
def run_collection_health() -> dict:
    """활성 사이트 24h 수집량 점검 + 위반 시 alert_events INSERT."""
    from insight.collection_health import run as ch_run
    payload = asyncio.run(ch_run(insert=True, save=True))
    counts = payload.get("violation_counts") or {}
    ae = payload.get("alert_events") or {}
    logger.info(
        "[collection_health] status=%s sites=%d critical=%d warning=%d "
        "inserted=%d cooldown_skip=%d snapshot=%s",
        payload.get("status"),
        int(payload.get("active_sites") or 0),
        int(counts.get("critical") or 0),
        int(counts.get("warning") or 0),
        int(ae.get("inserted") or 0),
        int(ae.get("skipped_cooldown") or 0),
        payload.get("snapshot_path"),
    )
    return {
        "status": payload.get("status"),
        "active_sites": int(payload.get("active_sites") or 0),
        "violations": int(counts.get("critical") or 0) + int(counts.get("warning") or 0),
        "critical": int(counts.get("critical") or 0),
        "warning": int(counts.get("warning") or 0),
        "inserted": int(ae.get("inserted") or 0),
    }


# ---------------------------------------------------------------------------
# Harvest 3 트랙 C — 수집 7일 트렌드 일별 적재 + markdown 보고
# beat 스케줄: 매일 09:30 KST (= 00:30 UTC). collection_health 가 *시점*(24h)을
# 본다면 이 task 는 *기간*(7일) 트렌드를 누적·분석한다. reports/collection_trend_
# YYYY-MM-DD.{json,md} 적재. backend /_internal/collection-trend-history?days=14
# 가 누적 json 을 소비.
# ---------------------------------------------------------------------------
@app.task(name="tasks.run_collection_trend", max_retries=0)
def run_collection_trend(days: int = 7) -> dict:
    """수집 7일 누적 트렌드 + 분류 + markdown 보고서 생성."""
    from insight.collection_trend import collect_payload, save_snapshot
    payload = asyncio.run(collect_payload(days=int(days)))
    paths = save_snapshot(payload)
    summary = payload.get("summary") or {}
    counts = summary.get("class_counts") or {}
    logger.info(
        "[collection_trend] days=%d sites=%d total_voc=%d "
        "healthy=%d moderate=%d low=%d dying=%d dead=%d "
        "volatile=%d (down=%d up=%d swing=%d) snapshot=%s",
        int(payload.get("days") or 0),
        int(payload.get("active_sites") or 0),
        int(summary.get("total_voc") or 0),
        int(counts.get("healthy") or 0),
        int(counts.get("moderate") or 0),
        int(counts.get("low") or 0),
        int(counts.get("dying") or 0),
        int(counts.get("dead") or 0),
        int(summary.get("volatile_count") or 0),
        int(summary.get("trend_down_count") or 0),
        int(summary.get("trend_up_count") or 0),
        int(summary.get("volatile_swing_count") or 0),
        str(paths.get("json")),
    )
    return {
        "status": "ok",
        "days": int(payload.get("days") or 0),
        "active_sites": int(payload.get("active_sites") or 0),
        "total_voc": int(summary.get("total_voc") or 0),
        "class_counts": counts,
        "volatile_count": int(summary.get("volatile_count") or 0),
        "json_path": str(paths.get("json") or ""),
        "md_path": str(paths.get("md") or ""),
    }


# ---------------------------------------------------------------------------
# R20 Track E — 백필 안전장치 실 운영 모니터링
# beat 스케줄: 매일 09:30 KST (= 00:30 UTC). reports/backfill_audit.jsonl 을 스캔
# 하여 PRESERVE_EXISTING=False 등 위험 백필을 자동 탐지·로그.  운영 정책 준수
# 여부 *자동 보증* 이며 R18 사고 재발 방지.
# ---------------------------------------------------------------------------
@app.task(name="tasks.run_backfill_audit_monitor", max_retries=0)
def run_backfill_audit_monitor() -> dict:
    """최근 7일 backfill_audit.jsonl 스캔 → 위험 백필 alert 로그."""
    from insight.backfill_audit_monitor import run as audit_monitor_run
    payload = audit_monitor_run(window_days=7)
    counts = payload.get("alert_counts") or {}
    critical = int(counts.get("critical") or 0)
    warning = int(counts.get("warning") or 0)
    if critical > 0:
        logger.warning(
            "[backfill-audit-monitor] 위험 백필 발견: critical=%d warning=%d total_runs=%d",
            critical, warning, payload.get("total_runs"),
        )
    else:
        logger.info(
            "[backfill-audit-monitor] OK runs=%d warnings=%d",
            payload.get("total_runs"), warning,
        )
    return {
        "status": "ok",
        "total_runs": payload.get("total_runs"),
        "critical": critical,
        "warning": warning,
        "alerts": len(payload.get("alerts") or []),
    }


# ---------------------------------------------------------------------------
# P1-3 — mv_voc_daily 자동 REFRESH
# beat 스케줄: 30분마다 (crontab(*/30 * * * *))
# CONCURRENTLY 옵션 → 읽기 차단 없음. 단 UNIQUE INDEX(mv_voc_daily_uniq) 전제.
# 첫 빌드 후 raw=114k → mv=2.3k, REFRESH CONCURRENTLY ≈ 80ms.
# ---------------------------------------------------------------------------
@app.task(name="tasks.refresh_mv_voc_daily", max_retries=2, default_retry_delay=60)
def refresh_mv_voc_daily() -> dict:
    """mv_voc_daily 머티리얼라이즈드 뷰를 CONCURRENTLY 재계산.

    psql 외부 호출(다른 export/health-check 태스크와 동일 패턴) — 비동기 엔진/세션을
    Celery 동기 컨텍스트에서 다시 띄우지 않으려는 의도.
    """
    import subprocess
    import time

    sql = "REFRESH MATERIALIZED VIEW CONCURRENTLY mv_voc_daily;"
    t0 = time.time()
    try:
        out = subprocess.run(
            ["psql", "-h", "127.0.0.1", "-p", "5434", "-U", "signalforge",
             "-d", "signalforge", "-v", "ON_ERROR_STOP=1", "-c", sql],
            env={**os.environ, "PGPASSWORD": os.getenv("PGPASSWORD", "signalforge_pass")},
            capture_output=True, text=True, timeout=300,
        )
    except subprocess.TimeoutExpired:
        logger.error("[refresh-mv-voc-daily] psql timeout > 300s")
        return {"status": "error", "message": "timeout"}

    elapsed_ms = int((time.time() - t0) * 1000)
    if out.returncode != 0:
        logger.error(f"[refresh-mv-voc-daily] 실패 rc={out.returncode} stderr={out.stderr.strip()}")
        return {"status": "error", "rc": out.returncode, "stderr": out.stderr.strip(), "elapsed_ms": elapsed_ms}

    logger.info(f"[refresh-mv-voc-daily] 완료 {elapsed_ms}ms")
    return {"status": "ok", "elapsed_ms": elapsed_ms}


@app.task(name="tasks.refresh_kpi_overview", max_retries=2, default_retry_delay=60)
def refresh_kpi_overview() -> dict:
    """kpi_overview MV CONCURRENTLY 재계산 (R16 트랙 C, 10분 주기)."""
    import subprocess, time
    t0 = time.time()
    try:
        out = subprocess.run(
            ["psql", "-h", "127.0.0.1", "-p", "5434", "-U", "signalforge",
             "-d", "signalforge", "-v", "ON_ERROR_STOP=1",
             "-c", "REFRESH MATERIALIZED VIEW CONCURRENTLY kpi_overview;"],
            env={**os.environ, "PGPASSWORD": os.getenv("PGPASSWORD", "signalforge_pass")},
            capture_output=True, text=True, timeout=120,
        )
    except subprocess.TimeoutExpired:
        return {"status": "error", "message": "timeout"}
    elapsed_ms = int((time.time() - t0) * 1000)
    if out.returncode != 0:
        return {"status": "error", "stderr": out.stderr.strip()}
    return {"status": "ok", "elapsed_ms": elapsed_ms}


@app.task(name="tasks.refresh_galaxy_master_timeline", max_retries=2, default_retry_delay=60)
def refresh_galaxy_master_timeline() -> dict:
    """galaxy_master_timeline MV 를 CONCURRENTLY 재계산 (R11 트랙 D, 1h 주기)."""
    import subprocess
    import time

    sql = "REFRESH MATERIALIZED VIEW CONCURRENTLY galaxy_master_timeline;"
    t0 = time.time()
    try:
        out = subprocess.run(
            ["psql", "-h", "127.0.0.1", "-p", "5434", "-U", "signalforge",
             "-d", "signalforge", "-v", "ON_ERROR_STOP=1", "-c", sql],
            env={**os.environ, "PGPASSWORD": os.getenv("PGPASSWORD", "signalforge_pass")},
            capture_output=True, text=True, timeout=300,
        )
    except subprocess.TimeoutExpired:
        return {"status": "error", "message": "timeout"}
    elapsed_ms = int((time.time() - t0) * 1000)
    if out.returncode != 0:
        return {"status": "error", "rc": out.returncode, "stderr": out.stderr.strip(), "elapsed_ms": elapsed_ms}
    logger.info(f"[refresh-galaxy-master-timeline] 완료 {elapsed_ms}ms")
    return {"status": "ok", "elapsed_ms": elapsed_ms}


# P2-2 신규: 키워드 ingest 주기 작업 (30분, 누적)
@app.task(bind=True, max_retries=2)
def run_ingest_keywords(self, batch: int = 1000, top_n: int = 20):
    """voc_records → voc_keywords 키워드 ingest (1회 batch)."""
    from keywords.ingest import ingest
    try:
        n = asyncio.run(ingest(batch=batch, top_n=top_n))
        logger.info(f"[ingest_keywords] {n} keyword rows inserted")
        return {"status": "done", "keyword_rows": n}
    except Exception as exc:
        logger.exception("[ingest_keywords] 실패: %s", exc)
        raise self.retry(exc=exc)


# P2-1 신규: category_daily / kg_edges_daily refresh
# 구현 노트 (2026-06-02): 기존 psycopg2 의존을 제거하고 refresh_mv_voc_daily /
# run_refresh_p3_mvs 와 동일한 psql subprocess 패턴으로 통일. 크롤러 venv 에
# psycopg2 가 없어도 동작하며, MV 별 실패 격리 + 소요시간(ms) 반환을 보장.
@app.task(bind=True, max_retries=2, name="tasks.run_refresh_p2_mvs")
def run_refresh_p2_mvs(self):
    """category_daily + kg_edges_daily MV refresh (CONCURRENTLY)."""
    import subprocess
    import time

    out: dict = {}
    env = {**os.environ, "PGPASSWORD": os.getenv("PGPASSWORD",
                                                os.getenv("POSTGRES_PASSWORD", "signalforge_pass"))}
    psql_base = [
        "psql", "-h", os.getenv("POSTGRES_HOST", "127.0.0.1"),
        "-p", os.getenv("POSTGRES_PORT", "5434"),
        "-U", os.getenv("POSTGRES_USER", "signalforge"),
        "-d", os.getenv("POSTGRES_DB", "signalforge"),
        "-v", "ON_ERROR_STOP=1",
    ]
    for mv in ("category_daily", "kg_edges_daily"):
        # category_daily 는 UNIQUE 인덱스가 표현식(COALESCE) 기반이라 CONCURRENTLY
        # 거부 → 비-CONCURRENTLY fallback (짧은 ACCESS EXCLUSIVE lock 감수).
        # kg_edges_daily 는 simple-column UNIQUE 라 CONCURRENTLY 가능.
        sql_primary = f"REFRESH MATERIALIZED VIEW CONCURRENTLY {mv};"
        sql_fallback = f"REFRESH MATERIALIZED VIEW {mv};"
        t0 = time.time()
        try:
            res = subprocess.run(
                psql_base + ["-c", sql_primary],
                env=env, capture_output=True, text=True, timeout=300,
            )
            mode = "concurrently"
            if res.returncode != 0 and "concurrently" in res.stderr.lower():
                # 표현식 UNIQUE 인덱스로 CONCURRENTLY 거부 → 일반 refresh 재시도.
                res = subprocess.run(
                    psql_base + ["-c", sql_fallback],
                    env=env, capture_output=True, text=True, timeout=300,
                )
                mode = "blocking"
        except subprocess.TimeoutExpired:
            out[mv] = {"status": "error", "error": "timeout",
                       "elapsed_ms": int((time.time() - t0) * 1000)}
            continue
        elapsed_ms = int((time.time() - t0) * 1000)
        if res.returncode != 0:
            out[mv] = {"status": "error", "rc": res.returncode,
                       "stderr": res.stderr.strip(), "elapsed_ms": elapsed_ms,
                       "mode": mode}
        else:
            out[mv] = {"status": "ok", "elapsed_ms": elapsed_ms, "mode": mode}
    logger.info(f"[refresh_p2_mvs] {out}")
    return {"status": "done", **out}


# ---------------------------------------------------------------------------
# Track E — 운영 품질 일일 보고 (Track E quality_report)
# beat 스케줄: 매일 00:30 UTC (= 09:30 KST)
# ---------------------------------------------------------------------------
@app.task(name="tasks.run_quality_report", max_retries=0)
def run_quality_report() -> dict:
    """캐시·grounding·MV·p95 지표 묶음 → reports/quality_YYYY-MM-DD.md."""
    from insight.quality_report import run as build_quality
    path = build_quality()
    logger.info(f"[quality-report] 생성 완료: {path}")
    return {"status": "ok", "path": str(path)}


# P4 트랙 A 신규: 알림 룰엔진 5분 주기 평가
# 백엔드의 /api/v1/alerts/test 를 호출하여 활성 룰을 라이브 metric 으로 평가하고
# 위반 시 alert_events INSERT + 채널 dispatch 까지 일괄 수행한다 (실제 운영 흐름).
# DB cooldown: alert_events.fired_at 으로 rule_id 의 마지막 발화를 조회하여
# cooldown_sec 이내라면 skip — RuleEngine.is_cooled() 와 동일 의미.
@app.task(name="tasks.evaluate_alert_rules", bind=True, max_retries=1, default_retry_delay=30)
def evaluate_alert_rules(self):
    """활성 룰 평가 + dispatch + cooldown 가드.

    Returns:
        {"status": "ok", "evaluated": N, "fired": M, "skipped_cooldown": K, "metrics": {...}}
    """
    import subprocess
    import json as _json
    import time

    env = {**os.environ, "PGPASSWORD": os.getenv("PGPASSWORD", "signalforge_pass")}
    psql_base = [
        "psql", "-h", os.getenv("POSTGRES_HOST", "127.0.0.1"),
        "-p", os.getenv("POSTGRES_PORT", "5434"),
        "-U", os.getenv("POSTGRES_USER", "signalforge"),
        "-d", os.getenv("POSTGRES_DB", "signalforge"),
        "-tA", "-v", "ON_ERROR_STOP=1",
    ]

    # 1) 활성 룰 + 마지막 fired_at 조회 (cooldown 가드용)
    sql_rules = """
        SELECT r.id, r.name, r.cooldown_sec,
               EXTRACT(EPOCH FROM (now() - COALESCE(MAX(e.fired_at), 'epoch'::timestamptz)))::int AS sec_since_last
        FROM alert_rules r
        LEFT JOIN alert_events e ON e.rule_id = r.id
        WHERE r.is_active = TRUE
        GROUP BY r.id
        ORDER BY r.id
    """
    try:
        res = subprocess.run(
            psql_base + ["-c", sql_rules],
            env=env, capture_output=True, text=True, timeout=30,
        )
    except subprocess.TimeoutExpired:
        return {"status": "error", "error": "psql_timeout"}
    if res.returncode != 0:
        return {"status": "error", "stderr": res.stderr.strip()}

    cooldown_ok_ids: list[int] = []
    skipped = 0
    for line in res.stdout.strip().splitlines():
        if not line:
            continue
        parts = line.split("|")
        if len(parts) < 4:
            continue
        rid = int(parts[0])
        cd = int(parts[2])
        sec_since = int(parts[3] or 0)
        if sec_since >= cd:
            cooldown_ok_ids.append(rid)
        else:
            skipped += 1

    # 2) /alerts/test 호출 — 백엔드 API 가 collect_metrics + RuleEngine + INSERT + dispatch 일괄 처리
    # ignore_cooldown=True 라 cooldown 가드는 위 1) 단계에서만 적용.
    # cooldown skip 룰이 있다면 evaluate 결과에서 그 rule_id 의 event 만 빼고 카운트.
    import urllib.request
    import urllib.error

    api_url = os.getenv("ALERTS_API_URL", "http://127.0.0.1:8000/api/v1/alerts/test?respect_cooldown=true")
    t0 = time.time()
    try:
        req = urllib.request.Request(api_url, method="POST",
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            payload = _json.loads(resp.read().decode())
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
        logger.exception("[evaluate_alert_rules] /alerts/test 호출 실패: %s", exc)
        return {"status": "error", "error": str(exc)}
    elapsed_ms = int((time.time() - t0) * 1000)

    # 3) cooldown 적용 — cooldown 위반된 룰의 event 는 DB INSERT 가 이미 일어났지만
    # 정책상 5분 주기에서는 새 알림으로 간주하지 않음 (대신 운영 정책 명시).
    # 단순화: payload['events'] 에서 cooldown_ok 인 룰만 카운트.
    events = payload.get("events", [])
    fired_after_cd = sum(1 for e in events if e.get("rule_id") in cooldown_ok_ids)

    # dispatched_channels 집계 — backend /alerts/test 가 채널 dispatch 결과를
    # 라벨(예: "slack:dry", "websocket") 로 정확히 채워준다.
    # 운영 모니터링에서 어떤 채널이 실 송신 / dry-run / 실패인지 즉시 확인 가능.
    dispatched_summary: dict = {}
    for e in events:
        for label in e.get("dispatched_channels", []) or []:
            dispatched_summary[label] = dispatched_summary.get(label, 0) + 1

    out = {
        "status": "ok",
        "evaluated": payload.get("evaluated", 0),
        "fired_total": payload.get("fired", 0),
        "fired_after_cooldown": fired_after_cd,
        "skipped_cooldown": skipped,
        "metrics": payload.get("metrics", {}),
        "dispatched": dispatched_summary,
        "elapsed_ms": elapsed_ms,
    }
    logger.info(
        "[evaluate_alert_rules] %s",
        {k: v for k, v in out.items() if k != "metrics"},
    )
    return out


# ---------------------------------------------------------------------------
# Track E — Drive 백업 검증 (verify_backup)
# beat 스케줄: 매일 20:00 UTC (= 05:00 KST 다음날) — backup-to-drive 사이클 직후.
#
# scripts/drive-sync/verify-backup.sh 를 subprocess 호출 →
#   exit 0 + ok=true : 정상 → last_verified.json 갱신만
#   exit 1 또는 ok=false : alert_rules.backup_fail (system.backup_ok < 1) 룰 fire,
#                          alert_events INSERT (value=0, payload={reason, file, mtime}).
# ---------------------------------------------------------------------------
@app.task(name="tasks.verify_backup", max_retries=0)
def verify_backup() -> dict:
    """Drive 백업 신선도+무결성 1회 검증 + 실패 시 alert_events INSERT."""
    import json as _json
    import subprocess
    import time as _time

    repo_root = os.path.abspath(os.path.join(_CRAWLER_DIR, ".."))
    script = os.path.join(repo_root, "scripts", "drive-sync", "verify-backup.sh")
    if not os.path.isfile(script):
        logger.error(f"[verify_backup] script not found: {script}")
        return {"status": "error", "error": "script_missing"}

    t0 = _time.time()
    try:
        res = subprocess.run(
            ["bash", script],
            capture_output=True, text=True, timeout=120,
        )
    except subprocess.TimeoutExpired:
        logger.error("[verify_backup] verify-backup.sh timeout > 120s")
        return {"status": "error", "error": "timeout"}
    elapsed_ms = int((_time.time() - t0) * 1000)

    # verify-backup.sh 는 마지막 stdout 라인에 JSON 한 줄 (tee).
    last_line = (res.stdout or "").strip().splitlines()[-1] if res.stdout else ""
    payload: dict = {}
    try:
        payload = _json.loads(last_line) if last_line else {}
    except Exception:
        payload = {"parse_error": True, "raw": last_line[:200]}

    ok = bool(payload.get("ok"))
    logger.info(
        f"[verify_backup] ok={ok} rc={res.returncode} elapsed_ms={elapsed_ms} "
        f"reason={payload.get('reason')}"
    )

    # 실패 시 alert_events INSERT (alert_rules.backup_fail).
    if not ok:
        env = {**os.environ, "PGPASSWORD": os.getenv("PGPASSWORD",
                                                    os.getenv("POSTGRES_PASSWORD", "signalforge_pass"))}
        psql_base = [
            "psql", "-h", os.getenv("POSTGRES_HOST", "127.0.0.1"),
            "-p", os.getenv("POSTGRES_PORT", "5434"),
            "-U", os.getenv("POSTGRES_USER", "signalforge"),
            "-d", os.getenv("POSTGRES_DB", "signalforge"),
            "-tA", "-v", "ON_ERROR_STOP=1",
        ]
        # cooldown 가드: backup_fail.cooldown_sec(3600) 이내 직전 발화 있으면 skip.
        # 일일 1회 호출이라 일반적으로 무관, 수동 재실행 대비.
        sql_cd = """
            SELECT EXTRACT(EPOCH FROM (now() - COALESCE(MAX(e.fired_at), 'epoch'::timestamptz)))::int,
                   r.cooldown_sec
            FROM alert_rules r
            LEFT JOIN alert_events e ON e.rule_id = r.id
            WHERE r.name = 'backup_fail' AND r.is_active = TRUE
            GROUP BY r.cooldown_sec
        """
        try:
            cd_res = subprocess.run(
                psql_base + ["-c", sql_cd],
                env=env, capture_output=True, text=True, timeout=10,
            )
            line = (cd_res.stdout or "").strip()
            if line:
                sec_since, cooldown = (int(x) for x in line.split("|"))
                if sec_since < cooldown:
                    logger.warning(
                        f"[verify_backup] cooldown active ({sec_since}s < {cooldown}s) — skip insert"
                    )
                    return {"status": "fail_cooldown",
                            "ok": False, "payload": payload, "elapsed_ms": elapsed_ms}
        except Exception as exc:
            logger.warning(f"[verify_backup] cooldown 조회 실패 (insert 진행): {exc}")

        # alert_events INSERT — value=0 → op '<' threshold=1 위반.
        ev_payload = {
            "type": "backup_fail",
            "reason": payload.get("reason"),
            "file": payload.get("file"),
            "mtime": payload.get("mtime"),
            "age_hours": payload.get("age_hours"),
            "drive_path": payload.get("drive_path"),
            "verified_at": payload.get("verified_at"),
        }
        sql_ins = """
            INSERT INTO alert_events
                (rule_id, severity, value, threshold, payload, dispatched_channels)
            SELECT id, severity, 0, threshold,
                   CAST($1 AS JSONB), ARRAY[]::varchar[]
            FROM alert_rules
            WHERE name = 'backup_fail' AND is_active = TRUE
            RETURNING id
        """
        # psql 의 -c $1 placeholder 는 비표준 — 안전하게 stdin heredoc 사용.
        try:
            ins_res = subprocess.run(
                psql_base + [
                    "-c",
                    "INSERT INTO alert_events (rule_id, severity, value, threshold, payload, dispatched_channels) "
                    "SELECT id, severity, 0, threshold, "
                    f"CAST('{_json.dumps(ev_payload).replace(chr(39), chr(39)*2)}' AS JSONB), "
                    "ARRAY[]::varchar[] "
                    "FROM alert_rules WHERE name='backup_fail' AND is_active=TRUE RETURNING id"
                ],
                env=env, capture_output=True, text=True, timeout=10,
            )
            if ins_res.returncode != 0:
                logger.error(f"[verify_backup] alert_events INSERT 실패: {ins_res.stderr.strip()}")
            else:
                logger.warning(
                    f"[verify_backup] alert_events 발화 — reason={payload.get('reason')}"
                )
        except Exception as exc:
            logger.exception(f"[verify_backup] alert_events INSERT 예외: {exc}")
        del sql_ins  # 미사용 (참조용 SQL 보존만)

    return {
        "status": "ok" if ok else "fail",
        "ok": ok,
        "payload": payload,
        "elapsed_ms": elapsed_ms,
        "rc": res.returncode,
    }


# P3-1 신규: platform_health / country_daily refresh
# 두 MV 모두 단순 컬럼 UNIQUE INDEX 보유 → CONCURRENTLY 가능 (READ 차단 없음).
# Celery beat 의 refresh-p3-mvs-30m 가 30 분마다 호출.
#
# 구현 노트: refresh_mv_voc_daily 와 동일하게 psql subprocess 패턴 — Celery 워커가
# psycopg2 없이 동작하도록 한다. 두 MV 를 1 회 psql 호출로 묶으면 어느 한쪽 실패
# 시 다른 쪽도 막히므로 분리 호출.
@app.task(bind=True, max_retries=2, name="tasks.run_refresh_p3_mvs")
def run_refresh_p3_mvs(self):
    """platform_health + country_daily MV refresh (CONCURRENTLY).

    개별 MV 실패가 다른 MV refresh 를 막지 않도록 격리.
    각 MV refresh 의 소요시간(ms) 도 함께 반환 → 운영 모니터링용.
    """
    import subprocess
    import time

    out: dict = {}
    env = {**os.environ, "PGPASSWORD": os.getenv("PGPASSWORD", "signalforge_pass")}
    psql_base = [
        "psql", "-h", os.getenv("POSTGRES_HOST", "127.0.0.1"),
        "-p", os.getenv("POSTGRES_PORT", "5434"),
        "-U", os.getenv("POSTGRES_USER", "signalforge"),
        "-d", os.getenv("POSTGRES_DB", "signalforge"),
        "-v", "ON_ERROR_STOP=1",
    ]
    for mv in ("platform_health", "country_daily"):
        sql = f"REFRESH MATERIALIZED VIEW CONCURRENTLY {mv};"
        t0 = time.time()
        try:
            res = subprocess.run(
                psql_base + ["-c", sql],
                env=env, capture_output=True, text=True, timeout=300,
            )
        except subprocess.TimeoutExpired:
            out[mv] = {"status": "error", "error": "timeout",
                       "elapsed_ms": int((time.time() - t0) * 1000)}
            continue
        elapsed_ms = int((time.time() - t0) * 1000)
        if res.returncode != 0:
            out[mv] = {"status": "error", "rc": res.returncode,
                       "stderr": res.stderr.strip(), "elapsed_ms": elapsed_ms}
        else:
            out[mv] = {"status": "ok", "elapsed_ms": elapsed_ms}

    logger.info(f"[refresh_p3_mvs] {out}")
    return {"status": "done", **out}


# ---------------------------------------------------------------------------
# R19 Track B — /dashboard/overview 캐시 워밍업 (Cold → Warm)
# beat 스케줄: 매 5분 (crontab(minute='*/5'))
#
# 목적
#   DashboardService.get_overview 는 @redis_cache(ttl=120s) 인데, 사용자 첫 접속이
#   캐시 MISS 시 SQL+직렬화로 50~60ms 정도 걸린다.  TTL 120s 보다 짧은 5분 주기로
#   인기 case 를 자동 호출해 항상 HIT(=1ms) 가 되도록 유지한다.
#
# 동작
#   urllib 로 /api/v1/dashboard/overview 를 GET — FastAPI 가 자기 Redis 캐시를
#   채운다.  Crawler 워커는 backend 와 별도 process 라 직접 DB 호출 대신 HTTP 가
#   가장 단순 / 격리 (operations_monitor 등 다른 task 도 같은 패턴).
#
# 워밍 대상 case (8건)
#   period × filter 매트릭스 — 운영 사용 패턴 분석 후 추가/조정 가능.
#     (1) period=24h, no filter            ← Dashboard 첫 화면
#     (2) period=7d,  no filter
#     (3) period=30d, no filter
#     (4) period=90d, no filter
#     (5) period=24h, platform=reddit
#     (6) period=30d, product=GS25
#     (7) period=30d, country=KR
#     (8) period=24h, country=US
# ---------------------------------------------------------------------------
@app.task(name="tasks.warm_dashboard_cache", max_retries=0)
def warm_dashboard_cache(base_url: Optional[str] = None) -> dict:
    """/dashboard/overview 자주 쓰는 8 case 를 미리 호출해 Redis 캐시를 채운다.

    Args:
        base_url: FastAPI base URL (기본 ``SIGNALFORGE_API`` env, 없으면 127.0.0.1:8000).

    Returns:
        {
          "status": "ok",
          "warmed": <int>,           # 200 OK 받은 case 수
          "failed": <int>,
          "elapsed_ms_total": <int>,
          "cases": [{"url": str, "ms": int, "rc": int}, ...]
        }
    """
    import json as _json
    import time
    import urllib.error
    import urllib.request

    base = (base_url or os.getenv("SIGNALFORGE_API") or "http://127.0.0.1:8000").rstrip("/")
    # 자주 쓰는 case 8건. 운영 분석 후 조정 가능.
    targets = [
        "/api/v1/dashboard/overview?period=24h",
        "/api/v1/dashboard/overview?period=7d",
        "/api/v1/dashboard/overview?period=30d",
        "/api/v1/dashboard/overview?period=90d",
        "/api/v1/dashboard/overview?period=24h&platform=reddit",
        "/api/v1/dashboard/overview?period=30d&product=GS25",
        "/api/v1/dashboard/overview?period=30d&country=KR",
        "/api/v1/dashboard/overview?period=24h&country=US",
    ]

    cases: list = []
    warmed = failed = 0
    t0_total = time.time()
    for path in targets:
        url = base + path
        t0 = time.time()
        rc = 0
        try:
            with urllib.request.urlopen(url, timeout=10.0) as resp:
                rc = int(resp.status)
                _ = resp.read()  # body 소비 → 캐시 SET 보장
        except urllib.error.HTTPError as e:
            rc = int(e.code)
        except Exception as e:  # noqa: BLE001
            rc = 0
            logger.warning("[warm-dashboard] url=%s err=%s", url, e)
        ms = int((time.time() - t0) * 1000)
        cases.append({"url": path, "ms": ms, "rc": rc})
        if rc == 200:
            warmed += 1
        else:
            failed += 1

    elapsed_ms_total = int((time.time() - t0_total) * 1000)
    logger.info(
        "[warm-dashboard] warmed=%d failed=%d total=%dms",
        warmed, failed, elapsed_ms_total,
    )
    return {
        "status": "ok" if failed == 0 else "partial",
        "warmed": warmed,
        "failed": failed,
        "elapsed_ms_total": elapsed_ms_total,
        "cases": cases,
    }


# ---------------------------------------------------------------------------
# R26 — Validator hook 폴링 (보고서 작성 직후 자동 cross-check)
# beat 스케줄: 매 5분 (crontab(*/5 * * * *))
# 본 후크는 reports/ + docs/dashboard/ 의 R*.md 를 mtime 기준 스캔하여
# 새 보고서가 발견되면 workflow_validator.parse_report 를 실행하고 drift > ±10%
# 를 reports/validator_hook_state.json 에 영구화한다.
# R25 archive 부재 회고 사고 모델을 직접 대응한다.
# ---------------------------------------------------------------------------
@app.task(name="tasks.run_validator_hook", max_retries=0)
def run_validator_hook() -> dict:
    """validator 후크 1 회 스캔. drift > ±10% 시 로그 WARNING."""
    from insight.workflow_validator_hook import run as hook_run
    out = hook_run()
    alerts = int(out.get("alerts_total") or 0)
    archive_drift = int(out.get("archive_drift_total") or 0)
    scanned = int(out.get("scanned_count") or 0)
    if alerts > 0 or archive_drift > 0:
        logger.warning(
            "[validator-hook] drift 감지: scanned=%d alerts=%d archive_drift=%d",
            scanned, alerts, archive_drift,
        )
    else:
        logger.info(
            "[validator-hook] OK scanned=%d", scanned,
        )
    return {
        "status": "ok",
        "scanned": scanned,
        "alerts": alerts,
        "archive_drift": archive_drift,
    }


# ---------------------------------------------------------------------------
# Stage 4.5 Y1 — 송신 측 양방향 자동 동기화 (auto-sync-to-drive)
# beat 스케줄: 매 30분 (crontab(minute='*/30')).
#
# 책임:
#   1) flock 로 중복 실행 차단 (수동 scripts/sync-to-drive.sh 와 경합 방지)
#   2) scripts/sync-to-drive.sh 호출 — DB dump 생성+업로드 / .env.example /
#      SIF (옵션: AUTO_SYNC_WITH_SIF=1 일 때만, 기본 0 — 매 30분 SIF 업로드는
#      대역폭 낭비.  SIF 는 별도 daily 트랙에서 처리).
#   3) backups/ 에서 새로 생성된 dump 메타 (sha256, size, mtime) 수집
#   4) voc_count 조회 (psql -tA, 실패 시 None)
#   5) LATEST.json 생성 → 로컬 + Drive 루트 업로드 (수신 측이 delta 판단)
#   6) audit JSONL (round=auto_sync) start/end/fail 라인 적재
#
# 환경 변수:
#   AUTO_SYNC_DRY_RUN=true   → bash sync-to-drive.sh --dry-run (rclone 시뮬레이션)
#   AUTO_SYNC_WITH_SIF=1     → SIF 도 포함 (default off)
#   AUTO_SYNC_SKIP_LOCK=1    → 잠금 우회 (테스트용)
# ---------------------------------------------------------------------------
@app.task(name="tasks.run_auto_sync_to_drive", bind=True, max_retries=3, default_retry_delay=300)
def run_auto_sync_to_drive(self) -> dict:
    """30분 주기 백업 + Drive 푸시 + LATEST.json 갱신."""
    import json as _json
    import subprocess
    import time as _time
    from datetime import datetime, timezone

    from insight.auto_sync import (
        acquire_lock,
        build_latest_payload,
        dump_meta_to_dict,
        newest_dump_meta,
        write_latest_json,
    )

    repo_root = os.path.abspath(os.path.join(_CRAWLER_DIR, ".."))
    script = os.path.join(repo_root, "scripts", "sync-to-drive.sh")
    if not os.path.isfile(script):
        logger.error(f"[auto_sync] script not found: {script}")
        return {"status": "error", "error": "script_missing", "path": script}

    dump_dir = os.path.join(repo_root, "backups")
    audit_dir = os.path.join(repo_root, "logs", "audit")
    os.makedirs(audit_dir, exist_ok=True)
    audit_file = os.path.join(audit_dir, "auto_sync.jsonl")

    latest_local = os.path.join(repo_root, "logs", "sync", "LATEST.json")
    # _lock_helper.sh 와 동일 경로 — sync-to-drive.sh 가 같은 파일을 flock 하므로
    # 두 락이 동일 inode 면 사실상 한 단계.  /var/lock 쓰기 불가 시 폴백.
    _lock_dir = "/var/lock" if os.access("/var/lock", os.W_OK) else os.path.join(
        os.environ.get("XDG_RUNTIME_DIR", "/tmp"), "sf-lock"
    )
    lock_path = os.path.join(_lock_dir, "sf_sync_to.lock")

    dry_run = (os.getenv("AUTO_SYNC_DRY_RUN", "").lower() in {"1", "true", "yes"})
    with_sif = (os.getenv("AUTO_SYNC_WITH_SIF", "0") in {"1", "true", "yes"})
    skip_lock = (os.getenv("AUTO_SYNC_SKIP_LOCK", "0") in {"1", "true", "yes"})

    run_id = f"{int(_time.time())}-{os.getpid()}"
    ts_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def _audit(event: str, extra: Optional[dict] = None) -> None:
        rec = {
            "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "round": "auto_sync",
            "track": "Y1",
            "run_id": run_id,
            "dry_run": dry_run,
            "event": event,
        }
        if extra:
            rec.update(extra)
        try:
            with open(audit_file, "a", encoding="utf-8") as f:
                f.write(_json.dumps(rec, ensure_ascii=False) + "\n")
        except Exception as exc:
            logger.warning(f"[auto_sync] audit write fail: {exc}")

    _audit("start", {"with_sif": with_sif})

    # 1) 잠금
    lock_cm = None
    if not skip_lock:
        try:
            lock_cm = acquire_lock(lock_path)
            lock_cm.__enter__()
        except RuntimeError as exc:
            logger.warning(f"[auto_sync] lock busy — skip cycle ({exc})")
            _audit("skip_locked", {"reason": str(exc)})
            return {"status": "skip", "reason": "lock_busy"}

    try:
        # 2) sync-to-drive.sh 호출
        args = ["bash", script]
        if dry_run:
            args.append("--dry-run")
        if not with_sif:
            args.append("--no-sif")  # 30분 주기에는 SIF 제외

        t0 = _time.time()
        # 출력을 파일로 리다이렉트 (capture_output 파이프 사용 시 sync-to-drive.sh 내부
        # `sleep 300 &` 백그라운드 워치독이 stdout/stderr fd 를 상속받아 Python
        # subprocess.run 이 5분 동안 블록되는 문제 회피).
        sync_log_dir = os.path.join(repo_root, "logs", "sync")
        os.makedirs(sync_log_dir, exist_ok=True)
        sync_log = os.path.join(sync_log_dir, f"sync-to-drive-{run_id}.log")
        try:
            with open(sync_log, "wb") as logf:
                res = subprocess.run(
                    args,
                    stdout=logf, stderr=logf, stdin=subprocess.DEVNULL,
                    timeout=600,  # DB dump+upload 여유
                    cwd=repo_root,
                    start_new_session=True,
                )
            # 진단용 마지막 500B 만 메모리로 (실패 시 tail 로 사용)
            try:
                with open(sync_log, "rb") as lf:
                    lf.seek(0, 2)
                    sz = lf.tell()
                    lf.seek(max(0, sz - 500))
                    _tail = lf.read().decode("utf-8", errors="replace")
            except Exception:
                _tail = ""
        except subprocess.TimeoutExpired:
            logger.error("[auto_sync] sync-to-drive.sh timeout > 600s")
            _audit("fail", {"reason": "timeout", "elapsed_ms": int((_time.time() - t0) * 1000)})
            try:
                raise self.retry(exc=Exception("sync_timeout"))
            except self.MaxRetriesExceededError:
                return {"status": "error", "error": "timeout_max_retry"}

        elapsed_ms = int((_time.time() - t0) * 1000)
        if res.returncode != 0:
            logger.error(
                f"[auto_sync] sync-to-drive.sh rc={res.returncode} tail={_tail!r}"
            )
            _audit("fail", {"reason": "script_nonzero", "rc": res.returncode,
                            "elapsed_ms": elapsed_ms, "tail": _tail[-200:],
                            "log_path": sync_log})
            try:
                raise self.retry(exc=Exception(f"script_rc={res.returncode}"))
            except self.MaxRetriesExceededError:
                return {"status": "error", "error": "script_rc_max_retry",
                        "rc": res.returncode, "log_path": sync_log}

        # 3) dump 메타 수집
        dump = newest_dump_meta(dump_dir, prefix="sf-db-")

        # 4) voc_count (best-effort — 실패 시 None)
        voc_count: Optional[int] = None
        try:
            env = {**os.environ,
                   "PGPASSWORD": os.getenv("PGPASSWORD",
                                            os.getenv("POSTGRES_PASSWORD", "signalforge_pass"))}
            psql_args = [
                "psql", "-h", os.getenv("POSTGRES_HOST", "127.0.0.1"),
                "-p", os.getenv("POSTGRES_PORT", "5434"),
                "-U", os.getenv("POSTGRES_USER", "signalforge"),
                "-d", os.getenv("POSTGRES_DB", "signalforge"),
                "-tA", "-v", "ON_ERROR_STOP=1",
                "-c", "SELECT COUNT(*) FROM voc_records",
            ]
            r2 = subprocess.run(psql_args, env=env, capture_output=True,
                                text=True, timeout=15)
            if r2.returncode == 0:
                voc_count = int((r2.stdout or "0").strip() or "0")
        except Exception as exc:
            logger.info(f"[auto_sync] voc_count 조회 실패 (무시): {exc}")

        # 5) LATEST.json
        payload = build_latest_payload(
            dump=dump, voc_count=voc_count, ts_iso=ts_iso,
            run_id=run_id, dry_run=dry_run,
        )
        try:
            write_latest_json(latest_local, payload)
        except Exception as exc:
            logger.error(f"[auto_sync] LATEST.json write 실패: {exc}")
            _audit("fail", {"reason": "latest_write", "error": str(exc)})
            return {"status": "error", "error": "latest_write"}

        # 5-b) Drive 루트 업로드 (dry-run 시 생략)
        latest_uploaded = False
        if not dry_run:
            remote = os.getenv("SF_DRIVE_REMOTE", "ApptainerImages")
            proj = os.getenv("SF_DRIVE_PROJECT", "SignalForge")
            try:
                rc = subprocess.run(
                    ["rclone", "copy", latest_local, f"{remote}:{proj}/"],
                    capture_output=True, text=True, timeout=60,
                )
                latest_uploaded = (rc.returncode == 0)
                if not latest_uploaded:
                    logger.warning(
                        f"[auto_sync] LATEST.json upload rc={rc.returncode} "
                        f"err={(rc.stderr or '')[-200:]!r}"
                    )
            except Exception as exc:
                logger.warning(f"[auto_sync] LATEST.json upload 예외 (무시): {exc}")

        _audit("end", {
            "elapsed_ms": elapsed_ms,
            "voc_count": voc_count,
            "dump": dump_meta_to_dict(dump),
            "latest_path": latest_local,
            "latest_uploaded": latest_uploaded,
        })

        logger.info(
            f"[auto_sync] OK dry={dry_run} elapsed_ms={elapsed_ms} "
            f"voc_count={voc_count} dump={dump.name if dump else None} "
            f"latest_uploaded={latest_uploaded}"
        )
        return {
            "status": "ok",
            "dry_run": dry_run,
            "elapsed_ms": elapsed_ms,
            "voc_count": voc_count,
            "dump": dump_meta_to_dict(dump),
            "latest_path": latest_local,
            "latest_uploaded": latest_uploaded,
        }
    finally:
        if lock_cm is not None:
            try:
                lock_cm.__exit__(None, None, None)
            except Exception:
                pass
