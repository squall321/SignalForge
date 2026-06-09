"""
마스터 데이터 시딩 스크립트
실행: python -m app.seeds.seed_master
"""
import asyncio
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.database import AsyncSessionLocal
from app.models import Product, Platform, VocCategory


# ── 제품 마스터 데이터 ──────────────────────────────────────
# @lat: PRODUCTS — [[products]] 참조.
PRODUCTS = [
    # Galaxy S 시리즈
    {"code": "GS25",   "series_code": "GS", "name_en": "Galaxy S25",       "name_ko": "갤럭시 S25"},
    {"code": "GS25P",  "series_code": "GS", "name_en": "Galaxy S25+",      "name_ko": "갤럭시 S25+"},
    {"code": "GS25U",  "series_code": "GS", "name_en": "Galaxy S25 Ultra", "name_ko": "갤럭시 S25 울트라"},
    # Galaxy Z 시리즈
    {"code": "GZF7",   "series_code": "GZ", "name_en": "Galaxy Z Fold7",   "name_ko": "갤럭시 Z 폴드7"},
    {"code": "GZFL7",  "series_code": "GZ", "name_en": "Galaxy Z Flip7",   "name_ko": "갤럭시 Z 플립7"},
    # Galaxy A/FE 시리즈
    {"code": "GA56",   "series_code": "GA", "name_en": "Galaxy A56",       "name_ko": "갤럭시 A56"},
    {"code": "GFE25",  "series_code": "GA", "name_en": "Galaxy FE25",      "name_ko": "갤럭시 FE25"},
    # Galaxy Watch
    {"code": "GW8",    "series_code": "GW", "name_en": "Galaxy Watch8",    "name_ko": "갤럭시 워치8"},
    {"code": "GWU",    "series_code": "GW", "name_en": "Galaxy Watch Ultra","name_ko": "갤럭시 워치 울트라"},
    # Galaxy Buds
    {"code": "GB3",    "series_code": "GB", "name_en": "Galaxy Buds3",     "name_ko": "갤럭시 버즈3"},
    {"code": "GB3P",   "series_code": "GB", "name_en": "Galaxy Buds3 Pro", "name_ko": "갤럭시 버즈3 프로"},
    # Galaxy Ring
    {"code": "GR2",    "series_code": "GR", "name_en": "Galaxy Ring2",     "name_ko": "갤럭시 링2"},

    # ── Galaxy 구세대 (시기별 비교용) ──
    {"code": "GS24",   "series_code": "GS", "name_en": "Galaxy S24",       "name_ko": "갤럭시 S24"},
    {"code": "GS24P",  "series_code": "GS", "name_en": "Galaxy S24+",      "name_ko": "갤럭시 S24+"},
    {"code": "GS24U",  "series_code": "GS", "name_en": "Galaxy S24 Ultra", "name_ko": "갤럭시 S24 울트라"},
    {"code": "GFE24",  "series_code": "GA", "name_en": "Galaxy S24 FE",    "name_ko": "갤럭시 S24 FE"},
    {"code": "GS23",   "series_code": "GS", "name_en": "Galaxy S23",       "name_ko": "갤럭시 S23"},
    {"code": "GS23P",  "series_code": "GS", "name_en": "Galaxy S23+",      "name_ko": "갤럭시 S23+"},
    {"code": "GS23U",  "series_code": "GS", "name_en": "Galaxy S23 Ultra", "name_ko": "갤럭시 S23 울트라"},
    {"code": "GFE23",  "series_code": "GA", "name_en": "Galaxy S23 FE",    "name_ko": "갤럭시 S23 FE"},
    {"code": "GS22",   "series_code": "GS", "name_en": "Galaxy S22",       "name_ko": "갤럭시 S22"},
    {"code": "GS22U",  "series_code": "GS", "name_en": "Galaxy S22 Ultra", "name_ko": "갤럭시 S22 울트라"},
    {"code": "GZF6",   "series_code": "GZ", "name_en": "Galaxy Z Fold6",   "name_ko": "갤럭시 Z 폴드6"},
    {"code": "GZFL6",  "series_code": "GZ", "name_en": "Galaxy Z Flip6",   "name_ko": "갤럭시 Z 플립6"},
    {"code": "GZF5",   "series_code": "GZ", "name_en": "Galaxy Z Fold5",   "name_ko": "갤럭시 Z 폴드5"},
    {"code": "GZFL5",  "series_code": "GZ", "name_en": "Galaxy Z Flip5",   "name_ko": "갤럭시 Z 플립5"},
    {"code": "GW7",    "series_code": "GW", "name_en": "Galaxy Watch7",    "name_ko": "갤럭시 워치7"},
    {"code": "GW6",    "series_code": "GW", "name_en": "Galaxy Watch6",    "name_ko": "갤럭시 워치6"},
    {"code": "GB2",    "series_code": "GB", "name_en": "Galaxy Buds2",     "name_ko": "갤럭시 버즈2"},
    {"code": "GB2P",   "series_code": "GB", "name_en": "Galaxy Buds2 Pro", "name_ko": "갤럭시 버즈2 프로"},

    # ── 경쟁사 (시장 비교용) ──
    {"code": "AP14",   "series_code": "AP", "name_en": "iPhone 14",            "name_ko": "아이폰 14"},
    {"code": "AP15",   "series_code": "AP", "name_en": "iPhone 15",            "name_ko": "아이폰 15"},
    {"code": "AP15P",  "series_code": "AP", "name_en": "iPhone 15 Pro",        "name_ko": "아이폰 15 Pro"},
    {"code": "AP15PM", "series_code": "AP", "name_en": "iPhone 15 Pro Max",    "name_ko": "아이폰 15 Pro Max"},
    {"code": "AP16",   "series_code": "AP", "name_en": "iPhone 16",            "name_ko": "아이폰 16"},
    {"code": "AP16P",  "series_code": "AP", "name_en": "iPhone 16 Pro",        "name_ko": "아이폰 16 Pro"},
    {"code": "AP16PM", "series_code": "AP", "name_en": "iPhone 16 Pro Max",    "name_ko": "아이폰 16 Pro Max"},
    {"code": "PX8",    "series_code": "PX", "name_en": "Pixel 8",              "name_ko": "픽셀 8"},
    {"code": "PX8P",   "series_code": "PX", "name_en": "Pixel 8 Pro",          "name_ko": "픽셀 8 Pro"},
    {"code": "PX9",    "series_code": "PX", "name_en": "Pixel 9",              "name_ko": "픽셀 9"},
    {"code": "PX9P",   "series_code": "PX", "name_en": "Pixel 9 Pro",          "name_ko": "픽셀 9 Pro"},
]

