"""voc_records.product_id 재매칭 — 옛/신규 디바이스 마스터로 product_id 채움.

Track A R7 (2026-06-04) — 0010 마이그레이션으로 추가된 Tab/Watch9/A07~57/M/F/
XCover/Wide/Jump/Note Pro 모델까지 매칭 사전 대폭 확장.

대상: voc_records WHERE product_id IS NULL AND content_original IS NOT NULL
입력: content_translated COALESCE content_original (lower-casing + 공백 정규화)

매칭 로직:
  1. NOISE_PATTERNS — Xiaomi/Infinix Note 등 노이즈가 매칭되면 즉시 skip.
  2. MODEL_REGEX_PATTERNS — 컨텍스트 의존 패턴 (Tab, SM-코드, Wide/Jump 한국어 한정 등).
     선언 순서대로 매칭 (more-specific first).
  3. MODEL_MAP — 단순 substring 사전. 키 길이 내림차순 정렬 적용.

환경변수:
  DATABASE_URL          (필수, postgresql+asyncpg://… )
  RELINK_LIMIT          총 처리 상한 (기본 200000, 0=무제한)
  RELINK_BATCH          배치 크기 (기본 5000)
  RELINK_DRY_RUN        '1' 이면 UPDATE 안 함 (기본 '0')

실행:
  DATABASE_URL=postgresql+asyncpg://... \\
    /home/koopark/claude/SignalForge/.venv/bin/python \\
    -m scripts.relink_products
"""
import asyncio
import logging
import os
import re
import sys
from collections import Counter
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("relink_products")

DATABASE_URL = os.getenv("DATABASE_URL", "")
LIMIT = int(os.getenv("RELINK_LIMIT", "200000"))
BATCH = int(os.getenv("RELINK_BATCH", "5000"))
DRY_RUN = os.getenv("RELINK_DRY_RUN", "0") == "1"


# ═══════════════════════════════════════════════════════════════════════
# 1) NOISE PATTERNS — 갤럭시 컨텍스트 없으면 즉시 skip
# ═══════════════════════════════════════════════════════════════════════
_GALAXY_CTX_RE = re.compile(r"galaxy|samsung|갤럭시|삼성|갤(럭시)?", re.IGNORECASE)

NOISE_PATTERNS = [
    # Xiaomi/Infinix Note 12/13/60 — 갤럭시 컨텍스트 없으면 차단
    (re.compile(r"\b(xiaomi|redmi|infinix|tecno|realme)\s+note\b", re.IGNORECASE), "non_galaxy_note"),
    # S27 미래 추측 — 카탈로그 없음
    (re.compile(r"\bs\s*27\b", re.IGNORECASE), "s27_future"),
    # YC batch 는 MASK_PATTERNS 로 옮김 — 부분 마스킹만으로 충분 (Galaxy 잔존 키워드 보존).
]


# ═══════════════════════════════════════════════════════════════════════
# 1b) MASK PATTERNS — 매칭 전에 길이 보존 마스킹 (' ' 로 치환).
# ═══════════════════════════════════════════════════════════════════════
# 'Cactus (YC S25) – Galaxy S21' 같이 YC 배치는 토큰만 가리고 본 모델 키워드는
# 그대로 살린다. NOISE_PATTERNS 의 *전역 차단* 과 다르게, 부분 마스킹이라
# 정상 매칭은 보존된다.
MASK_PATTERNS: list[re.Pattern] = [
    # Y Combinator batch — 'YC S20' / 'YC W11' / 'YC F25' / 'YC X20' 패턴
    re.compile(r"\byc\s+[swfx]\s*\d{2}\b", re.IGNORECASE),
    # notebook / notebooks — laptop 의미, Galaxy Note 와 충돌 방지
    re.compile(r"\bnotebooks?\b", re.IGNORECASE),
    # app store note / play store note — 일반 구문 (대명사로서 note)
    re.compile(r"\b(app|play)\s+store\s+note\b", re.IGNORECASE),
]


def _mask_noise(s: str) -> str:
    """노이즈 토큰을 공백(' ')으로 치환해 길이 보존하며 마스킹.

    NOISE_PATTERNS 의 전역 차단과 달리, MASK_PATTERNS 는 *문자열의 일부분*만
    가려 나머지 본문에서 Galaxy 키워드 정상 매칭이 가능하도록 한다.

    예) "Cactus (YC S25) – Galaxy S21" → "Cactus (        ) – Galaxy S21"
    """
    out = s
    for pat in MASK_PATTERNS:
        out = pat.sub(lambda m: " " * (m.end() - m.start()), out)
    return out


