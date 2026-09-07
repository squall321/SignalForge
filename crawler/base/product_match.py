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
import math
import re
from typing import Dict, List, Optional, Tuple

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

    # ── Galaxy A / F ── (신모델 2026: A57/A37/A27, F25 — 카탈로그엔 있었으나 패턴 누락)
    ("GA57",  [r"galaxy\s*a57" + _E, r"\ba57" + _E, r"갤럭시\s*a57"]),
    ("GA56",  [r"galaxy\s*a56" + _E, r"\ba56" + _E, r"갤럭시\s*a56"]),
    ("GA37",  [r"galaxy\s*a37" + _E, r"\ba37" + _E, r"갤럭시\s*a37"]),
    ("GA27",  [r"galaxy\s*a27" + _E, r"\ba27" + _E, r"갤럭시\s*a27"]),
    ("GF25",  [r"galaxy\s*f25" + _E, r"갤럭시\s*f25"]),

    # ── Galaxy Watch (Ultra → 9 → 8) ──
    ("GWU",   [r"watch\s*ultra", r"galaxy\s*watch\s*ultra", r"워치\s*울트라",
               r"워치울트라", r"\b워치울" + _E]),
    ("GW9",   [r"\bwatch\s*9" + _E, r"\bwatch9" + _E, r"galaxy\s*watch\s*9",
               r"워치\s*9" + _E, r"워치9", r"갤워치\s*9", r"갤워치9"]),
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
    # 구형 iPhone — 제품은 등록돼 있으나 패턴이 없어 미태깅이던 것 보강(단독 글만 태깅).
    ("AP13",  [r"\biphone\s*13" + _E, r"아이폰\s*13" + _E, r"아이폰13" + _E]),
    ("AP12",  [r"\biphone\s*12" + _E, r"아이폰\s*12" + _E, r"아이폰12" + _E]),
    ("AP11",  [r"\biphone\s*11" + _E, r"아이폰\s*11" + _E, r"아이폰11" + _E]),
    ("AP10",  [r"\biphone\s*x" + _E, r"아이폰\s*x" + _E, r"아이폰\s*텐" + _E]),
    ("AP8",   [r"\biphone\s*8" + _E, r"아이폰\s*8" + _E, r"아이폰8" + _E]),
    ("AP7",   [r"\biphone\s*7" + _E, r"아이폰\s*7" + _E, r"아이폰7" + _E]),
    ("AP6",   [r"\biphone\s*6" + _E, r"아이폰\s*6" + _E, r"아이폰6" + _E]),

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
    """본문/제목에서 대표 제품 코드 추론. 매치 없으면 None.

    infer_all_product_codes() 의 primary 와 **항상** 같다
    (voc_records.product_id 와 voc_product_links.primary 정합).
    """
    out = infer_all_product_codes(text)
    return out[0][0] if out else None


# ═════════ primary 재선정 — 문서 주제와의 관련성으로 고른다 ══════════════
#
# 후보 집합(어떤 제품이 언급됐나)은 그대로 두고 **어느 후보를 primary 로 올릴지**
# 만 다시 고른다. PRODUCT_PATTERNS 선언 순서(구체성·최신순)는 "이 글이 무엇에
# 관한 글인가"와 무관해서, 인도발 'Galaxy S26 폭발' 기사가 본문의 'S25 +' 조각
# 때문에 GS25P 로 귀속되는 식의 오발화가 났다.
#
# 적용 범위 — 첫 줄이 제목인 글(뉴스·게시판 제목형)에 한정한다. 제목이 없는
# 짧은 댓글류는 관련성 신호가 약해 재랭킹이 오히려 해로웠다(실측: 신규 홀드아웃
# 제목없음 33건 90.9% → 60.6%). 그래서 제목이 없으면 현행 순서를 그대로 쓴다.
#
# 실측 (2026-09-07, 수기 라벨. 현행 kept[0] → 재선정)
#   신규 홀드아웃 H2(140)  79.3% → 87.9%   고침 24 / 망침 12
#   신규 홀드아웃 H3(137)  79.6% → 89.1%   고침 22 / 망침  9   McNemar p=0.029
#   구 라벨셋   (287)      77.4% → 90.6%   고침 46 / 망침  8
_TITLE_MAX = 300   # 첫 줄이 이보다 길면 제목이 아니라 본문 첫 문단으로 본다
# 가중치는 두 피처뿐 — 제목 등장(강)과 등장 빈도(약). 소유·전환방향 등 부가 항은
# 두 홀드아웃 모두에서 성능을 **떨어뜨려** 제거했다(88.4 vs 91.3 / 90.6 vs 92.0).
_W_TITLE, _W_COUNT = 3.0, 1.0

_PRIORITY: Dict[str, int] = {code: i for i, (code, _) in enumerate(PRODUCT_PATTERNS)}