# ── 플랫폼 마스터 데이터 ──────────────────────────────────────

PLATFORMS = [
    {"code": "reddit",     "name": "Reddit",            "region": "GLOBAL", "base_url": "https://reddit.com"},
    {"code": "twitter",    "name": "Twitter / X",       "region": "GLOBAL", "base_url": "https://x.com"},
    {"code": "amazon_us",  "name": "Amazon US",         "region": "US",     "base_url": "https://amazon.com"},
    {"code": "amazon_de",  "name": "Amazon DE",         "region": "DE",     "base_url": "https://amazon.de"},
    {"code": "amazon_jp",  "name": "Amazon JP",         "region": "JP",     "base_url": "https://amazon.co.jp"},
    {"code": "amazon_kr",  "name": "Amazon KR",         "region": "KR",     "base_url": "https://amazon.co.kr"},
    {"code": "bestbuy",    "name": "Best Buy",          "region": "US",     "base_url": "https://bestbuy.com"},
    {"code": "clien",      "name": "Clien",             "region": "KR",     "base_url": "https://clien.net"},
    {"code": "ppomppu",    "name": "ppomppu",           "region": "KR",     "base_url": "https://ppomppu.co.kr"},
    {"code": "dcinside",   "name": "DCInside",          "region": "KR",     "base_url": "https://gall.dcinside.com"},
    {"code": "naver_cafe", "name": "Naver Cafe",        "region": "KR",     "base_url": "https://cafe.naver.com"},
    {"code": "xda",        "name": "XDA Developers",   "region": "GLOBAL", "base_url": "https://xda-developers.com"},
    {"code": "9to5google", "name": "9to5Google",       "region": "GLOBAL", "base_url": "https://9to5google.com"},
    {"code": "gsmarena",   "name": "GSMArena",         "region": "GLOBAL", "base_url": "https://www.gsmarena.com"},
    {"code": "xataka_mx",  "name": "Xataka México",    "region": "MX",     "base_url": "https://www.xataka.com.mx"},
]