# ═══════════════════════════════════════════════════════════════════════
# 2) MODEL REGEX PATTERNS — 컨텍스트 의존 매칭 (선언 순서대로, more specific first)
# ═══════════════════════════════════════════════════════════════════════
MODEL_REGEX_PATTERNS: list[tuple[re.Pattern, Optional[str]]] = [
    # ═══════════════════════════════════════════════════════════════════
    # R8 트랙 C: Samsung SM-XXX SKU 사전 (가장 명시적, 최우선 매칭)
    # 정규식 공통 형식: \bsm-?[xxx][a-z]?\b — 하이픈/리전 접미사 모두 허용.
    # ═══════════════════════════════════════════════════════════════════
    # ── Galaxy S 시리즈 SKU ───────────────────────────────────────
    (re.compile(r"\bsm-?s938[a-z]{0,2}\b", re.IGNORECASE), "GS25U"),
    (re.compile(r"\bsm-?s936[a-z]{0,2}\b", re.IGNORECASE), "GS25P"),
    (re.compile(r"\bsm-?s931[a-z]{0,2}\b", re.IGNORECASE), "GS25"),
    (re.compile(r"\bsm-?s928[a-z]{0,2}\b", re.IGNORECASE), "GS24U"),
    (re.compile(r"\bsm-?s926[a-z]{0,2}\b", re.IGNORECASE), "GS24P"),
    (re.compile(r"\bsm-?s921[a-z]{0,2}\b", re.IGNORECASE), "GS24"),
    (re.compile(r"\bsm-?s918[a-z]{0,2}\b", re.IGNORECASE), "GS23U"),
    (re.compile(r"\bsm-?s916[a-z]{0,2}\b", re.IGNORECASE), "GS23P"),
    (re.compile(r"\bsm-?s911[a-z]{0,2}\b", re.IGNORECASE), "GS23"),
    (re.compile(r"\bsm-?s908[a-z]{0,2}\b", re.IGNORECASE), "GS22U"),
    (re.compile(r"\bsm-?s906[a-z]{0,2}\b", re.IGNORECASE), "GS22"),    # S22+ 옛 카탈로그 없음 → GS22 폴백
    (re.compile(r"\bsm-?s901[a-z]{0,2}\b", re.IGNORECASE), "GS22"),
    (re.compile(r"\bsm-?s741[a-z]{0,2}\b", re.IGNORECASE), "GFE24"),   # S24 FE
    (re.compile(r"\bsm-?s721[a-z]{0,2}\b", re.IGNORECASE), "GFE23"),   # S23 FE
    (re.compile(r"\bsm-?s711[a-z]{0,2}\b", re.IGNORECASE), "GFE23"),   # S23 FE alt
    # ── S20 FE / S21 FE SKU (R8 추가) ─────────────────────────────
    (re.compile(r"\bsm-?g781[a-z]{0,2}\b", re.IGNORECASE), "GFE20"),  # S20 FE 5G
    (re.compile(r"\bsm-?g780[a-z]{0,2}\b", re.IGNORECASE), "GFE20"),  # S20 FE 4G
    (re.compile(r"\bsm-?g990[a-z]{0,2}\b", re.IGNORECASE), "GFE21"),  # S21 FE 5G
    # ── 옛 Galaxy S (S10~S21) SKU ────────────────────────────────
    (re.compile(r"\bsm-?g998[a-z]{0,2}\b", re.IGNORECASE), "GS21U"),
    (re.compile(r"\bsm-?g996[a-z]{0,2}\b", re.IGNORECASE), "GS21P"),
    (re.compile(r"\bsm-?g991[a-z]{0,2}\b", re.IGNORECASE), "GS21"),
    (re.compile(r"\bsm-?g988[a-z]{0,2}\b", re.IGNORECASE), "GS20U"),
    (re.compile(r"\bsm-?g985[a-z]{0,2}\b", re.IGNORECASE), "GS20P"),
    (re.compile(r"\bsm-?g981[a-z]{0,2}\b", re.IGNORECASE), "GS20"),
    (re.compile(r"\bsm-?g980[a-z]{0,2}\b", re.IGNORECASE), "GS20"),
    (re.compile(r"\bsm-?g977[a-z]{0,2}\b", re.IGNORECASE), "GS105G"),
    (re.compile(r"\bsm-?g975[a-z]{0,2}\b", re.IGNORECASE), "GS10P"),
    (re.compile(r"\bsm-?g973[a-z]{0,2}\b", re.IGNORECASE), "GS10"),
    (re.compile(r"\bsm-?g970[a-z]{0,2}\b", re.IGNORECASE), "GS10E"),
    (re.compile(r"\bsm-?g965[a-z]{0,2}\b", re.IGNORECASE), "GS9P"),
    (re.compile(r"\bsm-?g960[a-z]{0,2}\b", re.IGNORECASE), "GS9"),
    (re.compile(r"\bsm-?g955[a-z]{0,2}\b", re.IGNORECASE), "GS8P"),
    (re.compile(r"\bsm-?g950[a-z]{0,2}\b", re.IGNORECASE), "GS8"),
    (re.compile(r"\bsm-?g935[a-z]{0,2}\b", re.IGNORECASE), "GS7E"),
    (re.compile(r"\bsm-?g930[a-z]{0,2}\b", re.IGNORECASE), "GS7"),
    (re.compile(r"\bsm-?g925[a-z]{0,2}\b", re.IGNORECASE), "GS6E"),
    (re.compile(r"\bsm-?g920[a-z]{0,2}\b", re.IGNORECASE), "GS6"),
    (re.compile(r"\bsm-?g900[a-z]{0,2}\b", re.IGNORECASE), "GS5"),
    # ── Galaxy Note 시리즈 SKU ─────────────────────────────────────
    (re.compile(r"\bsm-?n986[a-z]{0,2}\b", re.IGNORECASE), "GN20U"),
    (re.compile(r"\bsm-?n985[a-z]{0,2}\b", re.IGNORECASE), "GN20U"),
    (re.compile(r"\bsm-?n981[a-z]{0,2}\b", re.IGNORECASE), "GN20"),
    (re.compile(r"\bsm-?n980[a-z]{0,2}\b", re.IGNORECASE), "GN20"),
    (re.compile(r"\bsm-?n976[a-z]{0,2}\b", re.IGNORECASE), "GN10P"),
    (re.compile(r"\bsm-?n975[a-z]{0,2}\b", re.IGNORECASE), "GN10P"),
    (re.compile(r"\bsm-?n971[a-z]{0,2}\b", re.IGNORECASE), "GN10"),
    (re.compile(r"\bsm-?n970[a-z]{0,2}\b", re.IGNORECASE), "GN10"),
    (re.compile(r"\bsm-?n960[a-z]{0,2}\b", re.IGNORECASE), "GN9"),
    (re.compile(r"\bsm-?n950[a-z]{0,2}\b", re.IGNORECASE), "GN8"),
    (re.compile(r"\bsm-?n930[a-z]{0,2}\b", re.IGNORECASE), "GN7"),
    (re.compile(r"\bsm-?n920[a-z]{0,2}\b", re.IGNORECASE), "GN5"),
    (re.compile(r"\bsm-?n910[a-z]{0,2}\b", re.IGNORECASE), "GN4"),
    (re.compile(r"\bsm-?n900[a-z]{0,2}\b", re.IGNORECASE), "GN3"),
    # ── Galaxy A 시리즈 SKU (A07~A57) ─────────────────────────────
    (re.compile(r"\bsm-?a576[a-z]{0,2}\b", re.IGNORECASE), "GA57"),
    (re.compile(r"\bsm-?a566[a-z]{0,2}\b", re.IGNORECASE), "GA56"),
    (re.compile(r"\bsm-?a556[a-z]{0,2}\b", re.IGNORECASE), "GA55"),
    (re.compile(r"\bsm-?a546[a-z]{0,2}\b", re.IGNORECASE), "GA54"),
    (re.compile(r"\bsm-?a536[a-z]{0,2}\b", re.IGNORECASE), "GA53"),
    (re.compile(r"\bsm-?a528[a-z]{0,2}\b", re.IGNORECASE), "GA52"),
    (re.compile(r"\bsm-?a526[a-z]{0,2}\b", re.IGNORECASE), "GA52"),
    (re.compile(r"\bsm-?a525[a-z]{0,2}\b", re.IGNORECASE), "GA52"),
    (re.compile(r"\bsm-?a516[a-z]{0,2}\b", re.IGNORECASE), "GA51"),
    (re.compile(r"\bsm-?a515[a-z]{0,2}\b", re.IGNORECASE), "GA51"),
    (re.compile(r"\bsm-?a505[a-z]{0,2}\b", re.IGNORECASE), "GA50"),
    (re.compile(r"\bsm-?a507[a-z]{0,2}\b", re.IGNORECASE), "GA50"),
    (re.compile(r"\bsm-?a376[a-z]{0,2}\b", re.IGNORECASE), "GA37"),
    (re.compile(r"\bsm-?a366[a-z]{0,2}\b", re.IGNORECASE), "GA36"),
    (re.compile(r"\bsm-?a356[a-z]{0,2}\b", re.IGNORECASE), "GA35"),
    (re.compile(r"\bsm-?a346[a-z]{0,2}\b", re.IGNORECASE), "GA34"),
    (re.compile(r"\bsm-?a336[a-z]{0,2}\b", re.IGNORECASE), "GA33"),
    (re.compile(r"\bsm-?a326[a-z]{0,2}\b", re.IGNORECASE), "GA32"),
    (re.compile(r"\bsm-?a325[a-z]{0,2}\b", re.IGNORECASE), "GA32"),
    (re.compile(r"\bsm-?a276[a-z]{0,2}\b", re.IGNORECASE), "GA27"),
    (re.compile(r"\bsm-?a266[a-z]{0,2}\b", re.IGNORECASE), "GA26"),
    (re.compile(r"\bsm-?a176[a-z]{0,2}\b", re.IGNORECASE), "GA17"),
    (re.compile(r"\bsm-?a166[a-z]{0,2}\b", re.IGNORECASE), "GA16"),
    (re.compile(r"\bsm-?a156[a-z]{0,2}\b", re.IGNORECASE), "GA15"),
    (re.compile(r"\bsm-?a146[a-z]{0,2}\b", re.IGNORECASE), "GA14"),
    (re.compile(r"\bsm-?a136[a-z]{0,2}\b", re.IGNORECASE), "GA13"),
    (re.compile(r"\bsm-?a127[a-z]{0,2}\b", re.IGNORECASE), "GA12"),
    (re.compile(r"\bsm-?a125[a-z]{0,2}\b", re.IGNORECASE), "GA12"),
    (re.compile(r"\bsm-?a076[a-z]{0,2}\b", re.IGNORECASE), "GA07"),
    # ── Galaxy Z Fold / Flip SKU ──────────────────────────────────
    (re.compile(r"\bsm-?f966[a-z]{0,2}\b", re.IGNORECASE), "GZF7"),
    (re.compile(r"\bsm-?f956[a-z]{0,2}\b", re.IGNORECASE), "GZF6"),
    (re.compile(r"\bsm-?f946[a-z]{0,2}\b", re.IGNORECASE), "GZF5"),
    (re.compile(r"\bsm-?f936[a-z]{0,2}\b", re.IGNORECASE), "GZF4"),
    (re.compile(r"\bsm-?f926[a-z]{0,2}\b", re.IGNORECASE), "GZF3"),
    (re.compile(r"\bsm-?f916[a-z]{0,2}\b", re.IGNORECASE), "GZF2"),
    (re.compile(r"\bsm-?f907[a-z]{0,2}\b", re.IGNORECASE), "GZF2"),
    (re.compile(r"\bsm-?f900[a-z]{0,2}\b", re.IGNORECASE), "GZF1"),
    (re.compile(r"\bsm-?f761[a-z]{0,2}\b", re.IGNORECASE), "GZFL7"),
    (re.compile(r"\bsm-?f741[a-z]{0,2}\b", re.IGNORECASE), "GZFL6"),
    (re.compile(r"\bsm-?f731[a-z]{0,2}\b", re.IGNORECASE), "GZFL5"),
    (re.compile(r"\bsm-?f721[a-z]{0,2}\b", re.IGNORECASE), "GZFL4"),
    (re.compile(r"\bsm-?f711[a-z]{0,2}\b", re.IGNORECASE), "GZFL3"),
    (re.compile(r"\bsm-?f707[a-z]{0,2}\b", re.IGNORECASE), "GZFL1"),  # Z Flip 5G — 카탈로그 없음 → GZFL1 폴백
    (re.compile(r"\bsm-?f700[a-z]{0,2}\b", re.IGNORECASE), "GZFL1"),
    # ── Galaxy M 시리즈 SKU (M14/M34/M54/M55 카탈로그 보유) ────
    (re.compile(r"\bsm-?m566[a-z]{0,2}\b", re.IGNORECASE), "GM55"),
    (re.compile(r"\bsm-?m546[a-z]{0,2}\b", re.IGNORECASE), "GM54"),
    (re.compile(r"\bsm-?m536[a-z]{0,2}\b", re.IGNORECASE), "GM54"),  # M53 → GM54 가까운 폴백
    (re.compile(r"\bsm-?m476[a-z]{0,2}\b", re.IGNORECASE), "GM34"),  # M44 → 가까운 GM34
    (re.compile(r"\bsm-?m446[a-z]{0,2}\b", re.IGNORECASE), "GM34"),
    (re.compile(r"\bsm-?m346[a-z]{0,2}\b", re.IGNORECASE), "GM34"),
    (re.compile(r"\bsm-?m336[a-z]{0,2}\b", re.IGNORECASE), "GM34"),
    (re.compile(r"\bsm-?m156[a-z]{0,2}\b", re.IGNORECASE), "GM14"),
    (re.compile(r"\bsm-?m146[a-z]{0,2}\b", re.IGNORECASE), "GM14"),
    (re.compile(r"\bsm-?m136[a-z]{0,2}\b", re.IGNORECASE), "GM14"),
    # ── Galaxy F 시리즈 SKU (F23/F25/F55 카탈로그) ────────────
    (re.compile(r"\bsm-?e556[a-z]{0,2}\b", re.IGNORECASE), "GF55"),  # F55 → SM-E556
    (re.compile(r"\bsm-?e256[a-z]{0,2}\b", re.IGNORECASE), "GF25"),
    (re.compile(r"\bsm-?e236[a-z]{0,2}\b", re.IGNORECASE), "GF23"),
    # ── Galaxy XCover SKU (G7xx 산업용) ───────────────────────
    (re.compile(r"\bsm-?g736[a-z]{0,2}\b", re.IGNORECASE), "GXC6"),  # XCover 6 Pro
    (re.compile(r"\bsm-?g715[a-z]{0,2}\b", re.IGNORECASE), "GXC5"),  # XCover Pro / 5
    (re.compile(r"\bsm-?g525[a-z]{0,2}\b", re.IGNORECASE), "GXC5"),  # XCover 5
    (re.compile(r"\bsm-?g398[a-z]{0,2}\b", re.IGNORECASE), "GXC4"),  # XCover 4s

    # ── Tab S 구형 .5/.4 사이즈 (R8 신규 — '10.5' 가 '10\b' 보다 우선) ──
    (re.compile(r"\b(?:galaxy\s+)?tab\s*s\s*10\.5\b", re.IGNORECASE), "GTS_105"),
    (re.compile(r"\b(?:galaxy\s+)?tab\s*s\s*8\.4\b", re.IGNORECASE), "GTS_84"),

    # ── Tab S 시리즈 (more specific first) ──────────────────────────
    (re.compile(r"\b(?:galaxy\s+)?tab\s*s\s*11\s*ultra\b", re.IGNORECASE), "GTABS11U"),
    (re.compile(r"\b(?:galaxy\s+)?tab\s*s\s*11\b", re.IGNORECASE), "GTABS11"),
    (re.compile(r"(?:갤(?:럭시)?\s*)?탭\s*s\s*11\s*울트라"), "GTABS11U"),
    (re.compile(r"(?:갤(?:럭시)?\s*)?탭\s*s\s*11"), "GTABS11"),

    (re.compile(r"\b(?:galaxy\s+)?tab\s*s\s*10\s*ultra\b", re.IGNORECASE), "GTABS10U"),
    (re.compile(r"\b(?:galaxy\s+)?tab\s*s\s*10\s*(?:plus|\+)", re.IGNORECASE), "GTABS10P"),
    (re.compile(r"\b(?:galaxy\s+)?tab\s*s\s*10\s*fe\b", re.IGNORECASE), "GTABS10F"),
    (re.compile(r"\b(?:galaxy\s+)?tab\s*s\s*10\b", re.IGNORECASE), "GTABS10"),
    (re.compile(r"(?:갤(?:럭시)?\s*)?탭\s*s\s*10\s*울트라"), "GTABS10U"),
    (re.compile(r"(?:갤(?:럭시)?\s*)?탭\s*s\s*10\s*fe", re.IGNORECASE), "GTABS10F"),
    (re.compile(r"(?:갤(?:럭시)?\s*)?탭\s*s\s*10"), "GTABS10"),

    (re.compile(r"\b(?:galaxy\s+)?tab\s*s\s*9\s*ultra\b", re.IGNORECASE), "GTABS9U"),
    # Tab S9 FE+ (R8 — 'FE+' 가 'FE\b' 보다 우선)
    (re.compile(r"\b(?:galaxy\s+)?tab\s*s\s*9\s*fe\+", re.IGNORECASE), "GTS9FP"),
    (re.compile(r"\b(?:galaxy\s+)?tab\s*s\s*9\s*(?:plus|\+)", re.IGNORECASE), "GTABS9P"),
    (re.compile(r"\b(?:galaxy\s+)?tab\s*s\s*9\s*fe\b", re.IGNORECASE), "GTABS9F"),
    (re.compile(r"\b(?:galaxy\s+)?tab\s*s\s*9\b", re.IGNORECASE), "GTABS9"),
    (re.compile(r"(?:갤(?:럭시)?\s*)?탭\s*s\s*9\s*fe", re.IGNORECASE), "GTABS9F"),
    (re.compile(r"(?:갤(?:럭시)?\s*)?탭\s*s\s*9"), "GTABS9"),

    (re.compile(r"\b(?:galaxy\s+)?tab\s*s\s*8\s*ultra\b", re.IGNORECASE), "GTABS8U"),
    (re.compile(r"\b(?:galaxy\s+)?tab\s*s\s*8\s*(?:plus|\+)", re.IGNORECASE), "GTABS8P"),
    (re.compile(r"\b(?:galaxy\s+)?tab\s*s\s*8\b", re.IGNORECASE), "GTABS8"),
    (re.compile(r"(?:갤(?:럭시)?\s*)?탭\s*s\s*8"), "GTABS8"),

    # Tab S 7 FE (R8 — '7 FE' 가 '7\b' 보다 우선)
    (re.compile(r"\b(?:galaxy\s+)?tab\s*s\s*7\s*fe\b", re.IGNORECASE), "GTS7F"),
    (re.compile(r"\b(?:galaxy\s+)?tab\s*s\s*7\s*(?:plus|\+)", re.IGNORECASE), "GTABS7P"),
    (re.compile(r"\b(?:galaxy\s+)?tab\s*s\s*7\b", re.IGNORECASE), "GTABS7"),
    (re.compile(r"(?:갤(?:럭시)?\s*)?탭\s*s\s*7"), "GTABS7"),

    # Tab S 6 Lite (R8 — '6 Lite' 가 '6\b' 보다 우선)
    (re.compile(r"\b(?:galaxy\s+)?tab\s*s\s*6\s*lite\b", re.IGNORECASE), "GTS6L"),
    (re.compile(r"\b(?:galaxy\s+)?tab\s*s\s*6\b", re.IGNORECASE), "GTABS6"),
    (re.compile(r"(?:갤(?:럭시)?\s*)?탭\s*s\s*6"), "GTABS6"),

    # ── Tab A 시리즈 ───────────────────────────────────────────────
    (re.compile(r"\b(?:galaxy\s+)?tab\s*a\s*11\b", re.IGNORECASE), "GTABA11"),
    (re.compile(r"갤(?:럭시\s*)?탭\s*a\s*11"), "GTABA11"),
    # Tab A 9.7 (R8 — '9.7' 가 '9\b' 보다 우선)
    (re.compile(r"\b(?:galaxy\s+)?tab\s*a\s*9\.7\b", re.IGNORECASE), "GTA97"),
    (re.compile(r"\b(?:galaxy\s+)?tab\s*a\s*9\s*(?:plus|\+)", re.IGNORECASE), "GTABA9P"),
    (re.compile(r"\b(?:galaxy\s+)?tab\s*a\s*9\b", re.IGNORECASE), "GTABA9"),
    (re.compile(r"갤(?:럭시\s*)?탭\s*a\s*9"), "GTABA9"),
    (re.compile(r"\b(?:galaxy\s+)?tab\s*a\s*8\b", re.IGNORECASE), "GTABA8"),
    (re.compile(r"갤(?:럭시\s*)?탭\s*a\s*8"), "GTABA8"),

    # ── Tab A 2019 (8" / 10.1" 옛 모델 — Harvest 7 X1 xda 매핑) ────
    # 'Samsung Galaxy Tab A (2019)' 같은 xda 제목 형식. 8" 명시가 우선이고,
    # 그 외엔 10.1" 모델로 통합 (xda 샘플상 8" 명시 1건). more-specific first.
    (re.compile(r"\b8[\"”']?\s+(?:samsung\s+)?galaxy\s+tab\s*a\b", re.IGNORECASE), "GTA8_19"),
    (re.compile(r"\b(?:galaxy\s+)?tab\s*a\s*\(?\s*2019\s*\)?", re.IGNORECASE), "GTA10_19"),

    # ── Tab Active5 ────────────────────────────────────────────────
    (re.compile(r"\btab\s*active\s*5(?:\s*pro)?\b", re.IGNORECASE), "GTABACT5"),
    (re.compile(r"탭\s*액티브\s*5"), "GTABACT5"),

    # ── Watch9 ─────────────────────────────────────────────────────
    (re.compile(r"\b(?:galaxy\s+)?watch\s*9\b", re.IGNORECASE), "GW9"),
    (re.compile(r"갤(?:럭시\s*)?워치\s*9|갤워치9"), "GW9"),

    # ── XCover (러기드 — 고유 명칭) ────────────────────────────────
    (re.compile(r"\bxcover\s*7(?:\s*pro)?\b", re.IGNORECASE), "GXC7"),
    (re.compile(r"엑스커버\s*7"), "GXC7"),
    (re.compile(r"\bxcover\s*6(?:\s*pro)?\b", re.IGNORECASE), "GXC6"),
    (re.compile(r"엑스커버\s*6"), "GXC6"),
    (re.compile(r"\bxcover\s*5\b", re.IGNORECASE), "GXC5"),
    (re.compile(r"엑스커버\s*5"), "GXC5"),
    (re.compile(r"\bxcover\s*4\b", re.IGNORECASE), "GXC4"),
    (re.compile(r"엑스커버\s*4"), "GXC4"),

    # ── Note Pro 12.2 (2014 태블릿 — .2 가 있어야만) ───────────────
    (re.compile(r"\bgalaxy\s+note\s*12\.2\b|\bnote\s*12\.2\b", re.IGNORECASE), "GNT122"),

    # ── A 시리즈 신규 (Galaxy 컨텍스트 한정으로 단어경계로 안전 확보) ─
    (re.compile(r"\bgalaxy\s+a\s*57\b", re.IGNORECASE), "GA57"),
    (re.compile(r"갤(?:럭시\s*)?a\s*57"), "GA57"),
    (re.compile(r"\bgalaxy\s+a\s*37\b", re.IGNORECASE), "GA37"),
    (re.compile(r"갤(?:럭시\s*)?a\s*37"), "GA37"),
    (re.compile(r"\bgalaxy\s+a\s*36\b", re.IGNORECASE), "GA36"),
    (re.compile(r"갤(?:럭시\s*)?a\s*36"), "GA36"),
    (re.compile(r"\bgalaxy\s+a\s*27\b", re.IGNORECASE), "GA27"),
    (re.compile(r"갤(?:럭시\s*)?a\s*27"), "GA27"),
    (re.compile(r"\bgalaxy\s+a\s*26\b", re.IGNORECASE), "GA26"),
    (re.compile(r"갤(?:럭시\s*)?a\s*26"), "GA26"),
    (re.compile(r"\bgalaxy\s+a\s*17\b", re.IGNORECASE), "GA17"),
    (re.compile(r"갤(?:럭시\s*)?a\s*17|갤a17"), "GA17"),
    (re.compile(r"\bgalaxy\s+a\s*16\b", re.IGNORECASE), "GA16"),
    (re.compile(r"갤(?:럭시\s*)?a\s*16"), "GA16"),
    (re.compile(r"\bgalaxy\s+a\s*07\b", re.IGNORECASE), "GA07"),
    (re.compile(r"갤(?:럭시\s*)?a\s*07"), "GA07"),

    # ── M / F 시리즈 (Galaxy 컨텍스트 필수) ────────────────────────
    (re.compile(r"\bgalaxy\s+m\s*55\b", re.IGNORECASE), "GM55"),
    (re.compile(r"\bgalaxy\s+m\s*54\b", re.IGNORECASE), "GM54"),
    (re.compile(r"\bgalaxy\s+m\s*34\b", re.IGNORECASE), "GM34"),
    (re.compile(r"\bgalaxy\s+m\s*14\b", re.IGNORECASE), "GM14"),
    (re.compile(r"\bgalaxy\s+f\s*55\b", re.IGNORECASE), "GF55"),
    (re.compile(r"\bgalaxy\s+f\s*25\b", re.IGNORECASE), "GF25"),
    (re.compile(r"\bgalaxy\s+f\s*23\b", re.IGNORECASE), "GF23"),

    # ═══════════════════════════════════════════════════════════════════
    # R8 신규: Galaxy 전 세대 완전 커버 (0011 catalog)
    # ═══════════════════════════════════════════════════════════════════

    # ── A 구형 연식 표기 (Galaxy 컨텍스트 필수) ──
    #   "Galaxy A3 (2015)" / "갤럭시 A3 2015" / "A3 2015" 등.
    #   more-specific (긴 코드 + 연식) 먼저.
    #   주의: 연식이 비-옵션 — '\(?20XX\)?' 의 '(' 와 ')' 만 옵션, 디지트는 필수.
    (re.compile(r"\bgalaxy\s+a\s*9\s*pro\s*\(?\s*2016\s*\)?", re.IGNORECASE), "GA9P_16"),
    (re.compile(r"\bgalaxy\s+a\s*9\s*\(?\s*2018\s*\)?", re.IGNORECASE), "GA9_18"),
    (re.compile(r"\bgalaxy\s+a\s*9\s*\(?\s*2016\s*\)?", re.IGNORECASE), "GA9_16"),
    (re.compile(r"\bgalaxy\s+a\s*9\s*\(?\s*2015\s*\)?", re.IGNORECASE), "GA9_15"),
    (re.compile(r"\bgalaxy\s+a\s*8\+\s*\(?\s*2018\s*\)?", re.IGNORECASE), "GA8P_18"),
    (re.compile(r"\bgalaxy\s+a\s*8\s*plus\s*\(?\s*2018\s*\)?", re.IGNORECASE), "GA8P_18"),
    (re.compile(r"\bgalaxy\s+a\s*8\s*\(?\s*2018\s*\)?", re.IGNORECASE), "GA8_18"),
    (re.compile(r"\bgalaxy\s+a\s*8\s*\(?\s*2015\s*\)?", re.IGNORECASE), "GA8_15"),
    (re.compile(r"\bgalaxy\s+a\s*7\s*\(?\s*2018\s*\)?", re.IGNORECASE), "GA7_18"),
    (re.compile(r"\bgalaxy\s+a\s*7\s*\(?\s*2017\s*\)?", re.IGNORECASE), "GA7_17"),
    (re.compile(r"\bgalaxy\s+a\s*7\s*\(?\s*2016\s*\)?", re.IGNORECASE), "GA7_16"),
    (re.compile(r"\bgalaxy\s+a\s*7\s*\(?\s*2015\s*\)?", re.IGNORECASE), "GA7_15"),
    (re.compile(r"\bgalaxy\s+a\s*6\+\s*\(?\s*2018\s*\)?", re.IGNORECASE), "GA6P_18"),
    (re.compile(r"\bgalaxy\s+a\s*6\s*plus\s*\(?\s*2018\s*\)?", re.IGNORECASE), "GA6P_18"),
    (re.compile(r"\bgalaxy\s+a\s*6\s*\(?\s*2018\s*\)?", re.IGNORECASE), "GA6_18"),
    (re.compile(r"\bgalaxy\s+a\s*5\s*\(?\s*2017\s*\)?", re.IGNORECASE), "GA5_17"),
    (re.compile(r"\bgalaxy\s+a\s*5\s*\(?\s*2016\s*\)?", re.IGNORECASE), "GA5_16"),
    (re.compile(r"\bgalaxy\s+a\s*5\s*\(?\s*2015\s*\)?", re.IGNORECASE), "GA5_15"),
    (re.compile(r"\bgalaxy\s+a\s*3\s*\(?\s*2017\s*\)?", re.IGNORECASE), "GA3_17"),
    (re.compile(r"\bgalaxy\s+a\s*3\s*\(?\s*2016\s*\)?", re.IGNORECASE), "GA3_16"),
    (re.compile(r"\bgalaxy\s+a\s*3\s*\(?\s*2015\s*\)?", re.IGNORECASE), "GA3_15"),

    # ── A0X (A01~A03 core 변형 — Galaxy 컨텍스트 필수) ──
    (re.compile(r"\bgalaxy\s+a\s*03\s*core\b", re.IGNORECASE), "GA03C"),
    (re.compile(r"\bgalaxy\s+a\s*03\s*s\b", re.IGNORECASE), "GA03S"),
    (re.compile(r"\bgalaxy\s+a\s*03\b", re.IGNORECASE), "GA03"),
    (re.compile(r"\bgalaxy\s+a\s*02\s*s\b", re.IGNORECASE), "GA02S"),
    (re.compile(r"\bgalaxy\s+a\s*02\b", re.IGNORECASE), "GA02"),
    (re.compile(r"\bgalaxy\s+a\s*01\b", re.IGNORECASE), "GA01"),

    # ── A1X / A2X / A3X 변형 (10e/10s/20e/20s/30s/21s) ──
    (re.compile(r"\bgalaxy\s+a\s*10\s*e\b", re.IGNORECASE), "GA10E"),
    (re.compile(r"\bgalaxy\s+a\s*10\s*s\b", re.IGNORECASE), "GA10S"),
    (re.compile(r"\bgalaxy\s+a\s*10\b", re.IGNORECASE), "GA10"),
    (re.compile(r"\bgalaxy\s+a\s*11\b", re.IGNORECASE), "GA11"),
    (re.compile(r"\bgalaxy\s+a\s*12\b", re.IGNORECASE), "GA12"),
    (re.compile(r"\bgalaxy\s+a\s*13\b", re.IGNORECASE), "GA13"),
    (re.compile(r"\bgalaxy\s+a\s*20\s*e\b", re.IGNORECASE), "GA20E"),
    (re.compile(r"\bgalaxy\s+a\s*20\s*s\b", re.IGNORECASE), "GA20S"),
    (re.compile(r"\bgalaxy\s+a\s*20\b", re.IGNORECASE), "GA20"),
    (re.compile(r"\bgalaxy\s+a\s*21\s*s\b", re.IGNORECASE), "GA21S"),
    (re.compile(r"\bgalaxy\s+a\s*21\b", re.IGNORECASE), "GA21"),
    (re.compile(r"\bgalaxy\s+a\s*22\b", re.IGNORECASE), "GA22"),
    (re.compile(r"\bgalaxy\s+a\s*23\b", re.IGNORECASE), "GA23"),
    (re.compile(r"\bgalaxy\s+a\s*24\b", re.IGNORECASE), "GA24"),
    (re.compile(r"\bgalaxy\s+a\s*25\b", re.IGNORECASE), "GA25"),
    (re.compile(r"\bgalaxy\s+a\s*30\s*s\b", re.IGNORECASE), "GA30S"),
    (re.compile(r"\bgalaxy\s+a\s*30\b", re.IGNORECASE), "GA30"),
    (re.compile(r"\bgalaxy\s+a\s*31\b", re.IGNORECASE), "GA31"),
    (re.compile(r"\bgalaxy\s+a\s*32\b", re.IGNORECASE), "GA32"),
    (re.compile(r"\bgalaxy\s+a\s*33\b", re.IGNORECASE), "GA33"),
    (re.compile(r"\bgalaxy\s+a\s*34\b", re.IGNORECASE), "GA34"),
    (re.compile(r"\bgalaxy\s+a\s*35\b", re.IGNORECASE), "GA35"),
    (re.compile(r"\bgalaxy\s+a\s*40\b", re.IGNORECASE), "GA40"),
    (re.compile(r"\bgalaxy\s+a\s*41\b", re.IGNORECASE), "GA41"),
    (re.compile(r"\bgalaxy\s+a\s*42\b", re.IGNORECASE), "GA42"),
    (re.compile(r"\bgalaxy\s+a\s*60\b", re.IGNORECASE), "GA60"),
    (re.compile(r"\bgalaxy\s+a\s*70\b", re.IGNORECASE), "GA70"),
    (re.compile(r"\bgalaxy\s+a\s*71\b", re.IGNORECASE), "GA71"),
    (re.compile(r"\bgalaxy\s+a\s*72\b", re.IGNORECASE), "GA72"),
    (re.compile(r"\bgalaxy\s+a\s*73\b", re.IGNORECASE), "GA73"),
    (re.compile(r"\bgalaxy\s+a\s*80\b", re.IGNORECASE), "GA80"),
    (re.compile(r"\bgalaxy\s+a\s*90\b", re.IGNORECASE), "GA90"),
    # 한국어 변형
    (re.compile(r"갤(?:럭시\s*)?a\s*50\b"), "GA50"),
    (re.compile(r"갤(?:럭시\s*)?a\s*33\b"), "GA33"),
    (re.compile(r"갤(?:럭시\s*)?a\s*35\b"), "GA35"),

    # ── J 시리즈 (Galaxy 컨텍스트 필수 — 일반어 충돌 회피) ──
    (re.compile(r"\bgalaxy\s+j\s*7\s*pro\b", re.IGNORECASE), "GJ7PRO"),
    (re.compile(r"\bgalaxy\s+j\s*7\s*prime\b", re.IGNORECASE), "GJ7PRM"),
    (re.compile(r"\bgalaxy\s+j\s*7\s*max\b", re.IGNORECASE), "GJ7MAX"),
    (re.compile(r"\bgalaxy\s+j\s*7\s*\(?2017\)?", re.IGNORECASE), "GJ7_17"),
    (re.compile(r"\bgalaxy\s+j\s*7\s*\(?2016\)?", re.IGNORECASE), "GJ7_16"),
    (re.compile(r"\bgalaxy\s+j\s*7\b", re.IGNORECASE), "GJ7"),
    (re.compile(r"\bgalaxy\s+j\s*5\s*prime\b", re.IGNORECASE), "GJ5PRM"),
    (re.compile(r"\bgalaxy\s+j\s*5\s*\(?2017\)?", re.IGNORECASE), "GJ5_17"),
    (re.compile(r"\bgalaxy\s+j\s*5\s*\(?2016\)?", re.IGNORECASE), "GJ5_16"),
    (re.compile(r"\bgalaxy\s+j\s*5\b", re.IGNORECASE), "GJ5"),
    (re.compile(r"\bgalaxy\s+j\s*3\s*\(?2017\)?", re.IGNORECASE), "GJ3_17"),
    (re.compile(r"\bgalaxy\s+j\s*3\s*\(?2016\)?", re.IGNORECASE), "GJ3_16"),
    (re.compile(r"\bgalaxy\s+j\s*2\s*pro\b", re.IGNORECASE), "GJ2PRO"),
    (re.compile(r"\bgalaxy\s+j\s*2\s*\(?2016\)?", re.IGNORECASE), "GJ2_16"),
    (re.compile(r"\bgalaxy\s+j\s*2\b", re.IGNORECASE), "GJ2"),
    (re.compile(r"\bgalaxy\s+j\s*1\s*mini\b", re.IGNORECASE), "GJ1M"),
    (re.compile(r"\bgalaxy\s+j\s*1\b", re.IGNORECASE), "GJ1"),
    (re.compile(r"\bgalaxy\s+j\s*8\b", re.IGNORECASE), "GJ8"),

    # ── M 시리즈 확장 (R8 신규 catalog) ──
    (re.compile(r"\bgalaxy\s+m\s*52\b", re.IGNORECASE), "GM52"),
    (re.compile(r"\bgalaxy\s+m\s*51\b", re.IGNORECASE), "GM51"),
    (re.compile(r"\bgalaxy\s+m\s*53\b", re.IGNORECASE), "GM53"),
    (re.compile(r"\bgalaxy\s+m\s*42\b", re.IGNORECASE), "GM42"),
    (re.compile(r"\bgalaxy\s+m\s*40\b", re.IGNORECASE), "GM40"),
    (re.compile(r"\bgalaxy\s+m\s*33\b", re.IGNORECASE), "GM33"),
    (re.compile(r"\bgalaxy\s+m\s*32\b", re.IGNORECASE), "GM32"),
    (re.compile(r"\bgalaxy\s+m\s*31\s*s\b", re.IGNORECASE), "GM31S"),
    (re.compile(r"\bgalaxy\s+m\s*31\b", re.IGNORECASE), "GM31"),
    (re.compile(r"\bgalaxy\s+m\s*30\s*s\b", re.IGNORECASE), "GM30S"),
    (re.compile(r"\bgalaxy\s+m\s*30\b", re.IGNORECASE), "GM30"),
    (re.compile(r"\bgalaxy\s+m\s*23\b", re.IGNORECASE), "GM23"),
    (re.compile(r"\bgalaxy\s+m\s*22\b", re.IGNORECASE), "GM22"),
    (re.compile(r"\bgalaxy\s+m\s*21\b", re.IGNORECASE), "GM21"),
    (re.compile(r"\bgalaxy\s+m\s*13\b", re.IGNORECASE), "GM13"),
    (re.compile(r"\bgalaxy\s+m\s*12\b", re.IGNORECASE), "GM12"),
    (re.compile(r"\bgalaxy\s+m\s*11\b", re.IGNORECASE), "GM11"),
    (re.compile(r"\bgalaxy\s+m\s*10\b", re.IGNORECASE), "GM10"),
    (re.compile(r"\bgalaxy\s+m\s*02\b", re.IGNORECASE), "GM02"),
    (re.compile(r"\bgalaxy\s+m\s*01\b", re.IGNORECASE), "GM01"),

    # ── F 시리즈 확장 ──
    (re.compile(r"\bgalaxy\s+f\s*02\s*s\b", re.IGNORECASE), "GF02S"),
    (re.compile(r"\bgalaxy\s+f\s*12\b", re.IGNORECASE), "GF12"),
    (re.compile(r"\bgalaxy\s+f\s*22\b", re.IGNORECASE), "GF22"),
    (re.compile(r"\bgalaxy\s+f\s*41\b", re.IGNORECASE), "GF41"),
    (re.compile(r"\bgalaxy\s+f\s*42\b", re.IGNORECASE), "GF42"),
    (re.compile(r"\bgalaxy\s+f\s*52\b", re.IGNORECASE), "GF52"),
    (re.compile(r"\bgalaxy\s+f\s*54\b", re.IGNORECASE), "GF54"),
    (re.compile(r"\bgalaxy\s+f\s*62\b", re.IGNORECASE), "GF62"),

    # ── Tab 구형 (1~4 + S/PRO/A 구형 + Active 1~4) ──
    (re.compile(r"\b(?:galaxy\s+)?tab\s*active\s*4(?:\s*pro)?\b", re.IGNORECASE), "GTACT4P"),
    (re.compile(r"\b(?:galaxy\s+)?tab\s*active\s*3\b", re.IGNORECASE), "GTACT3"),
    (re.compile(r"\b(?:galaxy\s+)?tab\s*active\s*2\b", re.IGNORECASE), "GTACT2"),
    (re.compile(r"(?:갤(?:럭시)?\s*)?탭\s*액티브\s*4(?:\s*pro)?", re.IGNORECASE), "GTACT4P"),
    (re.compile(r"(?:갤(?:럭시)?\s*)?탭\s*액티브\s*3"), "GTACT3"),
    (re.compile(r"(?:갤(?:럭시)?\s*)?탭\s*액티브\s*2"), "GTACT2"),
    (re.compile(r"\btabpro\s*12\.2\b", re.IGNORECASE), "GTP_122"),
    (re.compile(r"\btabpro\s*10\.1\b", re.IGNORECASE), "GTP_101"),
    (re.compile(r"\btabpro\s*8\.4\b", re.IGNORECASE), "GTP_84"),
    (re.compile(r"\b(?:galaxy\s+)?tab\s*pro\s*12\.2\b", re.IGNORECASE), "GTP_122"),
    (re.compile(r"\b(?:galaxy\s+)?tab\s*pro\s*10\.1\b", re.IGNORECASE), "GTP_101"),
    (re.compile(r"\b(?:galaxy\s+)?tab\s*pro\s*8\.4\b", re.IGNORECASE), "GTP_84"),
    (re.compile(r"\b(?:galaxy\s+)?tab\s*s\s*10\.5\b", re.IGNORECASE), "GTS_105"),
    (re.compile(r"\b(?:galaxy\s+)?tab\s*s\s*8\.4\b", re.IGNORECASE), "GTS_84"),
    (re.compile(r"\b(?:galaxy\s+)?tab\s*s\s*5\s*e\b", re.IGNORECASE), "GTS5E"),
    (re.compile(r"\b(?:galaxy\s+)?tab\s*s\s*6\s*lite\b", re.IGNORECASE), "GTS6L"),
    (re.compile(r"\b(?:galaxy\s+)?tab\s*s\s*7\s*fe\b", re.IGNORECASE), "GTS7F"),
    (re.compile(r"\b(?:galaxy\s+)?tab\s*s\s*9\s*fe\+", re.IGNORECASE), "GTS9FP"),
    (re.compile(r"\b(?:galaxy\s+)?tab\s*s\s*10\s*fe\+", re.IGNORECASE), "GTS10FP"),
    (re.compile(r"\b(?:galaxy\s+)?tab\s*s\s*11\+", re.IGNORECASE), "GTS11P"),
    (re.compile(r"\b(?:galaxy\s+)?tab\s*s\s*2\b", re.IGNORECASE), "GTS2"),
    (re.compile(r"\b(?:galaxy\s+)?tab\s*s\s*3\b", re.IGNORECASE), "GTS3"),
    (re.compile(r"\b(?:galaxy\s+)?tab\s*s\s*4\b", re.IGNORECASE), "GTS4"),
    (re.compile(r"\b(?:galaxy\s+)?tab\s*a\s*7\s*lite\b", re.IGNORECASE), "GTA7L"),
    (re.compile(r"\b(?:galaxy\s+)?tab\s*a\s*7\b", re.IGNORECASE), "GTA7"),
    (re.compile(r"\b(?:galaxy\s+)?tab\s*a\s*9\.7\b", re.IGNORECASE), "GTA97"),
    (re.compile(r"\b(?:galaxy\s+)?tab\s*4\s*10\.1\b", re.IGNORECASE), "GT4_10"),
    (re.compile(r"\b(?:galaxy\s+)?tab\s*4\s*8\.0\b", re.IGNORECASE), "GT4_8"),
    (re.compile(r"\b(?:galaxy\s+)?tab\s*4\s*7\.0\b", re.IGNORECASE), "GT4_7"),
    (re.compile(r"\b(?:galaxy\s+)?tab\s*3\s*10\.1\b", re.IGNORECASE), "GT3_10"),
    (re.compile(r"\b(?:galaxy\s+)?tab\s*3\s*8\.0\b", re.IGNORECASE), "GT3_8"),
    (re.compile(r"\b(?:galaxy\s+)?tab\s*3\s*7\.0\b", re.IGNORECASE), "GT3_7"),
    (re.compile(r"\b(?:galaxy\s+)?tab\s*2\s*10\.1\b", re.IGNORECASE), "GT2_10"),
    (re.compile(r"\b(?:galaxy\s+)?tab\s*2\s*7\.0\b", re.IGNORECASE), "GT2_7"),

    # ── XCover 구형 (1~4s + Pro 단독) ──
    (re.compile(r"\bxcover\s*4\s*s\b", re.IGNORECASE), "GXC4S"),
    (re.compile(r"\bxcover\s*3\b", re.IGNORECASE), "GXC3"),
    (re.compile(r"\bxcover\s*2\b", re.IGNORECASE), "GXC2"),
    (re.compile(r"\bxcover\s+pro\b", re.IGNORECASE), "GXCPRO"),
    (re.compile(r"엑스커버\s*4\s*s"), "GXC4S"),
    (re.compile(r"엑스커버\s*3"), "GXC3"),
    (re.compile(r"엑스커버\s*2"), "GXC2"),
    (re.compile(r"엑스커버\s*프로"), "GXCPRO"),

    # ── Watch / Gear / Fit 변형 ──
    (re.compile(r"\b(?:galaxy\s+)?watch\s*8\s*classic\b", re.IGNORECASE), "GW8C"),
    (re.compile(r"\b(?:galaxy\s+)?watch\s*6\s*classic\b", re.IGNORECASE), "GW6C"),
    (re.compile(r"\b(?:galaxy\s+)?watch\s*4\s*classic\b", re.IGNORECASE), "GW4C"),
    (re.compile(r"\b(?:galaxy\s+)?watch\s*fe\b", re.IGNORECASE), "GWFE"),
    (re.compile(r"\b(?:galaxy\s+)?watch\s*active\s*3\b", re.IGNORECASE), "GWA3"),
    (re.compile(r"갤(?:럭시\s*)?워치\s*8\s*클래식"), "GW8C"),
    (re.compile(r"갤(?:럭시\s*)?워치\s*6\s*클래식"), "GW6C"),
    (re.compile(r"갤(?:럭시\s*)?워치\s*4\s*클래식"), "GW4C"),
    (re.compile(r"갤(?:럭시\s*)?워치\s*fe", re.IGNORECASE), "GWFE"),
    (re.compile(r"갤(?:럭시\s*)?워치\s*액티브\s*3"), "GWA3"),
    (re.compile(r"\bgear\s*sport\b", re.IGNORECASE), "GGSPORT"),
    (re.compile(r"\bgear\s*s\s*3\b", re.IGNORECASE), "GGS3"),
    (re.compile(r"\bgear\s*s\s*2\b", re.IGNORECASE), "GGS2"),
    (re.compile(r"\bgear\s*s\b(?!\d)", re.IGNORECASE), "GGS"),
    (re.compile(r"\bgear\s*2\s*neo\b", re.IGNORECASE), "GGEAR2N"),
    (re.compile(r"\bgalaxy\s+gear\s*2\b", re.IGNORECASE), "GGEAR2"),
    (re.compile(r"\bgalaxy\s+gear\b(?!\s*2)", re.IGNORECASE), "GGEAR1"),
    (re.compile(r"\bgear\s*fit\s*2\b", re.IGNORECASE), "GGFIT2"),
    (re.compile(r"\bgear\s*fit\b(?!\s*2)", re.IGNORECASE), "GGEARFIT"),
    (re.compile(r"\bgalaxy\s+fit\s*3\b", re.IGNORECASE), "GFIT3"),
    (re.compile(r"\bgalaxy\s+fit\s*2\b", re.IGNORECASE), "GFIT2"),
    (re.compile(r"\bgalaxy\s+fit\s*e\b", re.IGNORECASE), "GFITE"),
    (re.compile(r"\bgalaxy\s+fit\b(?!\s*[23e])", re.IGNORECASE), "GFIT"),
    (re.compile(r"갤(?:럭시\s*)?핏\s*3"), "GFIT3"),
    (re.compile(r"갤(?:럭시\s*)?핏\s*2"), "GFIT2"),

    # ── Buds / IconX 변형 ──
    (re.compile(r"\bgear\s*iconx\s*2018\b", re.IGNORECASE), "GICX2"),
    (re.compile(r"\bgear\s*iconx\b(?!\s*2018)", re.IGNORECASE), "GICX"),
    (re.compile(r"\bgalaxy\s+buds\s*fe\b", re.IGNORECASE), "GBFE"),
    (re.compile(r"\bgalaxy\s+buds\+|\bgalaxy\s+buds\s*plus\b", re.IGNORECASE), "GBPLUS"),
    (re.compile(r"갤(?:럭시\s*)?버즈\s*\+|갤(?:럭시\s*)?버즈\s*플러스"), "GBPLUS"),
    (re.compile(r"갤(?:럭시\s*)?버즈\s*fe", re.IGNORECASE), "GBFE"),

    # ── Ring ──
    (re.compile(r"\bgalaxy\s+ring\b", re.IGNORECASE), "GR1"),
    (re.compile(r"갤(?:럭시\s*)?링\b"), "GR1"),

    # ── 옛 폰 (Galaxy 컨텍스트 필수 — 한국어/영문 단어 충돌 회피) ──
    (re.compile(r"\bgalaxy\s+note\s*edge\b", re.IGNORECASE), "GNEDGE"),
    (re.compile(r"\bgalaxy\s+note\s*fe\b", re.IGNORECASE), "GNFE"),
    (re.compile(r"\bgalaxy\s+note\s*10\s*lite\b", re.IGNORECASE), "GN10L"),
    (re.compile(r"\bgalaxy\s+s\s*10\s*lite\b", re.IGNORECASE), "GS10L"),
    (re.compile(r"\bgalaxy\s+s\s*9\s*active\b", re.IGNORECASE), "GS9A"),
    (re.compile(r"\bgalaxy\s+s\s*8\s*active\b", re.IGNORECASE), "GS8A"),
    (re.compile(r"\bgalaxy\s+s\s*6\s*edge\+", re.IGNORECASE), "GS6EP"),
    (re.compile(r"\bgalaxy\s+s\s*6\s*edge\s*plus\b", re.IGNORECASE), "GS6EP"),
    (re.compile(r"\bgalaxy\s+s\s*5\s*mini\b", re.IGNORECASE), "GS5MINI"),
    (re.compile(r"\bgalaxy\s+s\s*4\s*mini\b", re.IGNORECASE), "GS4MINI"),
    (re.compile(r"\bgalaxy\s+s\s*3\s*mini\b", re.IGNORECASE), "GS3MINI"),
    (re.compile(r"\bgalaxy\s+fold\s*5g\b", re.IGNORECASE), "GZF1_5G"),
    (re.compile(r"\bgalaxy\s+z\s*flip\s*5g\b", re.IGNORECASE), "GZFL1_5G"),
    (re.compile(r"\bgalaxy\s+grand\s*prime\+", re.IGNORECASE), "GGRPRMP"),
    (re.compile(r"\bgalaxy\s+grand\s*prime\b", re.IGNORECASE), "GGRPRM"),
    (re.compile(r"\bgalaxy\s+grand\s*2\b", re.IGNORECASE), "GGRAND2"),
    (re.compile(r"\bgalaxy\s+grand\b(?!\s*(?:2|prime))", re.IGNORECASE), "GGRAND"),
    (re.compile(r"\bgalaxy\s+core\s*prime\b", re.IGNORECASE), "GCOREPRM"),
    (re.compile(r"\bgalaxy\s+core\s*2\b", re.IGNORECASE), "GCORE2"),
    (re.compile(r"\bgalaxy\s+core\b(?!\s*(?:2|prime))", re.IGNORECASE), "GCORE"),
    (re.compile(r"\bgalaxy\s+mega\s*6\.3\b", re.IGNORECASE), "GMEGA63"),
    (re.compile(r"\bgalaxy\s+mega\s*5\.8\b", re.IGNORECASE), "GMEGA58"),
    # Track B 추가: 바리에이션 — "6.3in Galaxy Mega" 처럼 디지트가 앞에 오는 경우.
    # 우선 6.3 / 5.8 specific 패턴이 위에서 잡고, 아래는 bare fallback (대형판매가 GMEGA63 가 다수).
    (re.compile(r"\bgalaxy\s+mega\b", re.IGNORECASE), "GMEGA63"),
    (re.compile(r"\bgalaxy\s+ace\s*4\b", re.IGNORECASE), "GACE4"),
    (re.compile(r"\bgalaxy\s+ace\s*3\b", re.IGNORECASE), "GACE3"),
    (re.compile(r"\bgalaxy\s+ace\s*2\b", re.IGNORECASE), "GACE2"),
    (re.compile(r"\bgalaxy\s+ace\b(?!\s*[234])", re.IGNORECASE), "GACE"),
    (re.compile(r"\bgalaxy\s+on\s*7\b", re.IGNORECASE), "GON7"),
    (re.compile(r"\bgalaxy\s+on\s*5\b", re.IGNORECASE), "GON5"),
    (re.compile(r"\bgalaxy\s+pocket\s*2\b", re.IGNORECASE), "GPOCKET2"),
    (re.compile(r"\bgalaxy\s+pocket\b(?!\s*2)", re.IGNORECASE), "GPOCKET"),
    (re.compile(r"\bgalaxy\s+mini\s*2\b", re.IGNORECASE), "GMINI2"),
    (re.compile(r"\bgalaxy\s+mini\b(?!\s*2)", re.IGNORECASE), "GMINI"),
    (re.compile(r"\bgalaxy\s+star\s*2\b", re.IGNORECASE), "GSTAR2"),
    (re.compile(r"\bgalaxy\s+star\b(?!\s*2)", re.IGNORECASE), "GSTAR"),
    (re.compile(r"\bgalaxy\s+win\s*pro\b", re.IGNORECASE), "GWINPRO"),
    (re.compile(r"\bgalaxy\s+win\b(?!\s*pro)", re.IGNORECASE), "GWIN"),
    (re.compile(r"\bgalaxy\s+y\s*duos\b", re.IGNORECASE), "GYDUOS"),
    (re.compile(r"\bgalaxy\s+y\b(?!\s*duos)", re.IGNORECASE), "GY"),
    (re.compile(r"\bgalaxy\s+trend\s*lite\b", re.IGNORECASE), "GTRENDL"),
    (re.compile(r"\bgalaxy\s+trend\b(?!\s*lite)", re.IGNORECASE), "GTREND"),
    (re.compile(r"\bgalaxy\s+fame\b", re.IGNORECASE), "GFAME"),
    (re.compile(r"\bgalaxy\s+music\b", re.IGNORECASE), "GMUSIC"),
    (re.compile(r"\bgalaxy\s+express\s*2\b", re.IGNORECASE), "GEXPR2"),
    (re.compile(r"\bgalaxy\s+express\b(?!\s*2)", re.IGNORECASE), "GEXPRESS"),
    (re.compile(r"\bgalaxy\s+beam\b", re.IGNORECASE), "GBEAM"),
    (re.compile(r"\bgalaxy\s+s\s*plus\b", re.IGNORECASE), "GSPLUS"),
    (re.compile(r"\bgalaxy\s+i7500\b", re.IGNORECASE), "GI7500"),
    # 한국어 옛 폰 (Galaxy 컨텍스트 필수 — '갤럭시 ' 패턴)
    (re.compile(r"갤럭시\s*노트\s*엣지"), "GNEDGE"),
    (re.compile(r"갤럭시\s*노트\s*fe", re.IGNORECASE), "GNFE"),
    (re.compile(r"갤럭시\s*그랜드\s*프라임\+"), "GGRPRMP"),
    (re.compile(r"갤럭시\s*그랜드\s*프라임"), "GGRPRM"),
    (re.compile(r"갤럭시\s*메가\s*6\.3"), "GMEGA63"),
    (re.compile(r"갤럭시\s*메가\s*5\.8"), "GMEGA58"),
    # 갤럭시 에이스는 한국어 변형 단순 substring 으로 MODEL_MAP 처리 (위 None placeholder 제거)
    (re.compile(r"갤럭시\s*온\s*7"), "GON7"),
    (re.compile(r"갤럭시\s*온\s*5"), "GON5"),
    # Track B 한국어 OLD 폰 negative-lookahead — "스타터팩/미니멀/트렌디" 등 일반어 충돌 회피
    (re.compile(r"갤럭시\s*스타\s*2"), "GSTAR2"),
    (re.compile(r"갤럭시\s*스타(?![터트])"), "GSTAR"),
    (re.compile(r"갤럭시\s*미니\s*2"), "GMINI2"),
    (re.compile(r"갤럭시\s*미니(?!멀)"), "GMINI"),
    (re.compile(r"갤럭시\s*트렌드\s*라이트"), "GTRENDL"),
    (re.compile(r"갤럭시\s*트렌드(?!세터|디)"), "GTREND"),

    # ═══════════════════════════════════════════════════════════════════
    # R15 트랙 C: 한국어/영문 변형 보강 (공백 옵션 + 노트 + 탭1)
    # MODEL_MAP 의 substring 매칭이 공백 필수라 "갤럭시S25" / "galaxy s 21" 같은
    # 변형이 누락된다. 정규식으로 \s* 옵션화하여 보강.
    # ═══════════════════════════════════════════════════════════════════
    # ── Galaxy S 영문 공백 옵션 (S26~S5) — galaxy 컨텍스트 필수 ──
    #   "Galaxy S 21" / "galaxy s 10 plus" 등 (S 와 디지트 사이 공백).
    (re.compile(r"\bgalaxy\s+s\s*26\s*ultra\b", re.IGNORECASE), "GS26U"),
    (re.compile(r"\bgalaxy\s+s\s*26\s*(?:plus|\+)", re.IGNORECASE), "GS26P"),
    (re.compile(r"\bgalaxy\s+s\s*26\b", re.IGNORECASE), "GS26"),
    (re.compile(r"\bgalaxy\s+s\s*25\s*ultra\b", re.IGNORECASE), "GS25U"),
    (re.compile(r"\bgalaxy\s+s\s*25\s*(?:plus|\+)", re.IGNORECASE), "GS25P"),
    (re.compile(r"\bgalaxy\s+s\s*25\b", re.IGNORECASE), "GS25"),
    (re.compile(r"\bgalaxy\s+s\s*24\s*ultra\b", re.IGNORECASE), "GS24U"),
    (re.compile(r"\bgalaxy\s+s\s*24\s*(?:plus|\+)", re.IGNORECASE), "GS24P"),
    (re.compile(r"\bgalaxy\s+s\s*24\s*fe\b", re.IGNORECASE), "GFE24"),
    (re.compile(r"\bgalaxy\s+s\s*24\b", re.IGNORECASE), "GS24"),
    (re.compile(r"\bgalaxy\s+s\s*23\s*ultra\b", re.IGNORECASE), "GS23U"),
    (re.compile(r"\bgalaxy\s+s\s*23\s*(?:plus|\+)", re.IGNORECASE), "GS23P"),
    (re.compile(r"\bgalaxy\s+s\s*23\s*fe\b", re.IGNORECASE), "GFE23"),
    (re.compile(r"\bgalaxy\s+s\s*23\b", re.IGNORECASE), "GS23"),
    (re.compile(r"\bgalaxy\s+s\s*22\s*ultra\b", re.IGNORECASE), "GS22U"),
    (re.compile(r"\bgalaxy\s+s\s*22\s*(?:plus|\+)", re.IGNORECASE), "GS22"),  # S22+ 카탈로그 없음 → 폴백
    (re.compile(r"\bgalaxy\s+s\s*22\b", re.IGNORECASE), "GS22"),
    (re.compile(r"\bgalaxy\s+s\s*21\s*ultra\b", re.IGNORECASE), "GS21U"),
    (re.compile(r"\bgalaxy\s+s\s*21\s*(?:plus|\+)", re.IGNORECASE), "GS21P"),
    (re.compile(r"\bgalaxy\s+s\s*21\s*fe\b", re.IGNORECASE), "GFE21"),
    (re.compile(r"\bgalaxy\s+s\s*21\b", re.IGNORECASE), "GS21"),
    (re.compile(r"\bgalaxy\s+s\s*20\s*ultra\b", re.IGNORECASE), "GS20U"),
    (re.compile(r"\bgalaxy\s+s\s*20\s*(?:plus|\+)", re.IGNORECASE), "GS20P"),
    (re.compile(r"\bgalaxy\s+s\s*20\s*fe\b", re.IGNORECASE), "GFE20"),
    (re.compile(r"\bgalaxy\s+s\s*20\b", re.IGNORECASE), "GS20"),
    (re.compile(r"\bgalaxy\s+s\s*10\s*(?:plus|\+)", re.IGNORECASE), "GS10P"),
    (re.compile(r"\bgalaxy\s+s\s*10\s*5g\b", re.IGNORECASE), "GS105G"),
    (re.compile(r"\bgalaxy\s+s\s*10\s*e\b", re.IGNORECASE), "GS10E"),
    (re.compile(r"\bgalaxy\s+s\s*10\b", re.IGNORECASE), "GS10"),
    (re.compile(r"\bgalaxy\s+s\s*9\s*(?:plus|\+)", re.IGNORECASE), "GS9P"),
    (re.compile(r"\bgalaxy\s+s\s*9\b", re.IGNORECASE), "GS9"),
    (re.compile(r"\bgalaxy\s+s\s*8\s*(?:plus|\+)", re.IGNORECASE), "GS8P"),
    (re.compile(r"\bgalaxy\s+s\s*8\b", re.IGNORECASE), "GS8"),
    (re.compile(r"\bgalaxy\s+s\s*7\s*edge\b", re.IGNORECASE), "GS7E"),
    (re.compile(r"\bgalaxy\s+s\s*7\b", re.IGNORECASE), "GS7"),
    (re.compile(r"\bgalaxy\s+s\s*6\s*edge\b", re.IGNORECASE), "GS6E"),
    (re.compile(r"\bgalaxy\s+s\s*6\b", re.IGNORECASE), "GS6"),
    (re.compile(r"\bgalaxy\s+s\s*5\b", re.IGNORECASE), "GS5"),
    (re.compile(r"\bgalaxy\s+s\s*4\b", re.IGNORECASE), "GS4"),
    (re.compile(r"\bgalaxy\s+s\s*3\b", re.IGNORECASE), "GS3"),
    (re.compile(r"\bgalaxy\s+s\s*2\b", re.IGNORECASE), "GS2"),
    # ── Galaxy Note 영문 공백 옵션 ──
    (re.compile(r"\bgalaxy\s+note\s*20\s*ultra\b", re.IGNORECASE), "GN20U"),
    (re.compile(r"\bgalaxy\s+note\s*20\b", re.IGNORECASE), "GN20"),
    (re.compile(r"\bgalaxy\s+note\s*10\s*(?:plus|\+)", re.IGNORECASE), "GN10P"),
    (re.compile(r"\bgalaxy\s+note\s*10\b", re.IGNORECASE), "GN10"),
    (re.compile(r"\bgalaxy\s+note\s*9\b", re.IGNORECASE), "GN9"),
    (re.compile(r"\bgalaxy\s+note\s*8\b", re.IGNORECASE), "GN8"),
    (re.compile(r"\bgalaxy\s+note\s*7\b", re.IGNORECASE), "GN7"),
    (re.compile(r"\bgalaxy\s+note\s*5\b", re.IGNORECASE), "GN5"),
    (re.compile(r"\bgalaxy\s+note\s*4\b", re.IGNORECASE), "GN4"),
    (re.compile(r"\bgalaxy\s+note\s*3\b", re.IGNORECASE), "GN3"),
    (re.compile(r"\bgalaxy\s+note\s*2\b", re.IGNORECASE), "GN2"),
    (re.compile(r"\bgalaxy\s+note\s*1\b", re.IGNORECASE), "GN1"),
    # ── 갤럭시 + S 시리즈 한국어 공백 옵션 (S26~S5) ──
    #   "갤럭시S25" / "갤럭시 s25" / "갤s25" / "갤럭시s25울트라" 등.
    #   '울트라' 한국어 접미 변형 (Ultra 영문은 위 영문 패턴이 처리).
    (re.compile(r"(?:갤럭시|갤)\s*s\s*26\s*(?:울트라|ultra)", re.IGNORECASE), "GS26U"),
    (re.compile(r"(?:갤럭시|갤)\s*s\s*26\s*(?:플러스|\+)", re.IGNORECASE), "GS26P"),
    (re.compile(r"(?:갤럭시|갤)\s*s\s*26\b", re.IGNORECASE), "GS26"),
    (re.compile(r"(?:갤럭시|갤)\s*s\s*25\s*(?:울트라|ultra)", re.IGNORECASE), "GS25U"),
    (re.compile(r"(?:갤럭시|갤)\s*s\s*25\s*(?:플러스|\+)", re.IGNORECASE), "GS25P"),
    (re.compile(r"(?:갤럭시|갤)\s*s\s*25\b", re.IGNORECASE), "GS25"),
    (re.compile(r"(?:갤럭시|갤)\s*s\s*24\s*(?:울트라|ultra)", re.IGNORECASE), "GS24U"),
    (re.compile(r"(?:갤럭시|갤)\s*s\s*24\s*(?:플러스|\+)", re.IGNORECASE), "GS24P"),
    (re.compile(r"(?:갤럭시|갤)\s*s\s*24\s*fe\b", re.IGNORECASE), "GFE24"),
    (re.compile(r"(?:갤럭시|갤)\s*s\s*24\b", re.IGNORECASE), "GS24"),
    (re.compile(r"(?:갤럭시|갤)\s*s\s*23\s*(?:울트라|ultra)", re.IGNORECASE), "GS23U"),
    (re.compile(r"(?:갤럭시|갤)\s*s\s*23\s*(?:플러스|\+)", re.IGNORECASE), "GS23P"),
    (re.compile(r"(?:갤럭시|갤)\s*s\s*23\s*fe\b", re.IGNORECASE), "GFE23"),
    (re.compile(r"(?:갤럭시|갤)\s*s\s*23\b", re.IGNORECASE), "GS23"),
    (re.compile(r"(?:갤럭시|갤)\s*s\s*22\s*(?:울트라|ultra)", re.IGNORECASE), "GS22U"),
    (re.compile(r"(?:갤럭시|갤)\s*s\s*22\b", re.IGNORECASE), "GS22"),
    (re.compile(r"(?:갤럭시|갤)\s*s\s*21\s*(?:울트라|ultra)", re.IGNORECASE), "GS21U"),
    (re.compile(r"(?:갤럭시|갤)\s*s\s*21\s*(?:플러스|\+)", re.IGNORECASE), "GS21P"),
    (re.compile(r"(?:갤럭시|갤)\s*s\s*21\b", re.IGNORECASE), "GS21"),
    (re.compile(r"(?:갤럭시|갤)\s*s\s*20\s*(?:울트라|ultra)", re.IGNORECASE), "GS20U"),
    (re.compile(r"(?:갤럭시|갤)\s*s\s*20\s*(?:플러스|\+)", re.IGNORECASE), "GS20P"),
    (re.compile(r"(?:갤럭시|갤)\s*s\s*20\b", re.IGNORECASE), "GS20"),
    (re.compile(r"(?:갤럭시|갤)\s*s\s*10\s*(?:플러스|\+)", re.IGNORECASE), "GS10P"),
    (re.compile(r"(?:갤럭시|갤)\s*s\s*10\s*e\b", re.IGNORECASE), "GS10E"),
    (re.compile(r"(?:갤럭시|갤)\s*s\s*10\b", re.IGNORECASE), "GS10"),
    (re.compile(r"(?:갤럭시|갤)\s*s\s*9\s*(?:플러스|\+)", re.IGNORECASE), "GS9P"),
    (re.compile(r"(?:갤럭시|갤)\s*s\s*9\b", re.IGNORECASE), "GS9"),
    (re.compile(r"(?:갤럭시|갤)\s*s\s*8\s*(?:플러스|\+)", re.IGNORECASE), "GS8P"),
    (re.compile(r"(?:갤럭시|갤)\s*s\s*8\b", re.IGNORECASE), "GS8"),
    (re.compile(r"(?:갤럭시|갤)\s*s\s*7\s*엣지", re.IGNORECASE), "GS7E"),
    (re.compile(r"(?:갤럭시|갤)\s*s\s*7\b", re.IGNORECASE), "GS7"),
    (re.compile(r"(?:갤럭시|갤)\s*s\s*6\s*엣지", re.IGNORECASE), "GS6E"),
    (re.compile(r"(?:갤럭시|갤)\s*s\s*6\b", re.IGNORECASE), "GS6"),
    (re.compile(r"(?:갤럭시|갤)\s*s\s*5\b", re.IGNORECASE), "GS5"),
    (re.compile(r"(?:갤럭시|갤)\s*s\s*4\b", re.IGNORECASE), "GS4"),
    (re.compile(r"(?:갤럭시|갤)\s*s\s*3\b", re.IGNORECASE), "GS3"),
    (re.compile(r"(?:갤럭시|갤)\s*s\s*2\b", re.IGNORECASE), "GS2"),
    # ── 갤럭시 + 노트 한국어 (Note 옛/신모델) ──
    #   "갤럭시 노트10" / "갤노트10" / "갤럭시노트10+" 등.
    (re.compile(r"(?:갤럭시|갤)\s*노트\s*20\s*울트라"), "GN20U"),
    (re.compile(r"(?:갤럭시|갤)\s*노트\s*20\b"), "GN20"),
    (re.compile(r"(?:갤럭시|갤)\s*노트\s*10\s*(?:플러스|\+)"), "GN10P"),
    (re.compile(r"(?:갤럭시|갤)\s*노트\s*10\s*라이트"), "GN10L"),
    (re.compile(r"(?:갤럭시|갤)\s*노트\s*10\b"), "GN10"),
    (re.compile(r"(?:갤럭시|갤)\s*노트\s*9\b"), "GN9"),
    (re.compile(r"(?:갤럭시|갤)\s*노트\s*8\b"), "GN8"),
    (re.compile(r"(?:갤럭시|갤)\s*노트\s*7\b"), "GN7"),
    (re.compile(r"(?:갤럭시|갤)\s*노트\s*5\b"), "GN5"),
    (re.compile(r"(?:갤럭시|갤)\s*노트\s*4\b"), "GN4"),
    (re.compile(r"(?:갤럭시|갤)\s*노트\s*3\b"), "GN3"),
    (re.compile(r"(?:갤럭시|갤)\s*노트\s*2\b"), "GN2"),
    (re.compile(r"(?:갤럭시|갤)\s*노트\s*1\b"), "GN1"),
    # ── 갤탭 / 갤럭시탭 옛 1세대 ──
    (re.compile(r"(?:갤럭시|갤)\s*탭\s*1\b"), "GTAB1"),
    # ── 갤럭시 + A 시리즈 한국어 공백 옵션 (위에 일부 있으나 더 넓게) ──
    (re.compile(r"(?:갤럭시|갤)\s*a\s*55\b", re.IGNORECASE), "GA55"),
    (re.compile(r"(?:갤럭시|갤)\s*a\s*54\b", re.IGNORECASE), "GA54"),
    (re.compile(r"(?:갤럭시|갤)\s*a\s*53\b", re.IGNORECASE), "GA53"),
    (re.compile(r"(?:갤럭시|갤)\s*a\s*52\b", re.IGNORECASE), "GA52"),
    (re.compile(r"(?:갤럭시|갤)\s*a\s*51\b", re.IGNORECASE), "GA51"),
    (re.compile(r"(?:갤럭시|갤)\s*a\s*34\b", re.IGNORECASE), "GA34"),
    (re.compile(r"(?:갤럭시|갤)\s*a\s*32\b", re.IGNORECASE), "GA32"),
    (re.compile(r"(?:갤럭시|갤)\s*a\s*25\b", re.IGNORECASE), "GA25"),
    (re.compile(r"(?:갤럭시|갤)\s*a\s*24\b", re.IGNORECASE), "GA24"),
    (re.compile(r"(?:갤럭시|갤)\s*a\s*23\b", re.IGNORECASE), "GA23"),
    (re.compile(r"(?:갤럭시|갤)\s*a\s*15\b", re.IGNORECASE), "GA15"),
    (re.compile(r"(?:갤럭시|갤)\s*a\s*14\b", re.IGNORECASE), "GA14"),
    (re.compile(r"(?:갤럭시|갤)\s*a\s*13\b", re.IGNORECASE), "GA13"),
    (re.compile(r"(?:갤럭시|갤)\s*a\s*12\b", re.IGNORECASE), "GA12"),

    # ── 베어 S-넘버 패턴 (단어경계 + 변형 우선) ────────────────────
    # "S25 Ultra" / "S25+" 등 변형은 substring MODEL_MAP 에 이미 있으므로,
    # 이 regex 는 일반 "S25" 같은 베어 케이스만 잡는다. 단어경계 필수.
    # 주의: '+' 뒤에는 \b 안 붙음 (non-word/non-word = boundary 미발생).
    (re.compile(r"\bs\s*26\s*ultra\b", re.IGNORECASE), "GS26U"),
    (re.compile(r"\bs\s*26\s*(?:plus|\+)", re.IGNORECASE), "GS26P"),
    (re.compile(r"\bs\s*26\b", re.IGNORECASE), "GS26"),
    (re.compile(r"\bs\s*25\s*ultra\b", re.IGNORECASE), "GS25U"),
    (re.compile(r"\bs\s*25\s*(?:plus|\+)", re.IGNORECASE), "GS25P"),
    (re.compile(r"\bs\s*25\b", re.IGNORECASE), "GS25"),
    (re.compile(r"\bs\s*24\s*ultra\b", re.IGNORECASE), "GS24U"),
    (re.compile(r"\bs\s*24\s*(?:plus|\+)", re.IGNORECASE), "GS24P"),
    (re.compile(r"\bs\s*24\s*fe\b", re.IGNORECASE), "GFE24"),
    (re.compile(r"\bs\s*24\b", re.IGNORECASE), "GS24"),
    (re.compile(r"\bs\s*23\s*ultra\b", re.IGNORECASE), "GS23U"),
    (re.compile(r"\bs\s*23\s*(?:plus|\+)", re.IGNORECASE), "GS23P"),
    (re.compile(r"\bs\s*23\s*fe\b", re.IGNORECASE), "GFE23"),
    (re.compile(r"\bs\s*23\b", re.IGNORECASE), "GS23"),
    (re.compile(r"\bs\s*22\s*ultra\b", re.IGNORECASE), "GS22U"),
    (re.compile(r"\bs\s*22\b", re.IGNORECASE), "GS22"),

    # ── 베어 A-넘버 패턴 (Harvest 5 V2 — GSMArena NULL 회수) ───────────
    # 'A57 is pure crazy', 'Get the A17 5g', 'A37 base variant' 형태로
    # Galaxy 컨텍스트 없이도 명백한 모델 토큰. 단어경계로 'a76 cores' /
    # 'a52s' 같은 ARM Cortex / 변형 SKU 와 충돌 회피.
    # 카탈로그 존재 모델만 포함 (A72/A76/A78 ARM IP 와 충돌하므로 제외).
    # 'galaxy a XX' 패턴은 위 R8 섹션에서 이미 매칭되므로 여기는 베어 폴백.
    # A50/A56/A11/A20/A22 등은 충돌 가능 (날짜·코드명·USPS 등)으로 제외.
    (re.compile(r"\ba\s*57\b", re.IGNORECASE), "GA57"),
    (re.compile(r"\ba\s*56\b", re.IGNORECASE), "GA56"),
    (re.compile(r"\ba\s*55\b", re.IGNORECASE), "GA55"),
    (re.compile(r"\ba\s*54\b", re.IGNORECASE), "GA54"),
    (re.compile(r"\ba\s*53\b", re.IGNORECASE), "GA53"),
    (re.compile(r"\ba\s*52\b", re.IGNORECASE), "GA52"),
    (re.compile(r"\ba\s*51\b", re.IGNORECASE), "GA51"),
    (re.compile(r"\ba\s*37\b", re.IGNORECASE), "GA37"),
    (re.compile(r"\ba\s*36\b", re.IGNORECASE), "GA36"),
    (re.compile(r"\ba\s*35\b", re.IGNORECASE), "GA35"),
    (re.compile(r"\ba\s*34\b", re.IGNORECASE), "GA34"),
    (re.compile(r"\ba\s*33\b", re.IGNORECASE), "GA33"),
    (re.compile(r"\ba\s*32\b", re.IGNORECASE), "GA32"),
    (re.compile(r"\ba\s*31\b", re.IGNORECASE), "GA31"),
    (re.compile(r"\ba\s*27\b", re.IGNORECASE), "GA27"),
    (re.compile(r"\ba\s*26\b", re.IGNORECASE), "GA26"),
    (re.compile(r"\ba\s*25\b", re.IGNORECASE), "GA25"),
    (re.compile(r"\ba\s*24\b", re.IGNORECASE), "GA24"),
    (re.compile(r"\ba\s*23\b", re.IGNORECASE), "GA23"),
    (re.compile(r"\ba\s*17\b", re.IGNORECASE), "GA17"),
    (re.compile(r"\ba\s*16\b", re.IGNORECASE), "GA16"),
    (re.compile(r"\ba\s*15\b", re.IGNORECASE), "GA15"),
    (re.compile(r"\ba\s*14\b", re.IGNORECASE), "GA14"),
    (re.compile(r"\ba\s*13\b", re.IGNORECASE), "GA13"),
    (re.compile(r"\ba\s*12\b", re.IGNORECASE), "GA12"),
    (re.compile(r"\ba\s*07\b", re.IGNORECASE), "GA07"),
    (re.compile(r"\ba\s*71\b", re.IGNORECASE), "GA71"),
    (re.compile(r"\ba\s*70\b", re.IGNORECASE), "GA70"),

    # ── Harvest 5 V3 — Hardware.fr (French) 옛 A 시리즈 / J 시리즈 ────────
    # 'Galaxy A6 Pas de réception des SMS' / 'Samsung J3 de 2016' 같이
    # 단일 자릿수 A 모델·J 모델 매핑. 카탈로그 최신 버전 (2018) 우선.
    # word boundary 로 'galaxy a60' 등 충돌 차단.
    (re.compile(r"\bgalaxy\s+a\s*9\s+pro\b", re.IGNORECASE), "GA9P_16"),
    (re.compile(r"\bgalaxy\s+a\s*9\b", re.IGNORECASE), "GA9_18"),
    (re.compile(r"\bgalaxy\s+a\s*8\b", re.IGNORECASE), "GA8_18"),
    (re.compile(r"\bgalaxy\s+a\s*7\b", re.IGNORECASE), "GA7_18"),
    (re.compile(r"\bgalaxy\s+a\s*6\b", re.IGNORECASE), "GA6_18"),
    (re.compile(r"\bgalaxy\s+a\s*5\b", re.IGNORECASE), "GA5_17"),
    (re.compile(r"\bgalaxy\s+a\s*3\b", re.IGNORECASE), "GA3_17"),
    # ── J 시리즈 (galaxy 또는 samsung 컨텍스트 + J숫자) ─────────────────
    # 'Samsung J3 de 2016' / 'galaxy j7 prime' 등.
    (re.compile(r"\b(?:galaxy|samsung)\s+j\s*7\s+prime\b", re.IGNORECASE), "GJ7PRM"),
    (re.compile(r"\b(?:galaxy|samsung)\s+j\s*7\s+pro\b", re.IGNORECASE), "GJ7PRO"),
    (re.compile(r"\b(?:galaxy|samsung)\s+j\s*7\s+max\b", re.IGNORECASE), "GJ7MAX"),
    (re.compile(r"\b(?:galaxy|samsung)\s+j\s*7\b", re.IGNORECASE), "GJ7"),
    (re.compile(r"\b(?:galaxy|samsung)\s+j\s*5\s+prime\b", re.IGNORECASE), "GJ5PRM"),
    (re.compile(r"\b(?:galaxy|samsung)\s+j\s*5\b", re.IGNORECASE), "GJ5"),
    (re.compile(r"\b(?:galaxy|samsung)\s+j\s*3\b", re.IGNORECASE), "GJ3_16"),
    (re.compile(r"\b(?:galaxy|samsung)\s+j\s*2\s+pro\b", re.IGNORECASE), "GJ2PRO"),
    (re.compile(r"\b(?:galaxy|samsung)\s+j\s*2\b", re.IGNORECASE), "GJ2"),
    (re.compile(r"\b(?:galaxy|samsung)\s+j\s*1\s+mini\b", re.IGNORECASE), "GJ1M"),
    (re.compile(r"\b(?:galaxy|samsung)\s+j\s*1\b", re.IGNORECASE), "GJ1"),
    (re.compile(r"\b(?:galaxy|samsung)\s+j\s*8\b", re.IGNORECASE), "GJ8"),
]


