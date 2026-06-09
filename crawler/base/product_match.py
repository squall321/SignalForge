"""
제품 코드 자동 추론 — 게시물 본문/제목에서 어떤 제품에 관한 글인지 추론.

DB products 테이블 시드 코드와 1:1 정합. 다음 카테고리를 커버한다:
  · Galaxy 2025-26 (S25/S26/Z7/Z8/A/Watch8/Buds3-4/Ring2)
  · Galaxy 구세대 (S24/S23/S22/Z6/Z5/Watch7/Buds2)  — 시기별 비교용
  · 경쟁사 (iPhone, Pixel)                            — 시장 비교용

규칙: 더 구체적인 변형(Ultra/Plus/Pro/FE)을 먼저 평가, 첫 매치 채택.
한국어 커뮤니티(dcinside/clien/ppomppu) 변형 별칭 다수 포함.

한국어 처리 주의: Python re.\\b 는 한글 직전/직후를 word boundary 로 인정하지
않는다. 따라서 "S25인데" 같은 조사 결합형 매칭을 위해 generic 패턴은 끝의 \\b
대신 부정형 lookahead (?![0-9a-zA-Z]) 를 사용한다 — 숫자/영문 미연속만 차단,
한글/공백/구두점은 허용.
"""
import re
from typing import List, Optional, Tuple

# 한글 friendly 경계: 다음 글자가 숫자/영문이 아닐 때만 매칭 (한글·공백·구두점 OK)
_E = r"(?![0-9a-zA-Z])"