# ── VOC 카테고리 마스터 데이터 ────────────────────────────────
# @lat: VOC_CATEGORIES — [[categories]] 참조.
VOC_CATEGORIES = [
    {
        "code": "battery",
        "name_en": "Battery & Charging",
        "name_ko": "배터리/충전",
        "keywords": ["battery life", "drain", "charging", "fast charge", "배터리", "충전", "방전"],
    },
    {
        "code": "camera",
        "name_en": "Camera & Photography",
        "name_ko": "카메라/촬영",
        "keywords": ["camera", "photo", "zoom", "night mode", "video", "카메라", "사진", "줌"],
    },
    {
        "code": "display",
        "name_en": "Display & Screen",
        "name_ko": "디스플레이",
        "keywords": ["screen", "display", "brightness", "AMOLED", "refresh rate", "화면", "밝기"],
    },
    {
        "code": "performance",
        "name_en": "Performance & Thermal",
        "name_ko": "성능/발열",
        "keywords": ["lag", "slow", "heating", "fps", "thermal", "snapdragon", "발열", "버벅", "성능"],
    },
    {
        "code": "software",
        "name_en": "Software & UI",
        "name_ko": "소프트웨어/UI",
        "keywords": ["OneUI", "update", "bug", "crash", "software", "UI", "업데이트", "버그", "앱"],
    },
    {
        "code": "build_quality",
        "name_en": "Build Quality & Durability",
        "name_ko": "내구성/품질",
        "keywords": ["crack", "scratch", "build", "hinge", "durability", "힌지", "파손", "내구성"],
    },
    {
        "code": "price",
        "name_en": "Price & Value",
        "name_ko": "가격/가성비",
        "keywords": ["price", "expensive", "value", "worth", "cost", "가격", "비싸", "가성비"],
    },
    {
        "code": "design",
        "name_en": "Design & Form Factor",
        "name_ko": "디자인/형태",
        "keywords": ["design", "color", "form factor", "thin", "weight", "디자인", "색상", "무게"],
    },
    {
        "code": "connectivity",
        "name_en": "Connectivity",
        "name_ko": "연결성",
        "keywords": ["wifi", "bluetooth", "5G", "signal", "NFC", "연결", "신호", "와이파이"],
    },
    {
        "code": "ai_features",
        "name_en": "AI Features",
        "name_ko": "AI 기능",
        "keywords": ["AI", "Galaxy AI", "Circle to Search", "Live Translate", "갤럭시 AI", "서클투서치"],
    },
    {
        "code": "accessories",
        "name_en": "Accessories & Compatibility",
        "name_ko": "액세서리/호환",
        "keywords": ["case", "cover", "S Pen", "accessories", "charger", "케이스", "충전기", "S펜"],
    },
    {
        "code": "comparison",
        "name_en": "Competitor Comparison",
        "name_ko": "경쟁사 비교",
        "keywords": ["Apple", "iPhone", "Pixel", "vs", "compare", "better", "아이폰", "픽셀", "비교"],
    },
]


async def seed(db: AsyncSession):
    print("🌱 마스터 데이터 시딩 시작...")

    # Products
    for p in PRODUCTS:
        exists = (await db.execute(
            select(Product).where(Product.code == p["code"])
        )).scalar_one_or_none()
        if not exists:
            db.add(Product(**p))
    await db.commit()
    print(f"  ✅ Products: {len(PRODUCTS)}개 처리")

    # Platforms
    for p in PLATFORMS:
        exists = (await db.execute(
            select(Platform).where(Platform.code == p["code"])
        )).scalar_one_or_none()
        if not exists:
            db.add(Platform(**p))
    await db.commit()
    print(f"  ✅ Platforms: {len(PLATFORMS)}개 처리")

    # VOC Categories
    for c in VOC_CATEGORIES:
        exists = (await db.execute(
            select(VocCategory).where(VocCategory.code == c["code"])
        )).scalar_one_or_none()
        if not exists:
            db.add(VocCategory(**c))
    await db.commit()
    print(f"  ✅ VOC Categories: {len(VOC_CATEGORIES)}개 처리")

    print("🎉 시딩 완료!")


async def main():
    async with AsyncSessionLocal() as db:
        await seed(db)


if __name__ == "__main__":
    asyncio.run(main())