# ═══════════════════════════════════════════════════════════════════════
# 3) MODEL_MAP — 단순 substring 사전 (lowercase 정규화 후 매칭)
# ═══════════════════════════════════════════════════════════════════════
# 길이 내림차순 정렬되어 "note 10+" 가 "note 10" 보다 먼저 매칭됨.
MODEL_MAP: dict = {
    # ── Galaxy S22~S26 (R7 신규 — catalog 보유) ────────────────────
    "galaxy s26 ultra": "GS26U", "s26 ultra": "GS26U", "갤럭시 s26 울트라": "GS26U",
    "galaxy s26+": "GS26P", "s26 plus": "GS26P", "갤럭시 s26+": "GS26P",
    "galaxy s26": "GS26", "갤럭시 s26": "GS26", "갤s26": "GS26",
    "galaxy s25 ultra": "GS25U", "s25 ultra": "GS25U", "갤럭시 s25 울트라": "GS25U",
    "galaxy s25+": "GS25P", "s25 plus": "GS25P", "갤럭시 s25+": "GS25P", "s25+": "GS25P",
    "galaxy s25": "GS25", "갤럭시 s25": "GS25", "갤s25": "GS25",
    "galaxy s24 ultra": "GS24U", "s24 ultra": "GS24U", "갤럭시 s24 울트라": "GS24U",
    "galaxy s24+": "GS24P", "s24 plus": "GS24P", "갤럭시 s24+": "GS24P", "s24+": "GS24P",
    "galaxy s24 fe": "GFE24", "s24 fe": "GFE24",
    "galaxy s24": "GS24", "갤럭시 s24": "GS24", "갤s24": "GS24",
    "galaxy s23 ultra": "GS23U", "s23 ultra": "GS23U", "갤럭시 s23 울트라": "GS23U",
    "galaxy s23+": "GS23P", "s23 plus": "GS23P", "갤럭시 s23+": "GS23P", "s23+": "GS23P",
    "galaxy s23 fe": "GFE23", "s23 fe": "GFE23",
    "galaxy s23": "GS23", "갤럭시 s23": "GS23", "갤s23": "GS23",
    "galaxy s22 ultra": "GS22U", "s22 ultra": "GS22U", "갤럭시 s22 울트라": "GS22U",
    "galaxy s22": "GS22", "갤럭시 s22": "GS22", "갤s22": "GS22",
    # ── Galaxy S 옛 모델 (R6 유지) ────────────────────────────────
    "galaxy s10 5g": "GS105G", "s10 5g": "GS105G", "갤럭시 s10 5g": "GS105G",
    "galaxy s10+": "GS10P", "galaxy s10 plus": "GS10P", "s10 plus": "GS10P",
    "갤럭시 s10+": "GS10P", "갤s10+": "GS10P",
    "galaxy s10e": "GS10E", "s10e": "GS10E",
    "galaxy s10": "GS10", "갤럭시 s10": "GS10", "갤s10": "GS10",
    "galaxy s20 ultra": "GS20U", "s20 ultra": "GS20U", "갤럭시 s20 울트라": "GS20U",
    "galaxy s20+": "GS20P", "s20 plus": "GS20P", "갤럭시 s20+": "GS20P",
    "galaxy s20 fe": "GFE20", "s20 fe": "GFE20",
    "galaxy s20": "GS20", "갤럭시 s20": "GS20", "갤s20": "GS20",
    "galaxy s21 ultra": "GS21U", "s21 ultra": "GS21U", "갤럭시 s21 울트라": "GS21U",
    "galaxy s21+": "GS21P", "s21 plus": "GS21P", "갤럭시 s21+": "GS21P",
    "galaxy s21 fe": "GFE21", "s21 fe": "GFE21",
    "galaxy s21": "GS21", "갤럭시 s21": "GS21", "갤s21": "GS21",
    "galaxy s9+": "GS9P", "s9 plus": "GS9P", "갤럭시 s9+": "GS9P",
    "galaxy s9": "GS9", "갤럭시 s9": "GS9",
    "galaxy s8+": "GS8P", "s8 plus": "GS8P", "갤럭시 s8+": "GS8P",
    "galaxy s8": "GS8", "갤럭시 s8": "GS8",
    "galaxy s7 edge": "GS7E", "s7 edge": "GS7E", "갤럭시 s7 엣지": "GS7E",
    "galaxy s7": "GS7", "갤럭시 s7": "GS7",
    "galaxy s6 edge": "GS6E", "s6 edge": "GS6E", "갤럭시 s6 엣지": "GS6E",
    "galaxy s6": "GS6", "갤럭시 s6": "GS6",
    "galaxy s5": "GS5", "갤럭시 s5": "GS5",
    "galaxy s4": "GS4", "갤럭시 s4": "GS4",
    "galaxy s3": "GS3", "갤럭시 s3": "GS3",
    "galaxy s2": "GS2", "갤럭시 s2": "GS2",
    # ── Galaxy Note ───────────────────────────────────────────────
    "galaxy note 20 ultra": "GN20U", "note 20 ultra": "GN20U", "갤럭시 노트 20 울트라": "GN20U",
    "galaxy note20 ultra": "GN20U", "note20 ultra": "GN20U",
    "galaxy note 20": "GN20", "note 20": "GN20", "갤럭시 노트 20": "GN20",
    "galaxy note20": "GN20", "note20": "GN20",
    "galaxy note 10+": "GN10P", "note 10+": "GN10P", "note 10 plus": "GN10P",
    "갤럭시 노트 10+": "GN10P", "galaxy note10+": "GN10P", "note10+": "GN10P",
    "galaxy note 10": "GN10", "note 10": "GN10", "갤럭시 노트 10": "GN10",
    "galaxy note10": "GN10", "note10": "GN10",
    "galaxy note 9": "GN9", "note 9": "GN9", "갤럭시 노트 9": "GN9",
    "note9": "GN9",
    "galaxy note 8": "GN8", "note 8": "GN8", "갤럭시 노트 8": "GN8",
    "note8": "GN8",
    "galaxy note 7": "GN7", "note 7": "GN7", "갤럭시 노트 7": "GN7",
    "note7": "GN7", "노트7": "GN7", "갤노트7": "GN7",
    "galaxy note 5": "GN5", "note 5": "GN5", "갤럭시 노트 5": "GN5",
    "galaxy note 4": "GN4", "note 4": "GN4", "갤럭시 노트 4": "GN4",
    "galaxy note 3": "GN3", "note 3": "GN3", "갤럭시 노트 3": "GN3",
    "galaxy note 2": "GN2", "note 2": "GN2", "갤럭시 노트 2": "GN2",
    # ── Galaxy Z Fold 5~8 (R7 신규) ────────────────────────────────
    "galaxy z fold8": "GZF8", "z fold 8": "GZF8", "z fold8": "GZF8", "폴드8": "GZF8",
    "galaxy z fold7": "GZF7", "z fold 7": "GZF7", "z fold7": "GZF7",
    "갤럭시 z 폴드7": "GZF7", "폴드7": "GZF7",
    "galaxy z fold6": "GZF6", "z fold 6": "GZF6", "z fold6": "GZF6",
    "갤럭시 z 폴드6": "GZF6", "폴드6": "GZF6",
    "galaxy z fold5": "GZF5", "z fold 5": "GZF5", "z fold5": "GZF5",
    "갤럭시 z 폴드5": "GZF5", "폴드5": "GZF5",
    # Z Fold 옛 (1~4)
    "galaxy z fold4": "GZF4", "galaxy z fold 4": "GZF4", "z fold 4": "GZF4",
    "z fold4": "GZF4", "갤럭시 z 폴드4": "GZF4", "폴드4": "GZF4",
    "galaxy z fold3": "GZF3", "galaxy z fold 3": "GZF3", "z fold 3": "GZF3",
    "z fold3": "GZF3", "갤럭시 z 폴드3": "GZF3", "폴드3": "GZF3",
    "galaxy z fold2": "GZF2", "galaxy z fold 2": "GZF2", "z fold 2": "GZF2",
    "z fold2": "GZF2", "갤럭시 z 폴드2": "GZF2", "폴드2": "GZF2",
    "galaxy fold": "GZF1", "갤럭시 폴드": "GZF1",
    # ── Galaxy Z Flip 5~8 (R7 신규) ────────────────────────────────
    "galaxy z flip8": "GZFL8", "z flip 8": "GZFL8", "z flip8": "GZFL8", "플립8": "GZFL8",
    "galaxy z flip7": "GZFL7", "z flip 7": "GZFL7", "z flip7": "GZFL7",
    "갤럭시 z 플립7": "GZFL7", "플립7": "GZFL7",
    "galaxy z flip6": "GZFL6", "z flip 6": "GZFL6", "z flip6": "GZFL6",
    "갤럭시 z 플립6": "GZFL6", "플립6": "GZFL6",
    "galaxy z flip5": "GZFL5", "z flip 5": "GZFL5", "z flip5": "GZFL5",
    "갤럭시 z 플립5": "GZFL5", "플립5": "GZFL5",
    # Z Flip 옛 (1~4)
    "galaxy z flip4": "GZFL4", "galaxy z flip 4": "GZFL4", "z flip 4": "GZFL4",
    "z flip4": "GZFL4", "갤럭시 z 플립4": "GZFL4", "플립4": "GZFL4",
    "galaxy z flip3": "GZFL3", "galaxy z flip 3": "GZFL3", "z flip 3": "GZFL3",
    "z flip3": "GZFL3", "갤럭시 z 플립3": "GZFL3", "플립3": "GZFL3",
    "galaxy z flip": "GZFL1", "z flip": "GZFL1", "갤럭시 z 플립": "GZFL1",
    # ── Galaxy Watch 6~8 + Ultra (R7 신규) ─────────────────────────
    "galaxy watch ultra": "GWU", "watch ultra": "GWU", "갤럭시 워치 울트라": "GWU",
    "갤워치 울트라": "GWU",
    "galaxy watch8": "GW8", "galaxy watch 8": "GW8", "watch8": "GW8",
    "갤럭시 워치8": "GW8", "워치8": "GW8", "갤워치8": "GW8",
    "galaxy watch7": "GW7", "galaxy watch 7": "GW7", "watch7": "GW7",
    "갤럭시 워치7": "GW7", "워치7": "GW7", "갤워치7": "GW7",
    "galaxy watch6": "GW6", "galaxy watch 6": "GW6", "watch6": "GW6",
    "갤럭시 워치6": "GW6", "워치6": "GW6", "갤워치6": "GW6",
    # Watch 옛
    "galaxy watch5 pro": "GW5P", "galaxy watch 5 pro": "GW5P", "watch5 pro": "GW5P",
    "갤럭시 워치5 프로": "GW5P",
    "galaxy watch5": "GW5", "galaxy watch 5": "GW5", "watch5": "GW5",
    "갤럭시 워치5": "GW5", "워치5": "GW5",
    "galaxy watch4": "GW4", "galaxy watch 4": "GW4", "watch4": "GW4",
    "갤럭시 워치4": "GW4", "워치4": "GW4",
    "galaxy watch3": "GW3", "galaxy watch 3": "GW3", "watch3": "GW3",
    "갤럭시 워치3": "GW3",
    "galaxy watch active2": "GWA2", "watch active2": "GWA2", "active2": "GWA2",
    "galaxy watch active": "GWA", "watch active": "GWA",
    # ── Galaxy Buds 2/3/4 (R7 신규) ────────────────────────────────
    "galaxy buds4 pro": "GB4P", "buds4 pro": "GB4P", "갤럭시 버즈4 프로": "GB4P",
    "galaxy buds4": "GB4", "buds4": "GB4", "갤럭시 버즈4": "GB4", "버즈4": "GB4",
    "galaxy buds3 pro": "GB3P", "buds3 pro": "GB3P", "갤럭시 버즈3 프로": "GB3P",
    "버즈3 프로": "GB3P", "버즈3프로": "GB3P",
    "galaxy buds3": "GB3", "buds3": "GB3", "갤럭시 버즈3": "GB3", "버즈3": "GB3",
    "galaxy buds2 pro": "GB2P", "buds2 pro": "GB2P", "갤럭시 버즈2 프로": "GB2P",
    "버즈2 프로": "GB2P", "버즈2프로": "GB2P",
    "galaxy buds2": "GB2", "buds2": "GB2", "갤럭시 버즈2": "GB2", "버즈2": "GB2",
    # Galaxy Buds 1 (R6 유지)
    "galaxy buds pro": "GBP", "buds pro": "GBP", "갤럭시 버즈 프로": "GBP",
    "galaxy buds live": "GBL", "buds live": "GBL", "갤럭시 버즈 라이브": "GBL",
    "galaxy buds": "GB1", "갤럭시 버즈": "GB1",
    # ── iPhone 11~16 (R7 확장) ─────────────────────────────────────
    # 16 family
    "iphone 16 pro max": "AP16PM", "아이폰 16 프로 맥스": "AP16PM",
    "iphone 16 pro": "AP16P", "아이폰 16 프로": "AP16P",
    "iphone 16": "AP16", "아이폰 16": "AP16", "아이폰16": "AP16",
    # 15 family
    "iphone 15 pro max": "AP15PM", "아이폰 15 프로 맥스": "AP15PM",
    "iphone 15 pro": "AP15P", "아이폰 15 프로": "AP15P",
    "iphone 15": "AP15", "아이폰 15": "AP15", "아이폰15": "AP15",
    # 14
    "iphone 14": "AP14", "아이폰 14": "AP14", "아이폰14": "AP14",
    # 11~13
    "iphone 13": "AP13", "아이폰 13": "AP13", "아이폰13": "AP13",
    "iphone 12": "AP12", "아이폰 12": "AP12", "아이폰12": "AP12",
    "iphone 11": "AP11", "아이폰 11": "AP11", "아이폰11": "AP11",
    # iPhone X / 8 / 7 / 6 (R7 신규)
    "iphone x": "AP10", "아이폰 x": "AP10", "아이폰x": "AP10",
    "iphone 8 plus": "AP8", "iphone 8+": "AP8", "iphone 8": "AP8", "아이폰 8": "AP8",
    "iphone 7 plus": "AP7", "iphone 7+": "AP7", "iphone 7": "AP7", "아이폰 7": "AP7",
    "iphone 6 plus": "AP6", "iphone 6+": "AP6", "iphone 6": "AP6", "아이폰 6": "AP6",
    # ── Pixel 1~9 (R7 확장) ────────────────────────────────────────
    "pixel 9 pro": "PX9P", "픽셀 9 프로": "PX9P", "google pixel 9 pro": "PX9P",
    "pixel 9": "PX9", "픽셀 9": "PX9", "google pixel 9": "PX9",
    "pixel 8 pro": "PX8P", "픽셀 8 프로": "PX8P", "google pixel 8 pro": "PX8P",
    "pixel 8": "PX8", "픽셀 8": "PX8", "google pixel 8": "PX8",
    "pixel 7": "PX7", "픽셀 7": "PX7", "google pixel 7": "PX7",
    "pixel 6": "PX6", "픽셀 6": "PX6", "google pixel 6": "PX6",
    "pixel 5": "PX5", "픽셀 5": "PX5", "google pixel 5": "PX5",
    "pixel 4": "PX4", "픽셀 4": "PX4", "google pixel 4": "PX4",
    "pixel 3": "PX3", "픽셀 3": "PX3", "google pixel 3": "PX3",
    "pixel 2": "PX2", "픽셀 2": "PX2", "google pixel 2": "PX2",
    # ── Galaxy A 시리즈 보강 (R6 유지) ─────────────────────────────
    "galaxy a56": "GA56", "갤럭시 a56": "GA56",
    "galaxy a55": "GA55", "갤럭시 a55": "GA55",
    "galaxy a54": "GA54", "갤럭시 a54": "GA54",
    "galaxy a53": "GA53", "갤럭시 a53": "GA53",
    "galaxy a52": "GA52", "갤럭시 a52": "GA52",
    "galaxy a51": "GA51", "갤럭시 a51": "GA51",
    "galaxy a50": "GA50", "갤럭시 a50": "GA50",
    # ── R8 한국어 / Galaxy 컨텍스트 단순 substring ──
    "갤럭시 a35": "GA35", "갤럭시 a33": "GA33", "갤럭시 a25": "GA25",
    "갤럭시 a24": "GA24", "갤럭시 a23": "GA23", "갤럭시 a22": "GA22",
    "갤럭시 a13": "GA13", "갤럭시 a12": "GA12", "갤럭시 a11": "GA11",
    "갤럭시 a10": "GA10", "갤럭시 a20": "GA20", "갤럭시 a30": "GA30",
    "갤럭시 a40": "GA40", "갤럭시 a70": "GA70", "갤럭시 a71": "GA71",
    "갤럭시 a72": "GA72", "갤럭시 a80": "GA80", "갤럭시 a90": "GA90",
    "갤럭시 a01": "GA01", "갤럭시 a02": "GA02", "갤럭시 a03": "GA03",
    "갤럭시 j7 프로": "GJ7PRO", "갤럭시 j7 프라임": "GJ7PRM",
    "갤럭시 j5 프라임": "GJ5PRM", "갤럭시 j7": "GJ7", "갤럭시 j5": "GJ5",
    "갤럭시 j3": "GJ3_16", "갤럭시 j2": "GJ2", "갤럭시 j1": "GJ1",
    "갤럭시 j8": "GJ8",
    "갤럭시 m55": "GM55", "갤럭시 m54": "GM54", "갤럭시 m53": "GM53",
    "갤럭시 m52": "GM52", "갤럭시 m51": "GM51", "갤럭시 m42": "GM42",
    "갤럭시 m34": "GM34", "갤럭시 m33": "GM33", "갤럭시 m32": "GM32",
    "갤럭시 m31": "GM31", "갤럭시 m30": "GM30", "갤럭시 m22": "GM22",
    "갤럭시 m21": "GM21", "갤럭시 m14": "GM14", "갤럭시 m13": "GM13",
    "갤럭시 m12": "GM12", "갤럭시 m11": "GM11", "갤럭시 m10": "GM10",
    "갤럭시 f55": "GF55", "갤럭시 f54": "GF54", "갤럭시 f52": "GF52",
    "갤럭시 f42": "GF42", "갤럭시 f41": "GF41", "갤럭시 f22": "GF22",
    "갤럭시 f12": "GF12", "갤럭시 f62": "GF62",
    # ── Watch / Gear 한국어 ──
    "갤럭시 워치 fe": "GWFE", "갤럭시 워치 액티브3": "GWA3",
    "갤럭시 워치8 클래식": "GW8C", "갤럭시 워치6 클래식": "GW6C",
    "갤럭시 워치4 클래식": "GW4C",
    "갤럭시 기어 2": "GGEAR2", "갤럭시 기어": "GGEAR1",
    "삼성 기어 s3": "GGS3", "삼성 기어 s2": "GGS2", "삼성 기어 s": "GGS",
    "기어 스포트": "GGSPORT", "기어 핏 2": "GGFIT2", "기어 핏": "GGEARFIT",
    "기어 아이콘x 2018": "GICX2", "기어 아이콘x": "GICX",
    "갤럭시 핏3": "GFIT3", "갤럭시 핏2": "GFIT2", "갤럭시 핏 e": "GFITE",
    "갤럭시 핏": "GFIT",
    # ── Buds / Ring 한국어 ──
    "갤럭시 버즈+": "GBPLUS", "갤럭시 버즈 플러스": "GBPLUS",
    "갤럭시 버즈 fe": "GBFE",
    "갤럭시 링": "GR1",
    # ── 옛 폰 한국어 ──
    "갤럭시 노트 엣지": "GNEDGE", "갤럭시 노트 fe": "GNFE",
    "갤럭시 노트 10 라이트": "GN10L", "갤럭시 s10 라이트": "GS10L",
    "갤럭시 s8 액티브": "GS8A", "갤럭시 s9 액티브": "GS9A",
    "갤럭시 s6 엣지+": "GS6EP", "갤럭시 s5 미니": "GS5MINI",
    "갤럭시 s4 미니": "GS4MINI", "갤럭시 s3 미니": "GS3MINI",
    "갤럭시 그랜드 프라임+": "GGRPRMP", "갤럭시 그랜드 프라임": "GGRPRM",
    "갤럭시 그랜드 2": "GGRAND2", "갤럭시 그랜드": "GGRAND",
    "갤럭시 코어 프라임": "GCOREPRM", "갤럭시 코어 2": "GCORE2",
    "갤럭시 코어": "GCORE", "갤럭시 메가 6.3": "GMEGA63",
    "갤럭시 메가 5.8": "GMEGA58", "갤럭시 빔": "GBEAM",
    "갤럭시 에이스 4": "GACE4", "갤럭시 에이스 3": "GACE3",
    "갤럭시 에이스 2": "GACE2", "갤럭시 에이스": "GACE",
    "갤럭시 온7": "GON7", "갤럭시 온5": "GON5",
    "갤럭시 포켓 2": "GPOCKET2", "갤럭시 포켓": "GPOCKET",
    # 주의(Track B): "갤럭시 미니/스타/트렌드" substring 은 "미니멀/스타터팩/트렌디"
    # 같은 일반어와 충돌하므로 substring MAP 에서는 빼고, 위 MODEL_REGEX_PATTERNS 의
    # negative-lookahead 정규식으로만 매칭한다.
    "갤럭시 윈 프로": "GWINPRO", "갤럭시 윈": "GWIN",
    "갤럭시 페임": "GFAME", "갤럭시 뮤직": "GMUSIC",
    "갤럭시 익스프레스 2": "GEXPR2", "갤럭시 익스프레스": "GEXPRESS",
    "갤럭시 폴드 5g": "GZF1_5G", "갤럭시 z 플립 5g": "GZFL1_5G",
    # ── Tab 구형 한국어 ──
    "갤럭시 탭 s 10.5": "GTS_105", "갤럭시 탭 s 8.4": "GTS_84",
    "갤럭시 탭 s5e": "GTS5E", "갤럭시 탭 s6 lite": "GTS6L",
    "갤럭시 탭 s7 fe": "GTS7F", "갤럭시 탭 s11+": "GTS11P",
    "갤럭시 탭 s4": "GTS4", "갤럭시 탭 s3": "GTS3", "갤럭시 탭 s2": "GTS2",
    "갤럭시 탭 프로 12.2": "GTP_122", "갤럭시 탭 프로 10.1": "GTP_101",
    "갤럭시 탭 프로 8.4": "GTP_84",
    "갤럭시 탭 a7 lite": "GTA7L", "갤럭시 탭 a7": "GTA7",
    "갤럭시 탭 a 9.7": "GTA97",
    "갤럭시 탭 액티브 4 pro": "GTACT4P", "갤럭시 탭 액티브 3": "GTACT3",
    "갤럭시 탭 액티브 2": "GTACT2", "갤럭시 탭 액티브": "GTACT1",
    "갤럭시 엑스커버4s": "GXC4S", "갤럭시 엑스커버3": "GXC3",
    "갤럭시 엑스커버2": "GXC2", "갤럭시 엑스커버 프로": "GXCPRO",
    "갤럭시 엑스커버": "GXC1",
}