PRODUCT_PATTERNS: List[Tuple[str, List[str]]] = [
    # ═══════════ Galaxy 2026 라인업 (루머/얼리 액세스) ═══════════
    # ── Galaxy S26 (Ultra → Plus → 기본 순서) ──
    ("GS26U", [r"\bs26\s*ultra" + _E, r"galaxy\s*s26\s*ultra", r"\bs26u" + _E,
               r"s26\s*울트라", r"s26울트라", r"갤럭시\s*s26\s*울트라",
               r"\bs26\s*울" + _E, r"s26울" + _E, r"26\s*울트라", r"26울트라",
               r"\b26울" + _E, r"26울라리", r"s26\s*울라", r"s26울라",
               r"\bs26\s*ul" + _E,
               # 한국어 변형 보강: 26울모/26울x (붙임 직후 한글), 26울라(리)
               r"26울라" + _E, r"갤26울", r"\b26울모" + _E,
               r"\b26\s*울라리", r"\b26\s*울라" + _E]),
    ("GS26P", [r"\bs26\s*\+", r"\bs26\s*plus" + _E, r"galaxy\s*s26\s*\+",
               r"s26\s*플러스", r"s26플러스", r"\bs26\s*플" + _E, r"s26플" + _E,
               r"갤s26\s*\+", r"갤s26\s*플",
               # 보강: 26 플(러스), 26플, 26+, s26플러스, s26 플
               r"\b26\s*플러스" + _E, r"\b26플러스" + _E, r"\b26\s*플" + _E,
               r"\b26플" + _E, r"\b26\s*\+"]),
    ("GS26",  [r"galaxy\s*s26" + _E, r"\bs26" + _E, r"갤럭시\s*s26" + _E,
               r"갤s26" + _E,
               r"s26\s*일반", r"s26\s*노말", r"s26\s*노멀",
               r"s26\s*시리즈", r"s26시리즈",
               r"s26\s*기본", r"s26기본형", r"s26\s*기본형",
               # 보강: 숫자 단독 26 + (시리즈/기본/노멀)
               r"\b26\s*시리즈" + _E, r"\b26시리즈" + _E,
               r"\b26\s*노멀" + _E, r"\b26노멀" + _E,
               r"\b26\s*기본" + _E, r"\b26기본" + _E,
               r"\b26\s*일반" + _E]),

    # ═══════════ Galaxy 2025 라인업 ═══════════
    # ── Galaxy S25 (Ultra → Plus → FE → 기본 순서) ──
    ("GS25U", [r"\bs25\s*ultra" + _E, r"galaxy\s*s25\s*ultra", r"\bs25u" + _E,
               r"s25\s*울트라", r"s25울트라", r"갤럭시\s*s25\s*울트라",
               r"\bs25\s*울" + _E, r"s25울" + _E, r"25\s*울트라", r"25울트라",
               r"\b25울" + _E, r"갤s25\s*울", r"s25\s*ul" + _E,
               # 보강: 25울모/25울려/갤25울
               r"갤25울", r"\b25울모" + _E]),
    ("GS25P", [r"\bs25\s*\+", r"\bs25\s*plus" + _E, r"galaxy\s*s25\s*\+",
               r"s25\s*플러스", r"s25플러스", r"\bs25\s*플" + _E, r"s25플" + _E,
               r"갤s25\s*\+", r"갤s25\s*플", r"25\s*플러스", r"갤25\s*\+",
               # 보강: 25플(러스) 단독 숫자형
               r"\b25\s*플러스" + _E, r"\b25플러스" + _E, r"\b25\s*플" + _E,
               r"\b25플" + _E, r"\b25\s*\+"]),
    ("GFE25", [r"\bfe\s*25" + _E, r"\bfe25" + _E, r"s25\s*fe" + _E,
               r"galaxy\s*fe\s*25", r"갤럭시\s*fe\s*25", r"s25fe" + _E,
               # 보강: 25fe, 갤럭시 fe 25
               r"\b25\s*fe" + _E, r"\b25fe" + _E]),
    ("GS25",  [r"galaxy\s*s25" + _E, r"\bs25" + _E, r"갤럭시\s*s25" + _E,
               r"갤s25" + _E, r"갤25" + _E,
               # 보강: s25엣지(Edge 별도 SKU 없으므로 GS25로 흡수), s25 일반/노멀/기본
               r"s25엣지" + _E, r"s25\s*엣지" + _E, r"s25edge" + _E, r"s25\s*edge" + _E,
               r"s25\s*일반", r"s25\s*노멀", r"s25\s*노말",
               r"s25\s*기본", r"s25\s*시리즈", r"s25시리즈"]),

    # ── Galaxy Z 8세대 (루머/2026 출시 예정) ──
    ("GZF8",  [r"\bz\s*fold\s*8" + _E, r"\bfold\s*8" + _E, r"\bzfold8" + _E,
               r"z\s*폴드\s*8", r"폴드8", r"폴드\s*8" + _E, r"\b폴8" + _E,
               r"갤럭시\s*z\s*폴드\s*8", r"폴드\s*와이드", r"폴드와이드",
               r"와이드\s*폴드", r"와이드폴드"]),
    ("GZFL8", [r"\bz\s*flip\s*8" + _E, r"\bflip\s*8" + _E, r"\bzflip8" + _E,
               r"z\s*플립\s*8", r"플립8", r"플립\s*8" + _E, r"\b플8" + _E]),

    # ── Galaxy Z 7세대 ──
    ("GZF7",  [r"\bz\s*fold\s*7" + _E, r"\bfold\s*7" + _E, r"\bzfold7" + _E,
               r"z\s*폴드\s*7", r"폴드7", r"폴드\s*7" + _E, r"\b폴7" + _E,
               r"갤럭시\s*z\s*폴드\s*7"]),
    ("GZFL7", [r"\bz\s*flip\s*7" + _E, r"\bflip\s*7" + _E, r"\bzflip7" + _E,
               r"z\s*플립\s*7", r"플립7", r"플립\s*7" + _E, r"\b플7" + _E,
               r"갤럭시\s*z\s*플립\s*7"]),

    # ── Galaxy A ──
    ("GA56",  [r"galaxy\s*a56" + _E, r"\ba56" + _E, r"갤럭시\s*a56"]),

    # ── Galaxy Watch (Ultra → 8) ──
    ("GWU",   [r"watch\s*ultra", r"galaxy\s*watch\s*ultra", r"워치\s*울트라",
               r"워치울트라", r"\b워치울" + _E]),
    ("GW8",   [r"\bwatch\s*8" + _E, r"\bwatch8" + _E, r"galaxy\s*watch\s*8",
               r"워치\s*8" + _E, r"워치8", r"갤워치\s*8", r"갤워치8"]),

    # ── Galaxy Buds 4 (루머/얼리) ──
    ("GB4P",  [r"buds\s*4\s*pro", r"buds4\s*pro", r"버즈\s*4\s*프로",
               r"버즈4\s*프로", r"버즈4프로", r"버즈프로\s*4", r"버즈프로4",
               r"\b버4프" + _E, r"버4\s*프로", r"버4프로"]),
    ("GB4",   [r"\bbuds\s*4" + _E, r"\bbuds4" + _E, r"galaxy\s*buds\s*4",
               r"버즈\s*4" + _E, r"버즈4" + _E, r"갤버즈4", r"갤\s*버즈\s*4",
               r"\b버4" + _E]),

    # ── Galaxy Buds 3 ──
    ("GB3P",  [r"buds\s*3\s*pro", r"buds3\s*pro", r"버즈\s*3\s*프로",
               r"버즈3\s*프로", r"버즈3프로", r"버즈프로\s*3", r"버즈프로3",
               r"\b버3프" + _E, r"\bb3p" + _E]),
    ("GB3",   [r"\bbuds\s*3" + _E, r"\bbuds3" + _E, r"galaxy\s*buds\s*3",
               r"버즈\s*3" + _E, r"버즈3", r"갤버즈3", r"\b버3" + _E]),

    # ── Galaxy Ring ──
    ("GR2",   [r"galaxy\s*ring\s*2", r"\bring\s*2" + _E, r"\bring2" + _E,
               r"갤럭시\s*링\s*2", r"링2",
               # 단독 "Galaxy Ring"도 GR2 로 추정 (1세대 별도 추적 안 함)
               r"galaxy\s*ring" + _E, r"갤럭시\s*링" + _E]),

    # ═══════════ Galaxy 구세대 (비교용) ═══════════
    # ── S24 ──
    ("GS24U", [r"\bs24\s*ultra" + _E, r"galaxy\s*s24\s*ultra", r"\bs24u" + _E,
               r"s24\s*울트라", r"s24울트라", r"\bs24\s*울" + _E, r"s24울" + _E,
               r"\b24울" + _E, r"24울트라",
               # 보강: 갤24울/24u
               r"갤24울", r"\b24u" + _E]),
    ("GS24P", [r"\bs24\s*\+", r"\bs24\s*plus" + _E, r"s24\s*플러스", r"s24플러스",
               r"\bs24\s*플" + _E, r"갤s24\s*\+",
               # 보강: 24플(러스), 갤24+
               r"\b24\s*플러스" + _E, r"\b24플러스" + _E, r"갤24\s*\+"]),
    ("GFE24", [r"\bs24\s*fe" + _E, r"galaxy\s*s24\s*fe", r"s24fe" + _E,
               r"\b24\s*fe" + _E, r"\b24fe" + _E]),
    ("GS24",  [r"galaxy\s*s24" + _E, r"\bs24" + _E, r"갤럭시\s*s24" + _E,
               r"갤s24" + _E, r"갤24" + _E,
               # 보강
               r"s24\s*일반", r"s24\s*노멀", r"s24\s*기본", r"s24\s*시리즈"]),

    # ── S23 ──
    ("GS23U", [r"\bs23\s*ultra" + _E, r"galaxy\s*s23\s*ultra", r"\bs23u" + _E,
               r"s23\s*울트라", r"s23울트라", r"\bs23\s*울" + _E, r"s23울" + _E,
               r"\b23u" + _E,
               # 보강
               r"\b23울" + _E, r"\b23울트라" + _E, r"갤23울"]),
    ("GS23P", [r"\bs23\s*\+", r"\bs23\s*plus" + _E, r"s23\s*플러스", r"s23플러스",
               r"\b23\s*플러스" + _E, r"\b23플러스" + _E]),
    ("GFE23", [r"\bs23\s*fe" + _E, r"galaxy\s*s23\s*fe", r"s23fe" + _E,
               r"\b23\s*fe" + _E, r"\b23fe" + _E]),
    ("GS23",  [r"galaxy\s*s23" + _E, r"\bs23" + _E, r"갤럭시\s*s23" + _E,
               r"갤s23" + _E, r"갤23" + _E,
               r"s23\s*일반", r"s23\s*노멀", r"s23\s*기본"]),

    # ── S22 (요약 트래킹) ──
    ("GS22U", [r"\bs22\s*ultra" + _E, r"\bs22u" + _E, r"s22\s*울트라", r"s22울트라",
               r"\b22울" + _E, r"\b22울트라" + _E]),
    ("GS22",  [r"galaxy\s*s22" + _E, r"\bs22" + _E, r"\b22플러스" + _E, r"22플러스",
               r"갤s22" + _E, r"갤22" + _E]),

    # ── Z 6세대 / 5세대 ──
    ("GZF6",  [r"\bz\s*fold\s*6" + _E, r"\bfold\s*6" + _E, r"폴드\s*6" + _E,
               r"폴드6", r"\b폴6" + _E]),
    ("GZFL6", [r"\bz\s*flip\s*6" + _E, r"\bflip\s*6" + _E, r"플립\s*6" + _E,
               r"플립6", r"\b플6" + _E]),
    ("GZF5",  [r"\bz\s*fold\s*5" + _E, r"\bfold\s*5" + _E, r"폴드\s*5" + _E,
               r"폴드5", r"\b폴5" + _E]),
    ("GZFL5", [r"\bz\s*flip\s*5" + _E, r"\bflip\s*5" + _E, r"플립\s*5" + _E,
               r"플립5", r"\b플5" + _E]),

    # ── Watch 7 / 6 ──
    ("GW7",   [r"\bwatch\s*7" + _E, r"galaxy\s*watch\s*7", r"워치\s*7" + _E,
               r"워치7", r"갤워치\s*7", r"갤워치7"]),
    ("GW6",   [r"\bwatch\s*6" + _E, r"galaxy\s*watch\s*6", r"워치\s*6" + _E,
               r"워치6", r"갤워치\s*6", r"갤워치6"]),

    # ── Buds 2 Pro ──
    ("GB2P",  [r"buds\s*2\s*pro", r"buds2\s*pro", r"버즈\s*2\s*프로",
               r"버즈2프로", r"\b버2프" + _E]),
    ("GB2",   [r"\bbuds\s*2" + _E, r"\bbuds2" + _E, r"버즈\s*2" + _E, r"버즈2",
               r"\b버2" + _E]),

    # ═══════════ 경쟁사 (iPhone) ═══════════
    # 더 큰 번호 우선, Pro Max → Pro → 기본
    ("AP16PM",[r"iphone\s*16\s*pro\s*max", r"아이폰\s*16\s*프로\s*맥스",
               r"\b16\s*프맥" + _E, r"아이폰16프맥", r"16프맥"]),
    ("AP16P", [r"iphone\s*16\s*pro" + _E, r"아이폰\s*16\s*프로" + _E, r"아이폰16프로",
               r"\b아16프" + _E]),
    ("AP16",  [r"\biphone\s*16" + _E, r"아이폰\s*16" + _E, r"아이폰16" + _E,
               r"\b아16" + _E]),
    ("AP15PM",[r"iphone\s*15\s*pro\s*max", r"아이폰\s*15\s*프로\s*맥스",
               r"\b15\s*프맥" + _E, r"15프맥"]),
    ("AP15P", [r"iphone\s*15\s*pro" + _E, r"아이폰\s*15\s*프로" + _E, r"아이폰15프로"]),
    ("AP15",  [r"\biphone\s*15" + _E, r"아이폰\s*15" + _E, r"아이폰15" + _E,
               r"\b아15" + _E]),
    ("AP14",  [r"\biphone\s*14" + _E, r"아이폰\s*14" + _E, r"아이폰14" + _E,
               r"\b아14" + _E]),

    # ═══════════ 경쟁사 (Google Pixel) ═══════════
    ("PX9P",  [r"pixel\s*9\s*pro\s*xl", r"pixel\s*9\s*pro" + _E, r"픽셀\s*9\s*프로",
               r"\bpx9p" + _E, r"\bpx\s*9p" + _E]),
    ("PX9",   [r"\bpixel\s*9" + _E, r"픽셀\s*9" + _E, r"\bpx9" + _E]),
    ("PX8P",  [r"pixel\s*8\s*pro" + _E, r"픽셀\s*8\s*프로", r"\bpx8p" + _E]),
    ("PX8",   [r"\bpixel\s*8" + _E, r"픽셀\s*8" + _E, r"\bpx8" + _E]),
]

# 사전 컴파일
_COMPILED: List[Tuple[str, List[re.Pattern]]] = [
    (code, [re.compile(p, re.IGNORECASE) for p in pats])
    for code, pats in PRODUCT_PATTERNS
]


def infer_product_code(text: Optional[str]) -> Optional[str]:
    """본문/제목에서 제품 코드 추론. 매치 없으면 None.

    PRODUCT_PATTERNS 순서상 가장 먼저 매칭되는 코드를 채택 (구체적 변형 우선).
    """
    if not text:
        return None
    for code, patterns in _COMPILED:
        for pat in patterns:
            if pat.search(text):
                return code
    return None