def _spans_by_code(text: str, codes: List[str]) -> Dict[str, List[Tuple[int, int]]]:
    """후보 코드별 등장 구간 전부. 우선순위 높은(구체적인) 코드가 먼저 구간을 차지한다.

    infer_all_product_codes 와 같은 겹침 억제 규칙이되 첫 매칭만이 아니라
    **모든** 매칭을 세어 등장 횟수를 얻는다. 후보 집합 자체는 건드리지 않는다.
    """
    wanted = set(codes)
    cands: List[Tuple[Tuple[int, int, int], str, int, int]] = []
    for code, patterns in _COMPILED:
        if code not in wanted:
            continue
        pri = _PRIORITY[code]
        for pat in patterns:
            for m in pat.finditer(text):
                s, e = m.span()
                cands.append(((pri, s - e, s), code, s, e))   # 우선순위 → 긴 매칭 → 앞
    cands.sort(key=lambda t: t[0])

    taken: List[Tuple[int, int]] = []
    out: Dict[str, List[Tuple[int, int]]] = {c: [] for c in codes}
    for _key, code, s, e in cands:
        if any(s < te and ts < e for ts, te in taken):
            continue
        taken.append((s, e))
        out[code].append((s, e))
    for c in out:
        out[c].sort()
    return out


def _pick_primary(text: str, codes: List[str]) -> str:
    """후보 중 문서 주제에 가장 가까운 코드. 판단 근거가 없으면 codes[0](현행).

    **제목 있는 글에만 적용한다.** 전면 재랭킹은 신규 홀드아웃에서 재현되지 않았다
    (+1.4pt, p=0.89). 이득이 극단적으로 이질적이어서 — 제목 줄이 있으면 +11.2pt 이지만
    제목 없는 짧은 커뮤니티 글에서는 90.9%→60.6% 로 크게 진다. 게이팅하면 신규 표본
    277건에서 79.4%→88.4%(p=0.003), 2차 홀드아웃에서도 재현된다.

    피처는 **제목 등장 + 등장 빈도** 둘뿐이다. 제목내 위치·첫등장 위치·소유표현·
    from/to 방향 항을 더한 7피처 안은 두 표본 모두에서 이 단순안에 졌고(88.4 vs 91.3,
    90.6 vs 92.0, McNemar p=0.0169) 회귀도 2배(29건 vs 14건)였다. 신호를 더 넣는 것이
    항상 낫지는 않다.
    """
    if len(codes) < 2:
        return codes[0]
    nl = text.find("\n")
    if not 0 < nl <= _TITLE_MAX:
        return codes[0]                      # 제목 없는 글은 현행 우선순위 유지
    spans = _spans_by_code(text, codes)

    best, best_score = codes[0], None
    for code in codes:
        sp = spans.get(code)
        if not sp:
            continue
        score = (_W_TITLE * (1.0 if sp[0][0] < nl else 0.0)
                 + _W_COUNT * math.log2(1 + len(sp))
                 - 0.001 * _PRIORITY[code])   # 동점이면 현행 순서와 같게
        if best_score is None or score > best_score:
            best, best_score = code, score
    return best


# 비교 문맥 마커 — non-primary 링크를 mentioned 대신 compared 로 분류.
_COMPARE_RE = re.compile(
    r"\bvs\.?\b|versus|\bcompar(?:e|ed|ison)|대비|비교|어느\s*(?:게|것이)\s*나|"
    r"which\s+is\s+better",
    re.IGNORECASE,
)


def infer_all_product_codes(text: Optional[str]) -> List[Tuple[str, str]]:
    """본문에서 언급된 **모든** 제품을 (code, role) 로 추출.

    role — primary(문서 주제에 가장 가까운 제품) / compared(비교 마커 있음) / mentioned.

    span 겹침 억제가 핵심이다. `_E` 는 뒤 문자가 공백이면 통과하므로
    'Galaxy S26 Ultra' 가 GS26U(7,16) 와 GS26(0,10) 을 **동시에** 매칭한다.
    우선순위 순으로 훑으며 이미 채택된 구간과 겹치는 매칭을 버려야
    가장 구체적인 모델만 남는다. 'S26 Ultra vs Fold8' 처럼 구간이 안 겹치면
    둘 다 보존된다(비교글 신호 확보 = 이 함수의 존재 이유).

    primary 는 후보 중 **문서 주제와 가장 관련 있는** 것으로 고른다
    (_pick_primary 참조). 후보 집합과 compared/mentioned 판정은 그대로다.
    후보가 하나면 결과는 이전과 완전히 동일하다.
    """
    if not text:
        return []

    kept: List[Tuple[str, Tuple[int, int]]] = []
    for code, patterns in _COMPILED:
        span = None
        for pat in patterns:
            m = pat.search(text)
            if m:
                span = m.span()
                break
        if span is None:
            continue
        # 이미 채택된(더 우선순위 높은=구체적인) 매칭과 구간이 겹치면 버림
        if any(span[0] < e and s < span[1] for _, (s, e) in kept):
            continue
        kept.append((code, span))

    if not kept:
        return []
    codes = [c for c, _ in kept]
    primary = _pick_primary(text, codes)
    role = "compared" if _COMPARE_RE.search(text) else "mentioned"
    return ([(primary, "primary")]
            + [(c, role) for c in codes if c != primary])