# 길이 내림차순 — 긴 키 (예: "note 20 ultra") 가 짧은 키 ("note 20") 보다 우선.
_SORTED_KEYS = sorted(MODEL_MAP.keys(), key=len, reverse=True)

# 공백 정규화 — 모든 whitespace 를 단일 공백으로.
_WS_RE = re.compile(r"\s+")


def normalize(text: str) -> str:
    """소문자 + 단일 공백으로 정규화."""
    return _WS_RE.sub(" ", text.lower()).strip()


def _has_galaxy_context(norm: str) -> bool:
    """정규화된 텍스트가 galaxy/samsung/갤럭시/삼성 을 포함하는가."""
    return bool(_GALAXY_CTX_RE.search(norm))


def _resolve_wide_jump(norm: str) -> Optional[str]:
    """'와이드 N' / '점프 N' 한국어 패턴 — 그룹 캡처로 series_code 결정."""
    m = re.search(r"갤(?:럭시)?\s*와이드\s*([2-8])", norm)
    if m:
        return f"GWIDE{m.group(1)}"
    m = re.search(r"(?<![a-z])와이드\s*([2-8])\b", norm)
    if m:
        return f"GWIDE{m.group(1)}"
    m = re.search(r"(?:갤(?:럭시)?\s*)?점프\s*([1-4])", norm)
    if m:
        return f"GJUMP{m.group(1)}"
    return None


def match_product_code(text: str) -> Optional[str]:
    """텍스트에서 첫 매칭되는 모델의 products.code 반환. 없으면 None.

    매칭 우선순위:
      1. NOISE_PATTERNS — 갤럭시 컨텍스트 없이 노이즈 매칭 시 즉시 None.
      2. MODEL_REGEX_PATTERNS — 컨텍스트 의존 패턴 (Tab, SM-코드, A 시리즈 등).
      3. Wide/Jump 그룹 캡처 핸들러 (한국어 한정).
      4. MODEL_MAP — 길이 내림차순 substring 매칭.
    """
    if not text:
        return None
    norm = normalize(text)

    # 1) Noise gate — 갤럭시 컨텍스트 없이 noise pattern 만 매칭되면 즉시 None
    has_galaxy = _has_galaxy_context(norm)
    for pat, _name in NOISE_PATTERNS:
        if pat.search(norm) and not has_galaxy:
            return None

    # 1b) Noise mask — YC batch / notebook / app store note 등 토큰 부분 마스킹
    norm = _mask_noise(norm)

    # 2) Wide/Jump 그룹 캡처 — 한국 통신사 전용 모델 (베어 A-넘버보다 우선).
    #    Harvest 5 V2: 베어 'a33' 같은 GSMArena 회수 패턴이 추가되면서
    #    '점프2 A33' 같은 한국어 문장에서 GJUMP2 가 가려지는 회귀를 방지.
    wj = _resolve_wide_jump(norm)
    if wj:
        return wj

    # 3) Regex 패턴 — 선언 순서대로
    for pat, code in MODEL_REGEX_PATTERNS:
        if code is None:
            continue
        if pat.search(norm):
            return code

    # 4) MODEL_MAP substring 매칭 (길이 내림차순)
    for key in _SORTED_KEYS:
        if key in norm:
            return MODEL_MAP[key]

    return None


SELECT_SQL = text("""
    SELECT id, content_translated, content_original
    FROM voc_records
    WHERE product_id IS NULL
      AND content_original IS NOT NULL
      AND id < :cursor
    ORDER BY id DESC
    LIMIT :batch
""")

UPDATE_SQL = text("""
    UPDATE voc_records
    SET product_id = :pid
    WHERE id = :id
""")


async def load_code_to_id(db: AsyncSession) -> dict:
    """products 테이블에서 code → id 매핑 로드."""
    rows = (await db.execute(text("SELECT id, code FROM products"))).all()
    return {r.code: r.id for r in rows}


async def main() -> None:
    if not DATABASE_URL:
        log.error("DATABASE_URL 미설정")
        sys.exit(2)

    engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)
    Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with Session() as db:
        code_to_id = await load_code_to_id(db)
        total = (await db.execute(text("""
            SELECT count(*) FROM voc_records
            WHERE product_id IS NULL AND content_original IS NOT NULL
        """))).scalar_one()

    log.info(
        f"relink 대상: {total:,}건 (LIMIT={LIMIT or '무제한'}, BATCH={BATCH}, "
        f"DRY_RUN={DRY_RUN}, 등록 code={len(code_to_id)})"
    )

    seen = matched = unknown_code = 0
    code_hits: Counter = Counter()
    cursor = 1 << 62

    while True:
        async with Session() as db:
            rows = (await db.execute(SELECT_SQL, {"batch": BATCH, "cursor": cursor})).all()
            if not rows:
                log.info("  더 이상 처리할 NULL 행 없음 — 종료")
                break

            ups = []
            for r in rows:
                seen += 1
                txt = r.content_translated or r.content_original or ""
                code = match_product_code(txt)
                if not code:
                    continue
                pid = code_to_id.get(code)
                if not pid:
                    unknown_code += 1
                    continue
                ups.append({"id": r.id, "pid": pid})
                code_hits[code] += 1
                matched += 1

            if ups and not DRY_RUN:
                await db.execute(UPDATE_SQL, ups)
                await db.commit()

            cursor = rows[-1].id

            log.info(
                f"  진행 누적 {seen:,} / 매치 {matched:,} / unknown_code {unknown_code} "
                f"(이번 배치 UPDATE={len(ups)}, cursor={cursor})"
            )

        if LIMIT and seen >= LIMIT:
            log.info(f"LIMIT {LIMIT:,} 도달 — 종료")
            break

    await engine.dispose()
    hit_pct = matched * 100.0 / max(seen, 1)
    log.info(f"=== relink 완료: 시도 {seen:,} / 매치 {matched:,} / hit {hit_pct:.2f}% ===")
    log.info("  상위 매칭 code:")
    for code, n in code_hits.most_common(25):
        log.info(f"    {code:10s} {n:6,}")


if __name__ == "__main__":
    asyncio.run(main())
