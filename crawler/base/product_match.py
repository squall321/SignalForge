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
    # ── Galaxy S27 (2027-01 예정. GS26 선등록 관행과 동일) ──
    # 실측 언급 S27 1,640 · S27 Ultra 1,167 · S27+ 269
    ("GS27U", [r"\bs27\s*ultra" + _E, r"galaxy\s*s27\s*ultra", r"\bs27u" + _E,
               r"s27\s*울트라", r"s27울트라", r"갤럭시\s*s27\s*울트라"]),
    ("GS27P", [r"\bs27\s*\+", r"\bs27\s*plus" + _E, r"galaxy\s*s27\s*\+",
               r"s27\s*플러스", r"s27플러스"]),
    ("GS27",  [r"galaxy\s*s27" + _E, r"\bs27" + _E, r"갤럭시\s*s27" + _E,
               r"갤s27" + _E, r"갤럭시\s*S27"]),

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
    # 'buds pro 4' 어순 역전형 — 한글엔 있었으나 영문에 없어 GB4P 가 저평가됐다
    # (실측: "the buds pro 4s" 로 쓴 갤버즈4프로 불만글이 Bose QC Ultra 로 넘어감).
    # 타사 'OnePlus Buds Pro 3' 는 브랜드 인접 가드가 막는다.
    ("GB4P",  [r"buds\s*4\s*pro", r"buds4\s*pro", r"buds\s*pro\s*4", r"버즈\s*4\s*프로",
               r"버즈4\s*프로", r"버즈4프로", r"버즈프로\s*4", r"버즈프로4",
               r"\b버4프" + _E, r"버4\s*프로", r"버4프로"]),
    ("GB4",   [r"\bbuds\s*4" + _E, r"\bbuds4" + _E, r"galaxy\s*buds\s*4",
               r"버즈\s*4" + _E, r"버즈4" + _E, r"갤버즈4", r"갤\s*버즈\s*4",
               r"\b버4" + _E]),

    # ── Galaxy Buds 3 ──
    ("GB3P",  [r"buds\s*3\s*pro", r"buds3\s*pro", r"buds\s*pro\s*3", r"버즈\s*3\s*프로",
               r"버즈3\s*프로", r"버즈3프로", r"버즈프로\s*3", r"버즈프로3",
               r"\b버3프" + _E, r"\bb3p" + _E]),
    ("GB3",   [r"\bbuds\s*3" + _E, r"\bbuds3" + _E, r"galaxy\s*buds\s*3",
               r"버즈\s*3" + _E, r"버즈3", r"갤버즈3", r"\b버3" + _E]),

    # ── Galaxy Ring ──
    # 무경계 `\bring\s*2`·`\bring2`·`링2` 는 타사 스마트링을 흡수했다. 실측 —
    # 'Luna Ring 2.0', 'Circular Ring 2'(+포르투갈어판), Vertu 매장 기사(러시아어)가
    # GR2 로 태깅됐고, 활성 링크 512건 중 10건에 삼성 링 근거가 아예 없었다.
    # 브랜드 인접 가드로는 못 막는다 — Luna/Circular/Vertu 가 _WORD_BRAND 에 없어서다.
    # → 브랜드를 매칭에 흡수하거나(1행), 전방 80자 안에 삼성 앵커를 요구한다(2행).
    ("GR2",   [r"(?:galaxy|samsung|갤럭시|삼성|갤)[\s\-'’]{0,6}(?:ring|링)\s*2" + _E,
               r"\bring\s*2" + _E + r"(?=[^\n]{0,80}(?:galaxy|samsung|갤럭시|삼성))",
               r"\bring2" + _E + r"(?=[^\n]{0,80}(?:galaxy|samsung|갤럭시|삼성))",
               r"링2(?=[^\n]{0,80}(?:galaxy|samsung|갤럭시|삼성))",
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
    ("GB2P",  [r"buds\s*2\s*pro", r"buds2\s*pro", r"buds\s*pro\s*2", r"버즈\s*2\s*프로",
               r"버즈2프로", r"\b버2프" + _E]),
    ("GB2",   [r"\bbuds\s*2" + _E, r"\bbuds2" + _E, r"버즈\s*2" + _E, r"버즈2",
               r"\b버2" + _E]),


    # ── Galaxy Nexus (2011, 삼성·구글 공동) — 실측 1,643건인데 미등록이었다 ──
    ("GNEXUS", [r"galaxy\s*nexus" + _E, r"갤럭시\s*넥서스" + _E, r"\bgnex" + _E]),

    # ═══════════ 경쟁사 · Apple iPhone — Pro Max → Pro → Plus/mini/e → 기본 순.
    # APAIR 가 맨 앞인 이유는 'iPhone 17 Air' 를 AP17 이 가로채지 않게 하기 위해서다. ═══════════
    # ── iPhone 18 세대 + 폴더블 (2026-09~2027) ──
    # 실측 iPhone Fold 2,490(루머 아닌 실사용 토론) · 18 1,951 · 18 Pro 1,511 ·
    # 18 Pro Max 599 · Air 2 195 · 18e 146
    # APFOLD 는 'fold' 가 갤럭시와 겹치므로 **iphone/apple 인접을 반드시 요구**한다.
    ("APFOLD", [r"\biphone\s*fold" + _E, r"foldable\s*iphone" + _E,
                r"\biphone\s*폴드" + _E, r"폴더블\s*아이폰" + _E,
                r"아이폰\s*폴드" + _E]),
    ("AP18PM", [r"iphone\s*18\s*pro\s*max", r"아이폰\s*18\s*프로\s*맥스",
                r"\b18\s*프맥" + _E, r"18프맥"]),
    ("AP18P", [r"iphone\s*18\s*pro" + _E, r"아이폰\s*18\s*프로" + _E, r"아이폰18프로"]),
    ("AP18E", [r"\biphone\s*18\s*e" + _E, r"아이폰\s*18\s*e" + _E]),
    ("AP18",  [r"\biphone\s*18" + _E, r"아이폰\s*18" + _E, r"아이폰18" + _E]),
    ("APAIR2", [r"iphone\s*air\s*2" + _E, r"아이폰\s*에어\s*2" + _E,
                r"에어\s*2세대(?=[^\n]{0,40}(?:iphone|아이폰|apple|애플))"]),

    ("APAIR", [
        'iphone\\s*17\\s*air(?![0-9a-zA-Z])', '\\biphone\\s*air(?![0-9a-zA-Z])',
        '아이폰\\s*17\\s*에어(?![0-9a-zA-Z])', '아이폰\\s*에어(?!팟|드랍|드롭|태그|플레이|프린트)(?![0-9a-zA-Z])'
    ]),
    ("AP17PM", [
        'iphone\\s*17\\s*pro\\s*max', '아이폰\\s*17\\s*프로\\s*맥스', '\\b17\\s*프맥(?![0-9a-zA-Z])',
        '17프맥'
    ]),
    ("AP17P", ['iphone\\s*17\\s*pro(?![0-9a-zA-Z])', '아이폰\\s*17\\s*프로(?![0-9a-zA-Z])']),
    ("AP17E", ['\\biphone\\s*17\\s*e(?![0-9a-zA-Z])', '아이폰\\s*17\\s*e(?![0-9a-zA-Z])']),
    ("AP17", [
        '\\biphone\\s*17(?![0-9a-zA-Z])', '아이폰\\s*17(?![0-9a-zA-Z])', '아이폰17(?![0-9a-zA-Z])'
    ]),
    ("AP16PM", [
        'iphone\\s*16\\s*pro\\s*max', '아이폰\\s*16\\s*프로\\s*맥스', '\\b16\\s*프맥(?![0-9a-zA-Z])',
        '아이폰16프맥', '16프맥'
    ]),
    ("AP16P", [
        'iphone\\s*16\\s*pro(?![0-9a-zA-Z])', '아이폰\\s*16\\s*프로(?![0-9a-zA-Z])', '아이폰16프로',
        '\\b아16프(?![0-9a-zA-Z])'
    ]),
    ("AP16PL", [
        'iphone\\s*16\\s*plus(?![0-9a-zA-Z])', '아이폰\\s*16\\s*플(?![0-9a-zA-Z])',
        'iphone\\s*16\\+(?!\\s*mac)'
    ]),
    ("AP16E", ['\\biphone\\s*16\\s*e(?![0-9a-zA-Z])', '아이폰\\s*16\\s*e(?![0-9a-zA-Z])']),
    ("AP16", [
        '\\biphone\\s*16(?![0-9a-zA-Z])', '아이폰\\s*16(?![0-9a-zA-Z])', '아이폰16(?![0-9a-zA-Z])',
        '\\b아16(?![0-9a-zA-Z])'
    ]),
    ("AP15PM", [
        'iphone\\s*15\\s*pro\\s*max', '아이폰\\s*15\\s*프로\\s*맥스', '\\b15\\s*프맥(?![0-9a-zA-Z])',
        '15프맥'
    ]),
    ("AP15P", [
        'iphone\\s*15\\s*pro(?![0-9a-zA-Z])', '아이폰\\s*15\\s*프로(?![0-9a-zA-Z])', '아이폰15프로'
    ]),
    ("AP15PL", [
        'iphone\\s*15\\s*plus(?![0-9a-zA-Z])', '아이폰\\s*15\\s*플(?![0-9a-zA-Z])',
        'iphone\\s*15\\+(?!\\s*mac)'
    ]),
    ("AP15", [
        '\\biphone\\s*15(?![0-9a-zA-Z])', '아이폰\\s*15(?![0-9a-zA-Z])', '아이폰15(?![0-9a-zA-Z])',
        '\\b아15(?![0-9a-zA-Z])'
    ]),
    ("AP14PM", [
        'iphone\\s*14\\s*pro\\s*max', '아이폰\\s*14\\s*프로\\s*맥스', '\\b14\\s*프맥(?![0-9a-zA-Z])',
        '14프맥'
    ]),
    ("AP14P", ['iphone\\s*14\\s*pro(?![0-9a-zA-Z])', '아이폰\\s*14\\s*프로(?![0-9a-zA-Z])']),
    ("AP14PL", [
        'iphone\\s*14\\s*plus(?![0-9a-zA-Z])', '아이폰\\s*14\\s*플(?![0-9a-zA-Z])',
        'iphone\\s*14\\+(?!\\s*mac)'
    ]),
    ("AP14", [
        '\\biphone\\s*14(?![0-9a-zA-Z])', '아이폰\\s*14(?![0-9a-zA-Z])', '아이폰14(?![0-9a-zA-Z])',
        '\\b아14(?![0-9a-zA-Z])'
    ]),
    ("AP13PM", [
        'iphone\\s*13\\s*pro\\s*max', '아이폰\\s*13\\s*프로\\s*맥스', '\\b13\\s*프맥(?![0-9a-zA-Z])',
        '13프맥'
    ]),
    ("AP13P", ['iphone\\s*13\\s*pro(?![0-9a-zA-Z])', '아이폰\\s*13\\s*프로(?![0-9a-zA-Z])']),
    ("AP13MI", ['iphone\\s*13\\s*mini(?![0-9a-zA-Z])', '아이폰\\s*13\\s*미니', '13미니']),
    ("AP13", [
        '\\biphone\\s*13(?![0-9a-zA-Z])', '아이폰\\s*13(?![0-9a-zA-Z])', '아이폰13(?![0-9a-zA-Z])'
    ]),
    ("AP12PM", [
        'iphone\\s*12\\s*pro\\s*max', '아이폰\\s*12\\s*프로\\s*맥스', '\\b12\\s*프맥(?![0-9a-zA-Z])',
        '12프맥'
    ]),
    ("AP12P", ['iphone\\s*12\\s*pro(?![0-9a-zA-Z])', '아이폰\\s*12\\s*프로(?![0-9a-zA-Z])']),
    ("AP12MI", ['iphone\\s*12\\s*mini(?![0-9a-zA-Z])', '아이폰\\s*12\\s*미니', '12미니']),
    ("AP12", [
        '\\biphone\\s*12(?![0-9a-zA-Z])', '아이폰\\s*12(?![0-9a-zA-Z])', '아이폰12(?![0-9a-zA-Z])'
    ]),
    ("AP11PM", [
        'iphone\\s*11\\s*pro\\s*max', '아이폰\\s*11\\s*프로\\s*맥스', '\\b11\\s*프맥(?![0-9a-zA-Z])',
        '11프맥'
    ]),
    ("AP11P", ['iphone\\s*11\\s*pro(?![0-9a-zA-Z])', '아이폰\\s*11\\s*프로(?![0-9a-zA-Z])']),
    ("AP11", [
        '\\biphone\\s*11(?![0-9a-zA-Z])', '아이폰\\s*11(?![0-9a-zA-Z])', '아이폰11(?![0-9a-zA-Z])'
    ]),
    ("AP10SM", ['iphone\\s*xs\\s*max', '아이폰\\s*xs\\s*(?:max|맥스)']),
    ("AP10S", ['\\biphone\\s*xs(?![0-9a-zA-Z])', '아이폰\\s*xs(?![0-9a-zA-Z])']),
    ("AP10R", ['\\biphone\\s*xr(?![0-9a-zA-Z])', '아이폰\\s*xr(?![0-9a-zA-Z])']),
    ("AP10", [
        '\\biphone\\s*x(?![0-9a-zA-Z])', '아이폰\\s*x(?![0-9a-zA-Z])', '아이폰\\s*텐(?![0-9a-zA-Z])'
    ]),
    ("APSE3", [
        'iphone\\s*se\\s*\\(?\\s*3(?![0-9a-zA-Z])', '아이폰\\s*se\\s*\\(?\\s*3(?![0-9a-zA-Z])',
        'iphone\\s*se\\s*\\(?\\s*(?:3rd|third)', 'iphone\\s*se\\s*2022'
    ]),
    ("APSE2", [
        'iphone\\s*se\\s*\\(?\\s*2(?![0-9a-zA-Z])', '아이폰\\s*se\\s*\\(?\\s*2(?![0-9a-zA-Z])',
        'iphone\\s*se\\s*\\(?\\s*(?:2nd|second)', 'iphone\\s*se\\s*2020'
    ]),
    ("APSE1", [
        'iphone\\s*se\\s*\\(?\\s*1\\s*(?:st|세대)', '아이폰\\s*se\\s*\\(?\\s*1\\s*세대',
        'iphone\\s*se\\s*\\(?\\s*first', 'iphone\\s*se\\s*2016'
    ]),
    ("AP8", [
        '\\biphone\\s*8(?![0-9a-zA-Z])', '아이폰\\s*8(?![0-9a-zA-Z])', '아이폰8(?![0-9a-zA-Z])'
    ]),
    ("AP7", [
        '\\biphone\\s*7(?![0-9a-zA-Z])', '아이폰\\s*7(?![0-9a-zA-Z])', '아이폰7(?![0-9a-zA-Z])'
    ]),
    ("AP6", [
        '\\biphone\\s*6(?![0-9a-zA-Z])', '아이폰\\s*6(?![0-9a-zA-Z])', '아이폰6(?![0-9a-zA-Z])'
    ]),

    # ═══════════ 경쟁사 · Google Pixel — N Pro Fold → Pro XL → Pro → a → 기본 순.
    # PXFOLD(무세대 Pixel Fold)는 반드시 세대별 Fold 뒤에 둔다. ═══════════
    ("PX11PF", [
        'pixel\\s*11\\s*pro\\s*folds?(?![0-9a-zA-Z])', 'pixel\\s*11\\s*folds?(?![0-9a-zA-Z])',
        'pixel\\s*folds?\\s*11(?![0-9a-zA-Z])', '픽셀\\s*11\\s*프로\\s*폴드', '픽셀\\s*11\\s*폴드'
    ]),
    ("PX11PXL", [
        'pixel\\s*11\\s*pro\\s*xl(?![0-9a-zA-Z])', 'pixel\\s*11\\s*xl(?![0-9a-zA-Z])',
        '픽셀\\s*11\\s*프로\\s*xl(?![0-9a-zA-Z])'
    ]),
    ("PX11P", ['pixel\\s*11\\s*pros?(?![0-9a-zA-Z])', '픽셀\\s*11\\s*프로(?![0-9a-zA-Z])']),
    ("PX11", ['(?<!mega)pixel\\s*11(?![0-9a-zA-Z%])', '픽셀\\s*11(?![0-9a-zA-Z%])']),
    ("PX10PF", [
        'pixel\\s*10\\s*pro\\s*folds?(?![0-9a-zA-Z])', 'pixel\\s*10\\s*folds?(?![0-9a-zA-Z])',
        'pixel\\s*folds?\\s*10(?![0-9a-zA-Z])', '픽셀\\s*10\\s*프로\\s*폴드', '픽셀\\s*10\\s*폴드'
    ]),
    ("PX10PXL", [
        'pixel\\s*10\\s*pro\\s*xl(?![0-9a-zA-Z])', 'pixel\\s*10\\s*xl(?![0-9a-zA-Z])',
        '픽셀\\s*10\\s*프로\\s*xl(?![0-9a-zA-Z])'
    ]),
    ("PX10P", ['pixel\\s*10\\s*pros?(?![0-9a-zA-Z])', '픽셀\\s*10\\s*프로(?![0-9a-zA-Z])']),
    ("PX10A", ['pixel\\s*10a(?![0-9a-zA-Z])', '픽셀\\s*10a(?![0-9a-zA-Z])']),
    ("PX10", ['(?<!mega)pixel\\s*10(?![0-9a-zA-Z%])', '픽셀\\s*10(?![0-9a-zA-Z%])']),
    ("PX9PF", [
        'pixel\\s*9\\s*pro\\s*folds?(?![0-9a-zA-Z])', 'pixel\\s*9\\s*folds?(?![0-9a-zA-Z])',
        'pixel\\s*folds?\\s*9(?![0-9a-zA-Z])', '픽셀\\s*9\\s*프로\\s*폴드', '픽셀\\s*9\\s*폴드'
    ]),
    ("PX9PXL", [
        'pixel\\s*9\\s*pro\\s*xl(?![0-9a-zA-Z])', 'pixel\\s*9\\s*xl(?![0-9a-zA-Z])',
        '픽셀\\s*9\\s*프로\\s*xl(?![0-9a-zA-Z])'
    ]),
    ("PX9P", [
        'pixel\\s*9\\s*pro(?![0-9a-zA-Z])', '픽셀\\s*9\\s*프로', '\\bpx9p(?![0-9a-zA-Z])',
        '\\bpx\\s*9p(?![0-9a-zA-Z])'
    ]),
    ("PX9A", ['pixel\\s*9a(?![0-9a-zA-Z])', '픽셀\\s*9a(?![0-9a-zA-Z])']),
    ("PX9", [
        '\\bpixel\\s*9(?![0-9a-zA-Z])', '픽셀\\s*9(?![0-9a-zA-Z])', '\\bpx9(?![0-9a-zA-Z])'
    ]),
    ("PXFOLD", ['pixel\\s*folds?(?![0-9a-zA-Z])', '픽셀\\s*폴드(?![0-9a-zA-Z])']),
    ("PX8A", ['pixel\\s*8a(?![0-9a-zA-Z])', '픽셀\\s*8a(?![0-9a-zA-Z])']),
    ("PX8P", ['pixel\\s*8\\s*pro(?![0-9a-zA-Z])', '픽셀\\s*8\\s*프로', '\\bpx8p(?![0-9a-zA-Z])']),
    ("PX8", [
        '\\bpixel\\s*8(?![0-9a-zA-Z])', '픽셀\\s*8(?![0-9a-zA-Z])', '\\bpx8(?![0-9a-zA-Z])'
    ]),
    ("PX7P", ['pixel\\s*7\\s*pros?(?![0-9a-zA-Z])', '픽셀\\s*7\\s*프로']),
    ("PX7A", ['pixel\\s*7a(?![0-9a-zA-Z])', '픽셀\\s*7a(?![0-9a-zA-Z])']),
    ("PX6P", ['pixel\\s*6\\s*pros?(?![0-9a-zA-Z])', '픽셀\\s*6\\s*프로']),
    ("PX6A", ['pixel\\s*6a(?![0-9a-zA-Z])', '픽셀\\s*6a(?![0-9a-zA-Z])']),
    ("PX5A", ['pixel\\s*5a(?![0-9a-zA-Z])', '픽셀\\s*5a(?![0-9a-zA-Z])']),
    ("PX4A", ['pixel\\s*4a(?![0-9a-zA-Z])', '픽셀\\s*4a(?![0-9a-zA-Z])']),
    ("PX3A", ['pixel\\s*3a(?![0-9a-zA-Z])', '픽셀\\s*3a(?![0-9a-zA-Z])']),

    # ═══════════ 경쟁사 · Apple 웨어러블 (Apple Watch / AirPods) ═══════════
    ("AWU4", [
        '(?:apple\\s*watch|애플\\s*워치|아이워치|iwatch)\\s*\\(?\\s*ultra\\s*4(?![0-9])(?![:,][0-9])(?!\\.[0-9a-z])',
        '(?:apple\\s*watch|애플\\s*워치|아이워치|iwatch)\\s*울트라\\s*4(?![0-9])(?![:,][0-9])(?!\\.[0-9a-z])',
        'apple\\s*watch(?:(?!galaxy|samsung|삼성|갤럭|pixel|픽셀|garmin|amazfit|xiaomi|huawei|oneplus|fitbit|nothing|oura|whoop|mi\\s*band).){0,30}?(?<![0-9a-z])ultra\\s*4(?![0-9])(?![:,][0-9])(?!\\.[0-9a-z])'
    ]),
    ("AWU3", [
        '(?:apple\\s*watch|애플\\s*워치|아이워치|iwatch)\\s*\\(?\\s*ultra\\s*3(?![0-9])(?![:,][0-9])(?!\\.[0-9a-z])',
        '(?:apple\\s*watch|애플\\s*워치|아이워치|iwatch)\\s*울트라\\s*3(?![0-9])(?![:,][0-9])(?!\\.[0-9a-z])',
        'apple\\s*watch(?:(?!galaxy|samsung|삼성|갤럭|pixel|픽셀|garmin|amazfit|xiaomi|huawei|oneplus|fitbit|nothing|oura|whoop|mi\\s*band).){0,30}?(?<![0-9a-z])ultra\\s*3(?![0-9])(?![:,][0-9])(?!\\.[0-9a-z])'
    ]),
    ("AWU2", [
        '(?:apple\\s*watch|애플\\s*워치|아이워치|iwatch)\\s*\\(?\\s*ultra\\s*(?:2|ii)(?![0-9])(?![:,][0-9])(?!\\.[0-9a-z])',
        '(?:apple\\s*watch|애플\\s*워치|아이워치|iwatch)\\s*울트라\\s*2(?![0-9])(?![:,][0-9])(?!\\.[0-9a-z])',
        'apple\\s*watch(?:(?!galaxy|samsung|삼성|갤럭|pixel|픽셀|garmin|amazfit|xiaomi|huawei|oneplus|fitbit|nothing|oura|whoop|mi\\s*band).){0,30}?(?<![0-9a-z])ultra\\s*2(?![0-9])(?![:,][0-9])(?!\\.[0-9a-z])'
    ]),
    ("AWU1", [
        '(?:apple\\s*watch|애플\\s*워치|아이워치|iwatch)\\s*\\(?\\s*ultra(?![0-9a-z])(?!\\s*[2-9])',
        '(?:apple\\s*watch|애플\\s*워치|아이워치|iwatch)\\s*울트라(?!\\s*[2-9])'
    ]),
    ("AWSE3", [
        '(?:apple\\s*watch|애플\\s*워치|아이워치|iwatch)\\s*\\(?\\s*se\\s*(?:3|3rd|third)(?![0-9])(?![:,][0-9])(?!\\.[0-9a-z])',
        'apple\\s*watch(?:(?!galaxy|samsung|삼성|갤럭|pixel|픽셀|garmin|amazfit|xiaomi|huawei|oneplus|fitbit|nothing|oura|whoop|mi\\s*band).){0,30}?(?<![0-9a-z])se\\s*(?:3|3rd)(?![0-9])(?![:,][0-9])(?!\\.[0-9a-z])'
    ]),
    ("AWSE2", [
        '(?:apple\\s*watch|애플\\s*워치|아이워치|iwatch)\\s*\\(?\\s*se\\s*(?:2|ii|2nd|second)(?![0-9])(?![:,][0-9])(?!\\.[0-9a-z])',
        'apple\\s*watch(?:(?!galaxy|samsung|삼성|갤럭|pixel|픽셀|garmin|amazfit|xiaomi|huawei|oneplus|fitbit|nothing|oura|whoop|mi\\s*band).){0,30}?(?<![0-9a-z])se\\s*(?:2|2nd)(?![0-9])(?![:,][0-9])(?!\\.[0-9a-z])'
    ]),
    ("AWSE", [
        '(?:apple\\s*watch|애플\\s*워치|아이워치|iwatch)\\s*\\(?\\s*se(?![0-9a-z])(?!\\s*[2-9])'
    ]),
    ("AWS12", [
        '(?:apple\\s*watch|애플\\s*워치|아이워치|iwatch)\\s*\\(?\\s*(?:series|시리즈)\\s*12(?![0-9])(?![:,][0-9])(?!\\.[0-9a-z])',
        '(?:apple\\s*watch|애플\\s*워치|아이워치|iwatch)\\s*\\(?\\s*12(?![0-9])(?![:,][0-9])(?!\\.[0-9a-z])',
        'apple\\s*watch(?:(?!galaxy|samsung|삼성|갤럭|pixel|픽셀|garmin|amazfit|xiaomi|huawei|oneplus|fitbit|nothing|oura|whoop|mi\\s*band).){0,30}?(?:series|시리즈)\\s*12(?![0-9])(?![:,][0-9])(?!\\.[0-9a-z])'
    ]),
    ("AWS11", [
        '(?:apple\\s*watch|애플\\s*워치|아이워치|iwatch)\\s*\\(?\\s*(?:series|시리즈)\\s*11(?![0-9])(?![:,][0-9])(?!\\.[0-9a-z])',
        '(?:apple\\s*watch|애플\\s*워치|아이워치|iwatch)\\s*\\(?\\s*11(?![0-9])(?![:,][0-9])(?!\\.[0-9a-z])',
        'apple\\s*watch(?:(?!galaxy|samsung|삼성|갤럭|pixel|픽셀|garmin|amazfit|xiaomi|huawei|oneplus|fitbit|nothing|oura|whoop|mi\\s*band).){0,30}?(?:series|시리즈)\\s*11(?![0-9])(?![:,][0-9])(?!\\.[0-9a-z])'
    ]),
    ("AWS10", [
        '(?:apple\\s*watch|애플\\s*워치|아이워치|iwatch)\\s*\\(?\\s*(?:series|시리즈)\\s*10(?![0-9])(?![:,][0-9])(?!\\.[0-9a-z])',
        '(?:apple\\s*watch|애플\\s*워치|아이워치|iwatch)\\s*\\(?\\s*10(?![0-9])(?![:,][0-9])(?!\\.[0-9a-z])',
        'apple\\s*watch(?:(?!galaxy|samsung|삼성|갤럭|pixel|픽셀|garmin|amazfit|xiaomi|huawei|oneplus|fitbit|nothing|oura|whoop|mi\\s*band).){0,30}?(?:series|시리즈)\\s*10(?![0-9])(?![:,][0-9])(?!\\.[0-9a-z])'
    ]),
    ("AWS9", [
        '(?:apple\\s*watch|애플\\s*워치|아이워치|iwatch)\\s*\\(?\\s*(?:series|시리즈)\\s*9(?![0-9])(?![:,][0-9])(?!\\.[0-9a-z])',
        '(?:apple\\s*watch|애플\\s*워치|아이워치|iwatch)\\s*\\(?\\s*9(?![0-9])(?![:,][0-9])(?!\\.[0-9a-z])',
        'apple\\s*watch(?:(?!galaxy|samsung|삼성|갤럭|pixel|픽셀|garmin|amazfit|xiaomi|huawei|oneplus|fitbit|nothing|oura|whoop|mi\\s*band).){0,30}?(?:series|시리즈)\\s*9(?![0-9])(?![:,][0-9])(?!\\.[0-9a-z])'
    ]),
    ("AWS8", [
        '(?:apple\\s*watch|애플\\s*워치|아이워치|iwatch)\\s*\\(?\\s*(?:series|시리즈)\\s*8(?![0-9])(?![:,][0-9])(?!\\.[0-9a-z])',
        '(?:apple\\s*watch|애플\\s*워치|아이워치|iwatch)\\s*\\(?\\s*8(?![0-9])(?![:,][0-9])(?!\\.[0-9a-z])',
        'apple\\s*watch(?:(?!galaxy|samsung|삼성|갤럭|pixel|픽셀|garmin|amazfit|xiaomi|huawei|oneplus|fitbit|nothing|oura|whoop|mi\\s*band).){0,30}?(?:series|시리즈)\\s*8(?![0-9])(?![:,][0-9])(?!\\.[0-9a-z])'
    ]),
    ("AWS7", [
        '(?:apple\\s*watch|애플\\s*워치|아이워치|iwatch)\\s*\\(?\\s*(?:series|시리즈)\\s*7(?![0-9])(?![:,][0-9])(?!\\.[0-9a-z])',
        '(?:apple\\s*watch|애플\\s*워치|아이워치|iwatch)\\s*\\(?\\s*7(?![0-9])(?![:,][0-9])(?!\\.[0-9a-z])',
        'apple\\s*watch(?:(?!galaxy|samsung|삼성|갤럭|pixel|픽셀|garmin|amazfit|xiaomi|huawei|oneplus|fitbit|nothing|oura|whoop|mi\\s*band).){0,30}?(?:series|시리즈)\\s*7(?![0-9])(?![:,][0-9])(?!\\.[0-9a-z])'
    ]),
    ("AWS6", [
        '(?:apple\\s*watch|애플\\s*워치|아이워치|iwatch)\\s*\\(?\\s*(?:series|시리즈)\\s*6(?![0-9])(?![:,][0-9])(?!\\.[0-9a-z])',
        '(?:apple\\s*watch|애플\\s*워치|아이워치|iwatch)\\s*\\(?\\s*6(?![0-9])(?![:,][0-9])(?!\\.[0-9a-z])',
        'apple\\s*watch(?:(?!galaxy|samsung|삼성|갤럭|pixel|픽셀|garmin|amazfit|xiaomi|huawei|oneplus|fitbit|nothing|oura|whoop|mi\\s*band).){0,30}?(?:series|시리즈)\\s*6(?![0-9])(?![:,][0-9])(?!\\.[0-9a-z])'
    ]),
    ("AWS5", [
        '(?:apple\\s*watch|애플\\s*워치|아이워치|iwatch)\\s*\\(?\\s*(?:series|시리즈)\\s*5(?![0-9])(?![:,][0-9])(?!\\.[0-9a-z])',
        '(?:apple\\s*watch|애플\\s*워치|아이워치|iwatch)\\s*\\(?\\s*5(?![0-9])(?![:,][0-9])(?!\\.[0-9a-z])',
        'apple\\s*watch(?:(?!galaxy|samsung|삼성|갤럭|pixel|픽셀|garmin|amazfit|xiaomi|huawei|oneplus|fitbit|nothing|oura|whoop|mi\\s*band).){0,30}?(?:series|시리즈)\\s*5(?![0-9])(?![:,][0-9])(?!\\.[0-9a-z])'
    ]),
    ("AWS4", [
        '(?:apple\\s*watch|애플\\s*워치|아이워치|iwatch)\\s*\\(?\\s*(?:series|시리즈)\\s*4(?![0-9])(?![:,][0-9])(?!\\.[0-9a-z])',
        '(?:apple\\s*watch|애플\\s*워치|아이워치|iwatch)\\s*\\(?\\s*4(?![0-9])(?![:,][0-9])(?!\\.[0-9a-z])',
        'apple\\s*watch(?:(?!galaxy|samsung|삼성|갤럭|pixel|픽셀|garmin|amazfit|xiaomi|huawei|oneplus|fitbit|nothing|oura|whoop|mi\\s*band).){0,30}?(?:series|시리즈)\\s*4(?![0-9])(?![:,][0-9])(?!\\.[0-9a-z])'
    ]),
    ("AWS3", [
        '(?:apple\\s*watch|애플\\s*워치|아이워치|iwatch)\\s*\\(?\\s*(?:series|시리즈)\\s*3(?![0-9])(?![:,][0-9])(?!\\.[0-9a-z])',
        '(?:apple\\s*watch|애플\\s*워치|아이워치|iwatch)\\s*\\(?\\s*3(?![0-9])(?![:,][0-9])(?!\\.[0-9a-z])',
        'apple\\s*watch(?:(?!galaxy|samsung|삼성|갤럭|pixel|픽셀|garmin|amazfit|xiaomi|huawei|oneplus|fitbit|nothing|oura|whoop|mi\\s*band).){0,30}?(?:series|시리즈)\\s*3(?![0-9])(?![:,][0-9])(?!\\.[0-9a-z])'
    ]),
    ("AWS2", [
        '(?:apple\\s*watch|애플\\s*워치|아이워치|iwatch)\\s*\\(?\\s*(?:series|시리즈)\\s*2(?![0-9])(?![:,][0-9])(?!\\.[0-9a-z])',
        '(?:apple\\s*watch|애플\\s*워치|아이워치|iwatch)\\s*\\(?\\s*2(?![0-9])(?![:,][0-9])(?!\\.[0-9a-z])',
        'apple\\s*watch(?:(?!galaxy|samsung|삼성|갤럭|pixel|픽셀|garmin|amazfit|xiaomi|huawei|oneplus|fitbit|nothing|oura|whoop|mi\\s*band).){0,30}?(?:series|시리즈)\\s*2(?![0-9])(?![:,][0-9])(?!\\.[0-9a-z])'
    ]),
    ("AWS1", [
        '(?:apple\\s*watch|애플\\s*워치|아이워치|iwatch)\\s*\\(?\\s*(?:series|시리즈)\\s*1(?![0-9])(?![:,][0-9])(?!\\.[0-9a-z])',
        '(?:apple\\s*watch|애플\\s*워치|아이워치|iwatch)\\s*\\(?\\s*1(?![0-9])(?![:,][0-9])(?!\\.[0-9a-z])',
        'apple\\s*watch(?:(?!galaxy|samsung|삼성|갤럭|pixel|픽셀|garmin|amazfit|xiaomi|huawei|oneplus|fitbit|nothing|oura|whoop|mi\\s*band).){0,30}?(?:series|시리즈)\\s*1(?![0-9])(?![:,][0-9])(?!\\.[0-9a-z])',
        '(?:apple\\s*watch|애플\\s*워치|아이워치|iwatch)\\s*\\(?\\s*(?:1st|first)\\s*gen'
    ]),
    ("ABMAX2", [
        '(?:air\\s*pods?|에어\\s*팟)\\s*max\\s*2(?![0-9])(?![:,][0-9])(?!\\.[0-9a-z])',
        '(?:air\\s*pods?|에어\\s*팟)\\s*맥스\\s*2(?![0-9])(?![:,][0-9])(?!\\.[0-9a-z])'
    ]),
    ("ABMAX", [
        '(?:air\\s*pods?|에어\\s*팟)\\s*max(?![0-9a-z])(?!\\s*[2-9])',
        '(?:air\\s*pods?|에어\\s*팟)\\s*맥스(?!\\s*[2-9])'
    ]),
    ("ABP3", [
        '(?:air\\s*pods?|에어\\s*팟)\\s*pro\\s*(?:3|3rd|third)(?![0-9])(?![:,][0-9])(?!\\.[0-9a-z])',
        '(?:air\\s*pods?|에어\\s*팟)\\s*3\\s*pro(?![0-9a-z])',
        '(?:air\\s*pods?|에어\\s*팟)\\s*프로\\s*3(?![0-9])(?![:,][0-9])(?!\\.[0-9a-z])'
    ]),
    ("ABP2", [
        '(?:air\\s*pods?|에어\\s*팟)\\s*pro\\s*(?:2|ii|2nd|second)(?![0-9])(?![:,][0-9])(?!\\.[0-9a-z])',
        '(?:air\\s*pods?|에어\\s*팟)\\s*2\\s*pro(?![0-9a-z])',
        '(?:air\\s*pods?|에어\\s*팟)\\s*프로\\s*2(?![0-9])(?![:,][0-9])(?!\\.[0-9a-z])'
    ]),
    ("ABP1", [
        '(?:air\\s*pods?|에어\\s*팟)\\s*pro\\s*(?:1|1st|first)(?![0-9])(?![:,][0-9])(?!\\.[0-9a-z])',
        '(?:air\\s*pods?|에어\\s*팟)\\s*pro(?![0-9a-z])(?!\\s*[2-9])',
        '(?:air\\s*pods?|에어\\s*팟)\\s*프로(?!\\s*[2-9])'
    ]),
    ("AB5", [
        '(?:air\\s*pods?|에어\\s*팟)\\s*5(?![0-9])(?![:,][0-9])(?!\\.[0-9a-z])(?!\\s*(?:pro|max|프로|맥스))'
    ]),
    ("AB4", [
        '(?:air\\s*pods?|에어\\s*팟)\\s*(?:4|4th|fourth)(?![0-9])(?![:,][0-9])(?!\\.[0-9a-z])(?!\\s*(?:pro|max|프로|맥스))'
    ]),
    ("AB3", [
        '(?:air\\s*pods?|에어\\s*팟)\\s*(?:3|3rd|third)(?![0-9])(?![:,][0-9])(?!\\.[0-9a-z])(?!\\s*(?:pro|max|프로|맥스))'
    ]),
    ("AB2", [
        '(?:air\\s*pods?|에어\\s*팟)\\s*(?:2|2nd|second)(?![0-9])(?![:,][0-9])(?!\\.[0-9a-z])(?!\\s*(?:pro|max|프로|맥스))'
    ]),
    ("AB1", [
        '(?:air\\s*pods?|에어\\s*팟)\\s*(?:1|1st|first)(?![0-9])(?![:,][0-9])(?!\\.[0-9a-z])(?!\\s*(?:pro|max|프로|맥스))'
    ]),

    # ═══════════ 경쟁사 · Google 웨어러블 (Pixel Watch / Pixel Buds) ═══════════
    ("PW5", ['pixel\\s*watch\\s*5(?![0-9a-zA-Z])', '픽셀\\s*워치\\s*5(?![0-9a-zA-Z])']),
    ("PW4", ['pixel\\s*watch\\s*4(?![0-9a-zA-Z])', '픽셀\\s*워치\\s*4(?![0-9a-zA-Z])']),
    ("PW3", ['pixel\\s*watch\\s*3(?![0-9a-zA-Z])', '픽셀\\s*워치\\s*3(?![0-9a-zA-Z])']),
    ("PW2", ['pixel\\s*watch\\s*2(?![0-9a-zA-Z])', '픽셀\\s*워치\\s*2(?![0-9a-zA-Z])']),
    ("PW1", [
        'pixel\\s*watch\\s*1(?![0-9a-zA-Z])', 'pixel\\s*watch\\s*\\(?1(?:st)?\\s*gen',
        'pixel\\s*watch(?![0-9a-zA-Z])', '픽셀\\s*워치(?![0-9a-zA-Z])'
    ]),
    ("PBP2", ['pixel\\s*buds\\s*pro\\s*2(?![0-9a-zA-Z])']),
    ("PB2A", ['pixel\\s*buds\\s*2a(?![0-9a-zA-Z])']),
    ("PBP", ['pixel\\s*buds\\s*pro(?![0-9a-zA-Z])', '픽셀\\s*버즈\\s*프로(?![0-9a-zA-Z])']),

    # ═══════════ 경쟁사 · Xiaomi 계열 폰 (Xiaomi/Mi · Redmi · POCO) ═══════════
    ("XM18F", ['xiaomi\\s*18\\s*fold(?![0-9a-zA-Z])', '샤오미\\s*18\\s*폴드']),
    ("XM18P", ['xiaomi\\s*18\\s*pro(?![0-9a-zA-Z])', '샤오미\\s*18\\s*프로']),
    ("XM18", ['xiaomi\\s*18(?![0-9a-zA-Z])', '샤오미\\s*18(?![0-9a-zA-Z])']),
    ("XM17U", ['xiaomi\\s*17\\s*ultra(?![0-9a-zA-Z])', '샤오미\\s*17\\s*울트라']),
    ("XM17PM", ['xiaomi\\s*17\\s*pro\\s*max(?![0-9a-zA-Z])']),
    ("XM17P", ['xiaomi\\s*17\\s*pro(?![0-9a-zA-Z])', '샤오미\\s*17\\s*프로']),
    ("XM17TP", ['xiaomi\\s*17t\\s*pro(?![0-9a-zA-Z])']),
    ("XM17T", ['xiaomi\\s*17t(?![0-9a-zA-Z])', '샤오미\\s*17t(?![0-9a-zA-Z])']),
    ("XM17", ['xiaomi\\s*17(?![0-9a-zA-Z])', '샤오미\\s*17(?![0-9a-zA-Z])']),
    ("XM15U", ['xiaomi\\s*15\\s*ultra(?![0-9a-zA-Z])', '샤오미\\s*15\\s*울트라']),
    ("XM15TP", ['xiaomi\\s*15t\\s*pro(?![0-9a-zA-Z])']),
    ("XM15T", ['xiaomi\\s*15t(?![0-9a-zA-Z])', '샤오미\\s*15t(?![0-9a-zA-Z])']),
    ("XM15", ['xiaomi\\s*15(?![0-9a-zA-Z])', '샤오미\\s*15(?![0-9a-zA-Z])']),
    ("XM14T", ['xiaomi\\s*14t(?![0-9a-zA-Z])', '샤오미\\s*14t(?![0-9a-zA-Z])']),
    ("XM14", ['xiaomi\\s*14(?![0-9a-zA-Z])', '샤오미\\s*14(?![0-9a-zA-Z])']),
    ("XM13", ['xiaomi\\s*13(?![0-9a-zA-Z])', '샤오미\\s*13(?![0-9a-zA-Z])']),
    ("XM12", ['xiaomi\\s*12(?![0-9a-zA-Z])', '샤오미\\s*12(?![0-9a-zA-Z])']),
    ("XM11", [
        'xiaomi\\s*mi\\s*11(?![0-9a-zA-Z])', 'xiaomi\\s*11(?![0-9a-zA-Z])',
        '샤오미\\s*11(?![0-9a-zA-Z])', '\\bmi\\s*11\\s*(?:ultra|pro|lite|le|i|t)(?![0-9a-zA-Z])'
    ]),
    ("RMN17PM", ['redmi\\s*note\\s*17\\s*pro\\s*max(?![0-9a-zA-Z])']),
    ("RMN17P", ['redmi\\s*note\\s*17\\s*pro(?![0-9a-zA-Z])']),
    ("RMN17", ['redmi\\s*note\\s*17(?![0-9a-zA-Z])', '홍미\\s*노트\\s*17', '레드미\\s*노트\\s*17']),
    ("RMN15P", ['redmi\\s*note\\s*15\\s*pro(?![0-9a-zA-Z])']),
    ("RMN15", ['redmi\\s*note\\s*15(?![0-9a-zA-Z])', '홍미\\s*노트\\s*15', '레드미\\s*노트\\s*15']),
    ("RMN14P", ['redmi\\s*note\\s*14\\s*pro(?![0-9a-zA-Z])']),
    ("RMN14", ['redmi\\s*note\\s*14(?![0-9a-zA-Z])', '홍미\\s*노트\\s*14', '레드미\\s*노트\\s*14']),
    ("RMN13", ['redmi\\s*note\\s*13(?![0-9a-zA-Z])', '홍미\\s*노트\\s*13', '레드미\\s*노트\\s*13']),
    ("RMN12", ['redmi\\s*note\\s*12(?![0-9a-zA-Z])', '홍미\\s*노트\\s*12', '레드미\\s*노트\\s*12']),
    ("RMN11", ['redmi\\s*note\\s*11(?![0-9a-zA-Z])', '홍미\\s*노트\\s*11', '레드미\\s*노트\\s*11']),
    ("RMN10", ['redmi\\s*note\\s*10(?![0-9a-zA-Z])', '홍미\\s*노트\\s*10', '레드미\\s*노트\\s*10']),
    ("RMN9", ['redmi\\s*note\\s*9(?![0-9a-zA-Z])', '홍미\\s*노트\\s*9', '레드미\\s*노트\\s*9']),
    ("RMN8", ['redmi\\s*note\\s*8(?![0-9a-zA-Z])', '홍미\\s*노트\\s*8', '레드미\\s*노트\\s*8']),
    ("RMK100P", ['redmi\\s*k100\\s*pro(?![0-9a-zA-Z])']),
    ("RMK100", ['redmi\\s*k100(?![0-9a-zA-Z])', '\\bk100\\s*(?:pro|ultra|max)(?![0-9a-zA-Z])']),
    ("RMK90U", ['redmi\\s*k90\\s*ultra(?![0-9a-zA-Z])']),
    ("RMK90", ['redmi\\s*k90(?![0-9a-zA-Z])', '\\bk90\\s*(?:pro|ultra|max)(?![0-9a-zA-Z])']),
    ("RMT5", ['redmi\\s*turbo\\s*5(?![0-9a-zA-Z])']),
    ("RM15C", ['redmi\\s*15c(?![0-9a-zA-Z])']),
    ("PCF9U", ['poco\\s*f9\\s*ultra(?![0-9a-zA-Z])']),
    ("PCF9P", ['poco\\s*f9\\s*pro(?![0-9a-zA-Z])']),
    ("PCF9", ['poco\\s*f9(?![0-9a-zA-Z])']),
    ("PCF8U", ['poco\\s*f8\\s*ultra(?![0-9a-zA-Z])']),
    ("PCF8P", ['poco\\s*f8\\s*pro(?![0-9a-zA-Z])']),
    ("PCF8", ['poco\\s*f8(?![0-9a-zA-Z])']),
    ("PCF7", ['poco\\s*f7(?![0-9a-zA-Z])']),
    ("PCF6", ['poco\\s*f6(?![0-9a-zA-Z])']),
    ("PCX8PM", ['poco\\s*x8\\s*pro\\s*max(?![0-9a-zA-Z])']),
    ("PCX8P", ['poco\\s*x8\\s*pro(?![0-9a-zA-Z])']),
    ("PCX8", ['poco\\s*x8(?![0-9a-zA-Z])']),
    ("PCX7P", ['poco\\s*x7\\s*pro(?![0-9a-zA-Z])']),
    ("PCX7", ['poco\\s*x7(?![0-9a-zA-Z])']),
    ("PCX6", ['poco\\s*x6(?![0-9a-zA-Z])']),
    ("PCX3", ['poco\\s*x3(?![0-9a-zA-Z])']),
    ("PCM8", ['poco\\s*m8(?![0-9a-zA-Z])']),

    # ═══════════ 경쟁사 · 중국 2군 폰 (vivo · Oppo · OnePlus · realme · Honor · Huawei) ═══════════
    ("VVXF6", [
        "(?:vivo|비보)[\\s\\-'’]{0,3}x\\s*fold\\s*6(?![0-9a-zA-Z])",
        "(?:vivo|비보)[\\s\\-'’]{0,3}x\\s*폴드\\s*6(?![0-9a-zA-Z])"
    ]),
    ("VVXF5", [
        "(?:vivo|비보)[\\s\\-'’]{0,3}x\\s*fold\\s*5(?![0-9a-zA-Z])",
        "(?:vivo|비보)[\\s\\-'’]{0,3}x\\s*폴드\\s*5(?![0-9a-zA-Z])"
    ]),
    ("VVXF", [
        "(?:vivo|비보)[\\s\\-'’]{0,3}x\\s*fold(?![0-9a-zA-Z])",
        "(?:vivo|비보)[\\s\\-'’]{0,3}x\\s*폴드(?![0-9a-zA-Z])"
    ]),
    ("OOFN7", ['\\bfind\\s*n\\s*7(?![0-9a-zA-Z])', '파인드\\s*n\\s*7(?![0-9a-zA-Z])']),
    ("OOFN6", ['\\bfind\\s*n\\s*6(?![0-9a-zA-Z])', '파인드\\s*n\\s*6(?![0-9a-zA-Z])']),
    ("OOFN5", ['\\bfind\\s*n\\s*5(?![0-9a-zA-Z])', '파인드\\s*n\\s*5(?![0-9a-zA-Z])']),
    ("OOFN3", ['\\bfind\\s*n\\s*3(?![0-9a-zA-Z])']),
    ("OOFN2", ['\\bfind\\s*n\\s*2(?![0-9a-zA-Z])']),
    ("OOFX10", ['\\bfind\\s*x\\s*10(?![0-9a-zA-Z])', '파인드\\s*x\\s*10(?![0-9a-zA-Z])']),
    ("OOFX9", [
        '\\bfind\\s*x\\s*9\\s*s(?![0-9a-zA-Z])', '\\bfind\\s*x\\s*9(?![0-9a-zA-Z])',
        '파인드\\s*x\\s*9(?![0-9a-zA-Z])'
    ]),
    ("OOFX8", [
        '\\bfind\\s*x\\s*8\\s*s(?![0-9a-zA-Z])', '\\bfind\\s*x\\s*8(?![0-9a-zA-Z])',
        '파인드\\s*x\\s*8(?![0-9a-zA-Z])'
    ]),
    ("OOFX7", ['\\bfind\\s*x\\s*7(?![0-9a-zA-Z])']),
    ("OOFX6", ['\\bfind\\s*x\\s*6(?![0-9a-zA-Z])']),
    ("OOFX5", ['\\bfind\\s*x\\s*5(?![0-9a-zA-Z])']),
    ("OOFX3", ['\\bfind\\s*x\\s*3(?![0-9a-zA-Z])']),
    ("OOFX2", ['\\bfind\\s*x\\s*2(?![0-9a-zA-Z])']),
    ("OORN16", ['(?<![\\w\\-])reno\\s*16(?![0-9a-zA-Z])', '레노\\s*16(?![0-9a-zA-Z])']),
    ("OORN15", ['(?<![\\w\\-])reno\\s*15(?![0-9a-zA-Z])', '레노\\s*15(?![0-9a-zA-Z])']),
    ("OORN14", ['(?<![\\w\\-])reno\\s*14(?![0-9a-zA-Z])', '레노\\s*14(?![0-9a-zA-Z])']),
    ("OORN13", ['(?<![\\w\\-])reno\\s*13(?![0-9a-zA-Z])']),
    ("VVX500", ["(?:vivo|비보)[\\s\\-'’]{0,3}x\\s*500(?![0-9a-zA-Z])"]),
    ("VVX300", ["(?:vivo|비보)[\\s\\-'’]{0,3}x\\s*300(?![0-9a-zA-Z])"]),
    ("VVX200", ["(?:vivo|비보)[\\s\\-'’]{0,3}x\\s*200(?![0-9a-zA-Z])"]),
    ("VVX100", ["(?:vivo|비보)[\\s\\-'’]{0,3}x\\s*100(?![0-9a-zA-Z])"]),
    ("VVV70", ["(?:vivo|비보)[\\s\\-'’]{0,3}v\\s*70(?![0-9a-zA-Z])"]),
    ("VVIQ16", ['\\biqoo\\s*16(?![0-9a-zA-Z])']),
    ("VVIQ15", ['\\biqoo\\s*15(?![0-9a-zA-Z])']),
    ("VVIQ13", ['\\biqoo\\s*13(?![0-9a-zA-Z])']),
    ("OPOPEN", ["(?:one\\s?plus|원\\s?플러스|원플|一加)[\\s\\-'’]{0,3}open(?![0-9a-zA-Z])"]),
    ("OPNORD", [
        "(?:one\\s?plus|원\\s?플러스|원플|一加)[\\s\\-'’]{0,3}nord(?![0-9a-zA-Z])",
        '(?<![\\w\\-])nord\\s*ce\\s*\\d?(?![0-9a-zA-Z])',
        '(?<![\\w\\-])nord\\s*[2-6](?![0-9a-zA-Z])'
    ]),
    ("OP16", ["(?:one\\s?plus|원\\s?플러스|원플|一加)[\\s\\-'’]{0,3}16r?(?![0-9a-zA-Z])"]),
    ("OP15", ["(?:one\\s?plus|원\\s?플러스|원플|一加)[\\s\\-'’]{0,3}15[rst]?(?![0-9a-zA-Z])"]),
    ("OP13", ["(?:one\\s?plus|원\\s?플러스|원플|一加)[\\s\\-'’]{0,3}13[rst]?(?![0-9a-zA-Z])"]),
    ("OP12", ["(?:one\\s?plus|원\\s?플러스|원플|一加)[\\s\\-'’]{0,3}12[rst]?(?![0-9a-zA-Z])"]),
    ("OP11", ["(?:one\\s?plus|원\\s?플러스|원플|一加)[\\s\\-'’]{0,3}11[rst]?(?![0-9a-zA-Z])"]),
    ("OP10", ["(?:one\\s?plus|원\\s?플러스|원플|一加)[\\s\\-'’]{0,3}10[rt]?(?![0-9a-zA-Z])"]),
    ("OP9", ["(?:one\\s?plus|원\\s?플러스|원플|一加)[\\s\\-'’]{0,3}9r?t?(?![0-9a-zA-Z])"]),
    ("OP8", ["(?:one\\s?plus|원\\s?플러스|원플|一加)[\\s\\-'’]{0,3}8t?(?![0-9a-zA-Z])"]),
    ("OP7", ["(?:one\\s?plus|원\\s?플러스|원플|一加)[\\s\\-'’]{0,3}7t?(?![0-9a-zA-Z])"]),
    ("OP6", ["(?:one\\s?plus|원\\s?플러스|원플|一加)[\\s\\-'’]{0,3}6t?(?![0-9a-zA-Z])"]),
    ("OP5", ["(?:one\\s?plus|원\\s?플러스|원플|一加)[\\s\\-'’]{0,3}5t?(?![0-9a-zA-Z])"]),
    ("OP3", ["(?:one\\s?plus|원\\s?플러스|원플|一加)[\\s\\-'’]{0,3}3t?(?![0-9a-zA-Z])"]),
    ("RLGT8", ["(?:realme|리얼미|真我)[\\s\\-'’]{0,3}gt\\s*8t?(?![0-9a-zA-Z])"]),
    ("RLGT7", ["(?:realme|리얼미|真我)[\\s\\-'’]{0,3}gt\\s*7t?(?![0-9a-zA-Z])"]),
    ("RLGT6", ["(?:realme|리얼미|真我)[\\s\\-'’]{0,3}gt\\s*6t?(?![0-9a-zA-Z])"]),
    ("RLP4", ["(?:realme|리얼미|真我)[\\s\\-'’]{0,3}p\\s*4[a-z]?(?![0-9a-zA-Z])"]),
    ("RL16", ["(?:realme|리얼미|真我)[\\s\\-'’]{0,3}16t?(?![0-9a-zA-Z])"]),
    ("RL15", ["(?:realme|리얼미|真我)[\\s\\-'’]{0,3}15(?![0-9a-zA-Z])"]),
    ("RL14", ["(?:realme|리얼미|真我)[\\s\\-'’]{0,3}14(?![0-9a-zA-Z])"]),
    ("RL12", ["(?:realme|리얼미|真我)[\\s\\-'’]{0,3}12(?![0-9a-zA-Z])"]),
    ("HOMV6", [
        '(?<!red\\s)(?<!red-)\\bmagic\\s*v\\s*6(?![0-9a-zA-Z])', '매직\\s*v\\s*6(?![0-9a-zA-Z])'
    ]),
    ("HOMV5", [
        '(?<!red\\s)(?<!red-)\\bmagic\\s*v\\s*5(?![0-9a-zA-Z])', '매직\\s*v\\s*5(?![0-9a-zA-Z])'
    ]),
    ("HOMV3", ['(?<!red\\s)(?<!red-)\\bmagic\\s*v\\s*3(?![0-9a-zA-Z])']),
    ("HOMV2", ['(?<!red\\s)(?<!red-)\\bmagic\\s*v\\s*2(?![0-9a-zA-Z])']),
    ("HOM9", ['(?<!red\\s)(?<!red-)\\bmagic\\s*9(?![0-9a-zA-Z])', '매직\\s*9(?![0-9a-zA-Z])']),
    ("HOM8", [
        '(?<!red\\s)(?<!red-)\\bmagic\\s*8(?!\\s*ball)(?![0-9a-zA-Z])',
        '매직\\s*8(?![0-9a-zA-Z])'
    ]),
    ("HOM7", ['(?<!red\\s)(?<!red-)\\bmagic\\s*7(?![0-9a-zA-Z])']),
    ("HOM6", ['(?<!red\\s)(?<!red-)\\bmagic\\s*6(?![0-9a-zA-Z])']),
    ("HOM5", ['(?<!red\\s)(?<!red-)\\bmagic\\s*5(?![0-9a-zA-Z])']),
    ("HO600", ["(?:honou?r|아너|荣耀)[\\s\\-'’]{0,3}600(?![0-9a-zA-Z])"]),
    ("HO500", ["(?:honou?r|아너|荣耀)[\\s\\-'’]{0,3}500(?![0-9a-zA-Z])"]),
    ("HO400", ["(?:honou?r|아너|荣耀)[\\s\\-'’]{0,3}400(?![0-9a-zA-Z])"]),
    ("HO300", ["(?:honou?r|아너|荣耀)[\\s\\-'’]{0,3}300(?![0-9a-zA-Z])"]),
    ("HO200", ["(?:honou?r|아너|荣耀)[\\s\\-'’]{0,3}200(?![0-9a-zA-Z])"]),
    ("HWPUX", ['\\bpura\\s*x(?![0-9a-zA-Z])', '퓨라\\s*x(?![0-9a-zA-Z])']),
    ("HWPU90", ['\\bpura\\s*90s?(?![0-9a-zA-Z])', '퓨라\\s*90(?![0-9a-zA-Z])']),
    ("HWPU80", ['\\bpura\\s*80(?![0-9a-zA-Z])', '퓨라\\s*80(?![0-9a-zA-Z])']),
    ("HWPU70", ['\\bpura\\s*70(?![0-9a-zA-Z])', '퓨라\\s*70(?![0-9a-zA-Z])']),
    ("HWMXT2", ['\\bmate\\s*xt\\s*2(?![0-9a-zA-Z])', '메이트\\s*xt\\s*2(?![0-9a-zA-Z])']),
    ("HWMXT", ['\\bmate\\s*xt(?![0-9a-zA-Z])', '메이트\\s*xt(?![0-9a-zA-Z])']),
    ("HWMX7", ['\\bmate\\s*x\\s*7(?![0-9a-zA-Z])']),
    ("HWMX6", ['\\bmate\\s*x\\s*6(?![0-9a-zA-Z])']),
    ("HWMX5", ['\\bmate\\s*x\\s*5(?![0-9a-zA-Z])']),
    ("HWMX2", ['\\bmate\\s*x\\s*2(?![0-9a-zA-Z])', '메이트\\s*x\\s*2(?![0-9a-zA-Z])']),
    ("HWM90", ['\\bmate\\s*90(?![0-9a-zA-Z])']),
    ("HWM80", ['\\bmate\\s*80(?![0-9a-zA-Z])']),
    ("HWM70", ['\\bmate\\s*70(?![0-9a-zA-Z])']),
    ("HWM60", ['\\bmate\\s*60(?![0-9a-zA-Z])']),
    ("HWM50", ['\\bmate\\s*50(?![0-9a-zA-Z])']),
    ("HWM40", ['\\bmate\\s*40(?![0-9a-zA-Z])']),
    ("HWM30", ['\\bmate\\s*30(?![0-9a-zA-Z])']),
    ("HWM20", ['\\bmate\\s*20(?![0-9a-zA-Z])']),
    ("HWNV16", ['\\bnova\\s*16s?(?![0-9a-zA-Z])']),
    ("HWNV15", ['\\bnova\\s*15(?![0-9a-zA-Z])']),
    ("HWP50", [
        "(?:huawei|huwai|hauwei|화웨이|华为)[\\s\\-'’]{0,4}(?:s\\s*)?p\\s*50(?![0-9a-zA-Z])"
    ]),
    ("HWP40", [
        "(?:huawei|huwai|hauwei|화웨이|华为)[\\s\\-'’]{0,4}(?:s\\s*)?p\\s*40(?![0-9a-zA-Z])"
    ]),
    ("HWP30", [
        "(?:huawei|huwai|hauwei|화웨이|华为)[\\s\\-'’]{0,4}(?:s\\s*)?p\\s*30(?![0-9a-zA-Z])"
    ]),
    ("HWP20", [
        "(?:huawei|huwai|hauwei|화웨이|华为)[\\s\\-'’]{0,4}(?:s\\s*)?p\\s*20(?![0-9a-zA-Z])"
    ]),

    # ═══════════ 경쟁사 · 중화권 웨어러블 (Huawei · OnePlus · Nothing · Xiaomi/Amazfit) ═══════════
    ("HWGT7P", ['(?:huawei\\s*)?watch\\s*gt\\s*7\\s*pro(?![0-9a-zA-Z])']),
    ("HWGT7", ['(?:huawei\\s*)?watch\\s*gt\\s*7(?![0-9a-zA-Z])']),
    ("HWGT6P", ['(?:huawei\\s*)?watch\\s*gt\\s*6\\s*pro(?![0-9a-zA-Z])']),
    ("HWGT6", ['(?:huawei\\s*)?watch\\s*gt\\s*6(?![0-9a-zA-Z])']),
    ("HWGTR2", ['(?:huawei\\s*)?watch\\s*gt\\s*runner(?![0-9a-zA-Z])']),
    ("HWFIT5P", ['(?<!galaxy\\s)(?:huawei\\s*)?watch\\s*fit\\s*5\\s*pro(?![0-9a-zA-Z])']),
    ("HWFIT5", ['(?<!galaxy\\s)(?:huawei\\s*)?watch\\s*fit\\s*5(?![0-9a-zA-Z])']),
    ("HWFIT4P", ['(?<!galaxy\\s)(?:huawei\\s*)?watch\\s*fit\\s*4\\s*pro(?![0-9a-zA-Z])']),
    ("HWFIT4", ['(?<!galaxy\\s)(?:huawei\\s*)?watch\\s*fit\\s*4(?![0-9a-zA-Z])']),
    ("HWD3", ['(?:huawei\\s*)?watch\\s*d3(?![0-9a-zA-Z])']),
    ("HWD2", ['(?:huawei\\s*)?watch\\s*d2(?![0-9a-zA-Z])']),
    ("HWULT", ['(?:huawei\\s*)?watch\\s*ultimate(?![0-9a-zA-Z])']),
    ("HW6", ['huawei\\s*watch\\s*6(?![0-9a-zA-Z])', '화웨이\\s*워치\\s*6(?![0-9a-zA-Z])']),
    ("HW5", ['huawei\\s*watch\\s*5(?![0-9a-zA-Z])', '화웨이\\s*워치\\s*5(?![0-9a-zA-Z])']),
    ("HWB11", ['huawei\\s*band\\s*11(?![0-9a-zA-Z])', '화웨이\\s*밴드\\s*11(?![0-9a-zA-Z])']),
    ("HWB10", ['huawei\\s*band\\s*10(?![0-9a-zA-Z])', '화웨이\\s*밴드\\s*10(?![0-9a-zA-Z])']),
    ("HWFBP5", ['freebuds\\s*pro\\s*5(?![0-9a-zA-Z])', 'freebuds\\s*5\\s*pro(?![0-9a-zA-Z])']),
    ("HWFBP4", ['freebuds\\s*pro\\s*4(?![0-9a-zA-Z])']),
    ("HWFBNEO", ['freebuds\\s*neo(?![0-9a-zA-Z])']),
    ("HWFBSE", ['freebuds\\s*se(?![0-9a-zA-Z])']),
    ("HWFB7", ['freebuds\\s*7(?![0-9a-zA-Z])']),
    ("OPW4", ['one\\s*plus\\s*watch\\s*4(?![0-9a-zA-Z])', '원플러스\\s*워치\\s*4(?![0-9a-zA-Z])']),
    ("OPW3", ['one\\s*plus\\s*watch\\s*3(?![0-9a-zA-Z])', '원플러스\\s*워치\\s*3(?![0-9a-zA-Z])']),
    ("OPW2R", ['one\\s*plus\\s*watch\\s*2r(?![0-9a-zA-Z])']),
    ("OPW2", ['one\\s*plus\\s*watch\\s*2(?![0-9a-zA-Z])', '원플러스\\s*워치\\s*2(?![0-9a-zA-Z])']),
    ("OPBP3", ['one\\s*plus\\s*buds\\s*pro\\s*3(?![0-9a-zA-Z])']),
    ("OPB4", ['one\\s*plus\\s*buds\\s*4(?![0-9a-zA-Z])', '원플러스\\s*버즈\\s*4(?![0-9a-zA-Z])']),
    ("OPB3", ['one\\s*plus\\s*buds\\s*3(?![0-9a-zA-Z])', '원플러스\\s*버즈\\s*3(?![0-9a-zA-Z])']),
    ("OPBN", [
        '(?:one\\s*plus\\s*)?nord\\s*buds(?![0-9a-zA-Z])',
        'one\\s*plus\\s*buds\\s*nord(?![0-9a-zA-Z])'
    ]),
    ("NTE3A", ['nothing\\s*ear\\s*\\(?\\s*3a\\s*\\)?(?![0-9a-zA-Z])']),
    ("NTEA", ['nothing\\s*ear\\s*\\(\\s*a\\s*\\)']),
    ("NTE", [
        'nothing\\s*ear(?![0-9a-zA-Z])', '낫싱\\s*이어(?![0-9a-zA-Z])', '나띵\\s*이어(?![0-9a-zA-Z])'
    ]),
    ("XMB11", [
        '(?:xiaomi|redmi|mi)\\s*(?:smart\\s*)?band\\s*11(?![0-9a-zA-Z])',
        '샤오미\\s*밴드\\s*11(?![0-9a-zA-Z])', '(?<![가-힣])미밴드\\s*11(?![0-9a-zA-Z])'
    ]),
    ("XMB10", [
        '(?:xiaomi|redmi|mi)\\s*(?:smart\\s*)?band\\s*10(?![0-9a-zA-Z])',
        '샤오미\\s*밴드\\s*10(?![0-9a-zA-Z])', '(?<![가-힣])미밴드\\s*10(?![0-9a-zA-Z])'
    ]),
    ("XMB9", [
        '(?:xiaomi|redmi|mi)\\s*(?:smart\\s*)?band\\s*9(?![0-9a-zA-Z])',
        '샤오미\\s*밴드\\s*9(?![0-9a-zA-Z])', '(?<![가-힣])미밴드\\s*9(?![0-9a-zA-Z])'
    ]),
    ("XMB8", [
        '(?:xiaomi|redmi|mi)\\s*(?:smart\\s*)?band\\s*8(?![0-9a-zA-Z])',
        '샤오미\\s*밴드\\s*8(?![0-9a-zA-Z])', '(?<![가-힣])미밴드\\s*8(?![0-9a-zA-Z])'
    ]),
    ("XMBAND", [
        'xiaomi\\s*(?:smart\\s*)?band(?![0-9a-zA-Z])', '\\bmi\\s*band(?![0-9a-zA-Z])',
        '샤오미\\s*(?:스마트\\s*)?밴드(?![0-9a-zA-Z])', '(?<![가-힣])미밴드(?![0-9a-zA-Z])'
    ]),
    ("XMWS5", ['xiaomi\\s*watch\\s*s5(?![0-9a-zA-Z])']),
    ("XMW5", ['xiaomi\\s*watch\\s*5(?![0-9a-zA-Z])', '샤오미\\s*워치\\s*5(?![0-9a-zA-Z])']),
    ("RDW6", ['redmi\\s*watch\\s*6(?![0-9a-zA-Z])', '레드미\\s*워치\\s*6(?![0-9a-zA-Z])']),
    ("RDW5", ['redmi\\s*watch\\s*5(?![0-9a-zA-Z])', '레드미\\s*워치\\s*5(?![0-9a-zA-Z])']),
    ("RDW4", ['redmi\\s*watch\\s*4(?![0-9a-zA-Z])', '레드미\\s*워치\\s*4(?![0-9a-zA-Z])']),
    ("AZTR3", ['amazfit\\s*t[\\s\\-]*rex\\s*3(?![0-9a-zA-Z])']),
    ("AZBIP6", ['amazfit\\s*bip\\s*6(?![0-9a-zA-Z])']),
    ("AZACTM", ['amazfit\\s*active\\s*max(?![0-9a-zA-Z])']),
    ("AZBAL3", ['amazfit\\s*balance\\s*3(?![0-9a-zA-Z])']),
    ("AZBAL2", ['amazfit\\s*balance\\s*2(?![0-9a-zA-Z])']),
    ("AZBAL", ['amazfit\\s*balance(?![0-9a-zA-Z])']),

    # ═══════════ 경쟁사 · 기타 폰 (Nothing · Motorola · Sony Xperia · Nokia · Asus) ═══════════
    ("NTP4B", ['nothing\\s*phone\\s*\\(?\\s*4\\s*b\\s*\\)?(?![0-9a-zA-Z])']),
    ("NTP4A", ['nothing\\s*phone\\s*\\(?\\s*4\\s*a\\s*\\)?(?![0-9a-zA-Z])']),
    ("NTP3A", ['nothing\\s*phone\\s*\\(?\\s*3\\s*a\\s*\\)?(?![0-9a-zA-Z])']),
    ("NTP3", ['nothing\\s*phone\\s*\\(?\\s*3\\s*\\)?(?![0-9a-zA-Z])']),
    ("NTP2A", ['nothing\\s*phone\\s*\\(?\\s*2\\s*a\\s*\\)?(?![0-9a-zA-Z])']),
    ("NTP2", ['nothing\\s*phone\\s*\\(?\\s*2\\s*\\)?(?![0-9a-zA-Z])']),
    ("NTP1", ['nothing\\s*phone\\s*\\(?\\s*1\\s*\\)?(?![0-9a-zA-Z])']),
    ("NTCMF2P", ['cmf\\s*phone\\s*2(?![0-9a-zA-Z])']),
    ("NTCMF1", ['cmf\\s*phone\\s*1(?![0-9a-zA-Z])']),
    ("NTEAR", ['nothing\\s*ear(?![0-9a-zA-Z])']),
    ("NTP", ['nothing\\s*phone(?![0-9a-zA-Z:\\-])', 'nothing\\s*os(?![0-9a-zA-Z])', '낫싱\\s*폰']),
    ("MTRZFOLD", ['\\brazr\\s*fold(?![0-9a-zA-Z])', '레이저\\s*폴드']),
    ("MTRZ70U", ['\\brazr\\s*70\\s*ultra(?![0-9a-zA-Z])']),
    ("MTRZ70", ['\\brazr\\s*70(?![0-9a-zA-Z])']),
    ("MTRZ60U", ['\\brazr\\s*60\\s*ultra(?![0-9a-zA-Z])']),
    ("MTRZ60", ['\\brazr\\s*60(?![0-9a-zA-Z])']),
    ("MTRZ50U", ['\\brazr\\s*50\\s*ultra(?![0-9a-zA-Z])']),
    ("MTRZ40U", ['\\brazr\\s*40\\s*ultra(?![0-9a-zA-Z])']),
    ("MTRZR", ['\\brazr(?![0-9a-zA-Z])', '모토\\s*레이저', '레이저\\s*폰']),
    ("MTEDGE70", [
        'moto(?:rola)?\\s*edge\\s*70(?![0-9a-zA-Z])',
        '(?<![0-9]\\s)\\bedge\\s*70(?![0-9a-zA-Z])'
    ]),
    ("MTEDGE60", [
        'moto(?:rola)?\\s*edge\\s*60(?![0-9a-zA-Z])',
        '(?<![0-9]\\s)\\bedge\\s*60(?![0-9a-zA-Z])'
    ]),
    ("MTEDGE50", [
        'moto(?:rola)?\\s*edge\\s*50(?![0-9a-zA-Z])',
        '(?<![0-9]\\s)\\bedge\\s*50(?![0-9a-zA-Z])'
    ]),
    ("MTEDGE40", [
        'moto(?:rola)?\\s*edge\\s*40(?![0-9a-zA-Z])',
        '(?<![0-9]\\s)\\bedge\\s*40(?![0-9a-zA-Z])'
    ]),
    ("MTEDGE30", [
        'moto(?:rola)?\\s*edge\\s*30(?![0-9a-zA-Z])',
        '(?<![0-9]\\s)\\bedge\\s*30(?![0-9a-zA-Z])'
    ]),
    ("MTEDGE20", [
        'moto(?:rola)?\\s*edge\\s*20(?![0-9a-zA-Z])',
        '(?<![0-9]\\s)\\bedge\\s*20(?![0-9a-zA-Z])'
    ]),
    ("MTEDGE", ['moto(?:rola)?\\s*edge(?![0-9a-zA-Z])', '모토로라\\s*엣지']),
    ("MTG86", ['\\bmoto\\s*g\\s*86(?![0-9a-zA-Z])']),
    ("MTGSTYLU", ['\\bmoto\\s*g\\s*stylus(?![0-9a-zA-Z])']),
    ("MTGPOWER", ['\\bmoto\\s*g\\s*power(?![0-9a-zA-Z])']),
    ("MTG", ['\\bmoto\\s*g\\s*\\d{0,3}(?![0-9a-zA-Z])', '모토\\s*g(?![0-9a-zA-Z])']),
    ("MTX", ['\\bmoto\\s*x\\s*\\d{0,2}(?![0-9a-zA-Z])']),
    ("MTZ", ['\\bmoto\\s*z\\s*\\d{0,2}(?![0-9a-zA-Z])']),
    ("MTE", ['\\bmoto\\s*e\\s*\\d{0,2}(?![0-9a-zA-Z])']),
    ("SNX1M8", ['xperia\\s*1\\s*viii(?![0-9a-zA-Z])']),
    ("SNX1M7", ['xperia\\s*1\\s*vii(?![0-9a-zA-Z])']),
    ("SNX1M6", ['xperia\\s*1\\s*vi(?![0-9a-zA-Z])']),
    ("SNX1M5", ['xperia\\s*1\\s*v(?![0-9a-zA-Z])']),
    ("SNX1M4", ['xperia\\s*1\\s*iv(?![0-9a-zA-Z])']),
    ("SNX1M3", ['xperia\\s*1\\s*iii(?![0-9a-zA-Z])']),
    ("SNX1M2", ['xperia\\s*1\\s*ii(?![0-9a-zA-Z])']),
    ("SNX1", ['xperia\\s*1(?![0-9a-zA-Z])', '엑스페리아\\s*1(?![0-9a-zA-Z])']),
    ("SNX10M8", ['xperia\\s*10\\s*viii(?![0-9a-zA-Z])']),
    ("SNX10M7", ['xperia\\s*10\\s*vii(?![0-9a-zA-Z])']),
    ("SNX10", ['xperia\\s*10(?![0-9a-zA-Z])']),
    ("SNX5", ['xperia\\s*5(?![0-9a-zA-Z])']),
    ("SNXXZ", ['xperia\\s*xz\\s*\\d?(?![0-9a-zA-Z])']),
    ("SNXZ", ['xperia\\s*z\\s*[1-5](?![0-9a-zA-Z])', 'xperia\\s*z(?![0-9a-zA-Z])']),
    ("SNXPERIA", ['\\bxperia(?![0-9a-zA-Z])', '엑스페리아']),
    ("NK3310", ['nokia\\s*3310(?![0-9a-zA-Z])']),
    ("NK3210", ['nokia\\s*3210(?![0-9a-zA-Z])']),
    ("NKN900", ['nokia\\s*n\\s*900(?![0-9a-zA-Z])']),
    ("NKN95", ['nokia\\s*n\\s*95(?![0-9a-zA-Z])']),
    ("NKN9", ['nokia\\s*n\\s*9(?![0-9a-zA-Z])']),
    ("NKN8", ['nokia\\s*n\\s*8(?![0-9a-zA-Z])']),
    ("NK9PV", ['nokia\\s*9\\s*pureview(?![0-9a-zA-Z])', 'nokia\\s*9(?![0-9.])(?![0-9a-zA-Z])']),
    ("NK1100", ['nokia\\s*1100(?![0-9a-zA-Z])']),
    ("NK808", ['nokia\\s*808(?![0-9a-zA-Z])']),
    ("NK61", ['nokia\\s*6\\.1(?![0-9a-zA-Z])']),
    ("NK8_17", ['nokia\\s*8(?![0-9.])(?![0-9a-zA-Z])']),
    ("NK7P_18", ['nokia\\s*7\\s*plus(?![0-9a-zA-Z])', 'nokia\\s*7(?![0-9.])(?![0-9a-zA-Z])']),
    ("NK6_17", ['nokia\\s*6(?![0-9.])(?![0-9a-zA-Z])']),
    ("NKLUMIA", ['\\blumia(?![0-9a-zA-Z])', '노키아\\s*루미아']),
    ("ASROG9", ['rog\\s*phone\\s*9(?![0-9a-zA-Z])']),
    ("ASROG8", ['rog\\s*phone\\s*8(?![0-9a-zA-Z])']),
    ("ASROG5", ['rog\\s*phone\\s*5(?![0-9a-zA-Z])']),
    ("ASROG3", ['rog\\s*phone\\s*3(?![0-9a-zA-Z])']),
    ("ASROG2", ['rog\\s*phone\\s*2(?![0-9a-zA-Z])']),
    ("ASROG", ['rog\\s*phone(?![0-9a-zA-Z])', '로그\\s*폰']),
    ("ASZEN12", ['zen\\s*fone\\s*12(?![0-9a-zA-Z])']),
    ("ASZEN10", ['zen\\s*fone\\s*10(?![0-9a-zA-Z])']),
    ("ASZEN9", ['zen\\s*fone\\s*9(?![0-9a-zA-Z])']),
    ("ASZEN8", ['zen\\s*fone\\s*8(?![0-9a-zA-Z])']),
    ("ASZEN6", ['zen\\s*fone\\s*6(?![0-9a-zA-Z])']),
    ("ASZEN2", ['zen\\s*fone\\s*2(?![0-9a-zA-Z])']),
    ("ASZENMAX", ['zen\\s*fone\\s*max(?![0-9a-zA-Z])']),
    ("ASZEN", ['zen\\s*fone(?![0-9a-zA-Z])', '젠폰']),

    # ═══════════ 경쟁사 · 피트니스/오디오 (Garmin · Fitbit · Bose · Sony 오디오 · Jabra) ═══════════
    ("GMNFX8", ['\\bfenix\\s*8(?![0-9a-zA-Z])', '가민\\s*피닉스\\s*8(?![0-9a-zA-Z])']),
    ("GMNFX7", ['\\bfenix\\s*7(?![0-9a-zA-Z])', '가민\\s*피닉스\\s*7(?![0-9a-zA-Z])']),
    ("GMNFX6", ['\\bfenix\\s*6(?![0-9a-zA-Z])']),
    ("GMNFX3", ['\\bfenix\\s*3(?![0-9a-zA-Z])']),
    ("GMNFR970", ['\\bforerunner\\s*970(?![0-9a-zA-Z])', '\\bfr\\s*970(?![0-9a-zA-Z])']),
    ("GMNFR965", ['\\bforerunner\\s*965(?![0-9a-zA-Z])', '\\bfr\\s*965(?![0-9a-zA-Z])']),
    ("GMNFR955", ['\\bforerunner\\s*955(?![0-9a-zA-Z])', '\\bfr\\s*955(?![0-9a-zA-Z])']),
    ("GMNFR570", ['\\bforerunner\\s*570(?![0-9a-zA-Z])', '\\bfr\\s*570(?![0-9a-zA-Z])']),
    ("GMNFR265", ['\\bforerunner\\s*265(?![0-9a-zA-Z])', '\\bfr\\s*265(?![0-9a-zA-Z])']),
    ("GMNFR165", ['\\bforerunner\\s*165(?![0-9a-zA-Z])', '\\bfr\\s*165(?![0-9a-zA-Z])']),
    ("GMNFR55", ['\\bforerunner\\s*55(?![0-9a-zA-Z])', '\\bfr\\s*55(?![0-9a-zA-Z])']),
    ("GMNV4", [
        "(?:(?<=garmin )|(?<=garmin's )|(?<=garmin’s )|(?<=garmin-)|(?<=가민 )|(?<=가민))venu\\s*4s?(?![0-9a-zA-Z])",
        '\\bvenu\\s*4s?(?![0-9a-zA-Z])(?=[^\\n]{0,80}(?:garmin|가민|fenix|forerunner|v[ií]voactive|epix|instinct))',
        '가민\\s*베뉴\\s*4(?![0-9a-zA-Z])'
    ]),
    ("GMNV3", [
        "(?:(?<=garmin )|(?<=garmin's )|(?<=garmin’s )|(?<=garmin-)|(?<=가민 )|(?<=가민))venu\\s*3s?(?![0-9a-zA-Z])",
        '\\bvenu\\s*3s?(?![0-9a-zA-Z])(?=[^\\n]{0,80}(?:garmin|가민|fenix|forerunner|v[ií]voactive|epix|instinct))',
        '가민\\s*베뉴\\s*3(?![0-9a-zA-Z])'
    ]),
    ("GMNV2", [
        "(?:(?<=garmin )|(?<=garmin's )|(?<=garmin’s )|(?<=garmin-)|(?<=가민 )|(?<=가민))venu\\s*2s?(?![0-9a-zA-Z])",
        '\\bvenu\\s*2s?(?![0-9a-zA-Z])(?=[^\\n]{0,80}(?:garmin|가민|fenix|forerunner|v[ií]voactive|epix|instinct))',
        '가민\\s*베뉴\\s*2(?![0-9a-zA-Z])'
    ]),
    ("FTBC6", [
        "(?:(?<=fitbit )|(?<=fitbit's )|(?<=fitbit’s )|(?<=fitbit-)|(?<=fit bit )|(?<=핏빗 )|(?<=핏빗))charge\\s*6(?![0-9a-zA-Z])",
        '\\bcharge\\s*6(?![0-9a-zA-Z])(?=[^\\n]{0,40}(?:fit ?bit|핏빗))',
        '핏빗\\s*차지\\s*6(?![0-9a-zA-Z])'
    ]),
    ("FTBC5", [
        "(?:(?<=fitbit )|(?<=fitbit's )|(?<=fitbit’s )|(?<=fitbit-)|(?<=fit bit )|(?<=핏빗 )|(?<=핏빗))charge\\s*5(?![0-9a-zA-Z])",
        '\\bcharge\\s*5(?![0-9a-zA-Z])(?=[^\\n]{0,40}(?:fit ?bit|핏빗))',
        '핏빗\\s*차지\\s*5(?![0-9a-zA-Z])'
    ]),
    ("FTBS2", [
        "(?:(?<=fitbit )|(?<=fitbit's )|(?<=fitbit’s )|(?<=fitbit-)|(?<=fit bit )|(?<=핏빗 )|(?<=핏빗))sense\\s*2(?![0-9a-zA-Z])",
        '\\bsense\\s*2(?![0-9a-zA-Z])(?=[^\\n]{0,40}(?:fit ?bit|핏빗))',
        '핏빗\\s*센스\\s*2(?![0-9a-zA-Z])'
    ]),
    ("FTBS1", [
        "(?:(?<=fitbit )|(?<=fitbit's )|(?<=fitbit’s )|(?<=fitbit-)|(?<=fit bit )|(?<=핏빗 )|(?<=핏빗))sense(?![0-9a-zA-Z])",
        '\\bsense(?![0-9a-zA-Z])(?=[^\\n]{0,40}(?:fit ?bit|핏빗))', '핏빗\\s*센스(?![0-9a-zA-Z])'
    ]),
    ("FTBV4", [
        "(?:(?<=fitbit )|(?<=fitbit's )|(?<=fitbit’s )|(?<=fitbit-)|(?<=fit bit )|(?<=핏빗 )|(?<=핏빗))versa\\s*4(?![0-9a-zA-Z])",
        '\\bversa\\s*4(?![0-9a-zA-Z])(?=[^\\n]{0,40}(?:fit ?bit|핏빗))',
        '핏빗\\s*버사\\s*4(?![0-9a-zA-Z])'
    ]),
    ("FTBV3", [
        "(?:(?<=fitbit )|(?<=fitbit's )|(?<=fitbit’s )|(?<=fitbit-)|(?<=fit bit )|(?<=핏빗 )|(?<=핏빗))versa\\s*3(?![0-9a-zA-Z])",
        '\\bversa\\s*3(?![0-9a-zA-Z])(?=[^\\n]{0,40}(?:fit ?bit|핏빗))',
        '핏빗\\s*버사\\s*3(?![0-9a-zA-Z])'
    ]),
    ("FTBV2", [
        "(?:(?<=fitbit )|(?<=fitbit's )|(?<=fitbit’s )|(?<=fitbit-)|(?<=fit bit )|(?<=핏빗 )|(?<=핏빗))versa\\s*2(?![0-9a-zA-Z])",
        '\\bversa\\s*2(?![0-9a-zA-Z])(?=[^\\n]{0,40}(?:fit ?bit|핏빗))',
        '핏빗\\s*버사\\s*2(?![0-9a-zA-Z])'
    ]),
    ("BSEQCU", ['quiet\\s?comfort\\s*ultra', 'bose[^\\n]{0,12}qc\\s*ultra']),
    ("BSEQC45", [
        'quiet\\s?comfort\\s*45(?![0-9a-zA-Z])', 'bose[^\\n]{0,12}qc\\s*45(?![0-9a-zA-Z])'
    ]),
    ("BSEQC35", [
        'quiet\\s?comfort\\s*35(?![0-9a-zA-Z])', 'bose[^\\n]{0,12}qc\\s*35(?![0-9a-zA-Z])'
    ]),
    ("BSEQCE", ['quiet\\s?comfort\\s*(?:ii\\s*)?earbuds']),
    ("SNAWH6", ['\\bwh[\\s\\-]?1000\\s?xm\\s?6(?![0-9a-zA-Z])']),
    ("SNAWH5", ['\\bwh[\\s\\-]?1000\\s?xm\\s?5(?![0-9a-zA-Z])']),
    ("SNAWH4", ['\\bwh[\\s\\-]?1000\\s?xm\\s?4(?![0-9a-zA-Z])']),
    ("SNAWF6", ['\\bwf[\\s\\-]?1000\\s?xm\\s?6(?![0-9a-zA-Z])']),
    ("SNAWF5", ['\\bwf[\\s\\-]?1000\\s?xm\\s?5(?![0-9a-zA-Z])']),
    ("SNAWF4", ['\\bwf[\\s\\-]?1000\\s?xm\\s?4(?![0-9a-zA-Z])']),
    ("JBRE", [
        "(?:(?<=jabra )|(?<=jabra's )|(?<=jabra’s )|(?<=jabra-)|(?<=자브라 )|(?<=자브라))elite(?![0-9a-zA-Z])",
        '\\belite(?![0-9a-zA-Z])(?=[^\\n]{0,40}(?:jabra|자브라))'
    ]),

    # ═══════════ 제품군 확장 · XR 헤드셋 ═══════════
    ("MQ3S", [
        '(?:meta|oculus)[\\s\\-]*quest[\\s\\-]*3\\s*s(?![0-9a-zA-Z])',
        '(?<!galaxy\\s)\\bquest[\\s\\-]*3\\s*s(?![0-9a-zA-Z])',
        '메타[\\s]*퀘스트[\\s]*3\\s*s(?![0-9a-zA-Z])'
    ]),
    ("MQ3", [
        '(?:meta|oculus)[\\s\\-]*quest[\\s\\-]*3(?![0-9a-zA-Z])',
        '(?<!galaxy\\s)\\bquest[\\s\\-]*3(?![0-9a-zA-Z])', '메타[\\s]*퀘스트[\\s]*3(?![0-9a-zA-Z])'
    ]),
    ("MQ2", [
        '(?:meta|oculus)[\\s\\-]*quest[\\s\\-]*2(?![0-9a-zA-Z])',
        '(?<!galaxy\\s)\\bquest[\\s\\-]*2(?![0-9a-zA-Z])', '메타[\\s]*퀘스트[\\s]*2(?![0-9a-zA-Z])'
    ]),
    ("MQP", [
        '(?:meta|oculus)[\\s\\-]*quest[\\s\\-]*pro(?![0-9a-zA-Z])',
        '(?<!galaxy\\s)\\bquest[\\s\\-]*pro(?![0-9a-zA-Z])'
    ]),
    ("MQ", [
        '(?:meta|oculus)[\\s\\-]*quest(?![0-9a-zA-Z])', '(?:메타|오큘러스)[\\s]*퀘스트(?![0-9a-zA-Z])'
    ]),
    ("MRFT", ['oculus[\\s\\-]*rift(?![0-9a-zA-Z])', '오큘러스[\\s]*리프트']),
    ("APVPM5", [
        '(?:apple[\\s\\-]*)?\\bvision[\\s\\-]*pro[\\s\\-]*\\(?\\s*m5(?![0-9a-zA-Z])',
        '\\bm5[\\s\\-]*vision[\\s\\-]*pro(?![0-9a-zA-Z])'
    ]),
    ("APVP", [
        'apple[\\s\\-]*vision[\\s\\-]*pro(?![0-9a-zA-Z])',
        '\\bvision[\\s\\-]*pro(?![0-9a-zA-Z])(?=[^\\n]{0,100}(?:apple|visionos|애플))',
        '애플[\\s]*비전[\\s]*프로(?![젝그])',
        '비전[\\s]*프로(?![젝그])(?![0-9a-zA-Z])(?=[^\\n]{0,100}(?:apple|애플|visionos))'
    ]),
    ("GXR", [
        'galaxy[\\s\\-]*xr(?![0-9a-zA-Z])', '갤럭시[\\s]*xr(?![0-9a-zA-Z])',
        '갤[\\s]*xr(?![0-9a-zA-Z])', '(?:project[\\s\\-]*)?moohan(?![0-9a-zA-Z])',
        '프로젝트[\\s]*무한'
    ]),
    ("GVR", ['gear[\\s\\-]*vr(?![0-9a-zA-Z])', '기어[\\s]*vr(?![0-9a-zA-Z])']),

    # ═══════════ 제품군 확장 · 스마트글래스 ═══════════
    ("GGL", [
        'galaxy[\\s\\-]*glasses(?![0-9a-zA-Z])',
        'samsung[\\s\\-]*(?:ai[\\s\\-]*)?glasses(?![0-9a-zA-Z])', '갤럭시[\\s]*글래스',
        '삼성[\\s]*(?:ai[\\s]*)?글래스'
    ]),
    ("MRBD", [
        '(?:meta[\\s\\-]*)?ray[\\s\\-]?ban[\\s\\-]*(?:meta[\\s\\-]*)?display(?![0-9a-zA-Z])',
        '메타[\\s]*레이[\\s\\-]?밴[\\s]*디스플레이'
    ]),
    ("OKM", [
        'oakley[\\s\\-]*meta(?![0-9a-zA-Z])', 'meta[\\s\\-]*oakley(?![0-9a-zA-Z])',
        'oakley[\\s\\-]*(?:vanguard|hstn)(?![0-9a-zA-Z])', '오클리[\\s]*메타'
    ]),
    ("MRB", [
        'ray[\\s\\-]?ban[\\s\\-]*meta(?![0-9a-zA-Z])',
        "meta(?:'s|’s)?[\\s\\-]*ray[\\s\\-]?ban(?![0-9a-zA-Z])",
        'ray[\\s\\-]?ban(?![0-9a-zA-Z])(?=[^\\n]{0,60}meta)', '레이[\\s\\-]?밴[\\s]*메타',
        '메타[\\s]*레이[\\s\\-]?밴'
    ]),
    ("MGL", [
        '(?<!ban )(?<!ban-)(?<!밴 )(?<!밴)meta[\\s\\-]*(?:ai[\\s\\-]*)?glass(?:es)?(?![0-9a-zA-Z])',
        '(?<!밴 )(?<!밴)메타[\\s]*(?:ai[\\s]*)?글래스'
    ]),
    ("XRL", [
        '\\bxreal(?![0-9a-zA-Z])', '\\bx\\-real(?![0-9a-zA-Z])', '\\bnreal(?![0-9a-zA-Z])',
        '엑스리얼'
    ]),
    ("VTR", ['\\bviture(?![0-9a-zA-Z])']),
    ("RKD", ['\\brokid(?![0-9a-zA-Z])', '로키드']),

    # ═══════════ 제품군 확장 · 스마트링 ═══════════
    ("OURA5", [
        '\\boura\\s*ring\\s*5(?![0-9a-zA-Z])',
        '\\boura\\s*ring\\s*gen(?:eration)?\\s*5(?![0-9a-zA-Z])',
        '\\boura\\s*(?:ring\\s*)?5\\s*(?:대|세대|generation|gen)(?![0-9a-zA-Z])',
        '\\boura\\s*5(?![0-9a-zA-Z])', '오우라\\s*링\\s*5(?![0-9a-zA-Z])'
    ]),
    ("OURA4", [
        '\\boura\\s*ring\\s*4(?![0-9a-zA-Z])',
        '\\boura\\s*ring\\s*gen(?:eration)?\\s*4(?![0-9a-zA-Z])',
        '\\boura\\s*(?:ring\\s*)?gen(?:eration)?\\s*4(?![0-9a-zA-Z])',
        '\\boura\\s*4(?![0-9a-zA-Z])', '오우라\\s*링\\s*4(?![0-9a-zA-Z])'
    ]),
    ("OURA3", [
        '\\boura\\s*ring\\s*3(?![0-9a-zA-Z])',
        '\\boura\\s*ring\\s*gen(?:eration)?\\s*3(?![0-9a-zA-Z])',
        '\\boura\\s*(?:ring\\s*)?gen(?:eration)?\\s*3(?![0-9a-zA-Z])',
        '\\bgen\\s*3\\s*oura(?![0-9a-zA-Z])', '\\boura\\s*3(?![0-9a-zA-Z])',
        '오우라\\s*링\\s*3(?![0-9a-zA-Z])'
    ]),
    ("OURA", [
        '\\boura\\s*ring(?![0-9a-zA-Z])', '(?<![A-Za-z]-)\\boura(?![0-9a-zA-Z])(?!\\s*bay\\b)',
        '오우라\\s*링(?![0-9a-zA-Z])', '오우라(?![0-9a-zA-Z])'
    ]),
    ("ULHRA", ['\\bultra\\s?human\\s*(?:ring\\s*)?air(?![0-9a-zA-Z])']),
    ("ULHR", ['\\bultra\\s?human(?![0-9a-zA-Z])']),
    ("RGC3", ["\\bring\\s?conn(?:'s|’s)?\\s*(?:gen\\s*)?3(?![0-9a-zA-Z])"]),
    ("RGC2", [
        "\\bring\\s?conn(?:'s|’s)?\\s*(?:gen\\s*)?2\\s*air(?![0-9a-zA-Z])",
        "\\bring\\s?conn(?:'s|’s)?\\s*(?:gen\\s*)?2(?![0-9a-zA-Z])"
    ]),
    ("RGC", ['\\bring\\s?conn(?![0-9a-zA-Z])']),
    ("AZHELIO", ['\\b(?:amazfit\\s*)?helio\\s*ring(?![0-9a-zA-Z])']),

    # ═══════════ 제품군 확장 · 피트니스 밴드 ═══════════
    ("WHPMG", [
        '\\bwhoop\\s*(?:strap\\s*)?mg(?![0-9a-zA-Z])',
        '\\bwhoop\\s*5(?:\\.0)?\\s*(?:life\\s*)?mg(?![0-9a-zA-Z])'
    ]),
    ("WHP5", [
        '\\bwhoop\\s*(?:strap\\s*)?5(?:\\.0)?\\s*(?:peak|life)(?![0-9a-zA-Z])',
        '\\bwhoop\\s*(?:strap\\s*)?5(?:\\.0)?(?![0-9a-zA-Z])'
    ]),
    ("WHP4", ['\\bwhoop\\s*(?:strap\\s*)?4(?:\\.0)?(?![0-9a-zA-Z])']),
    ("WHP", [
        '(?<!big\\s)(?<!woo\\s)(?<!whoop\\s)\\bwhoop(?![\\s\\-]*(?:de|dee|doo)\\b)(?!\\s+whoop)(?!\\s+(?:and|or)\\s+(?:cheer|holler|scream|shout|yell|clap))(?![0-9a-zA-Z])'
    ]),

    # ═══════════ 제품군 확장 · 헤드폰 ═══════════
    ("ANKSPACE", [
        '(?:sound\\s?core|anker)(?:\\s*by\\s*anker)?[\\s\\-]*space\\s*(?:one|q45|a40|2)(?:\\s*pro)?(?![0-9a-zA-Z])',
        '\\bspace\\s*(?:one|q45|a40|2)(?:\\s*pro)?(?![0-9a-zA-Z])(?=[^\\n]{0,80}(?:sound\\s?core|anker))'
    ]),
    ("ANKQ", [
        '(?:sound\\s?core|anker)(?:\\s*by\\s*anker)?[\\s\\-]*(?:life\\s*)?q(?:20i|30s?|45|35)(?![0-9a-zA-Z])',
        '\\b(?:life\\s*)?q(?:20i|30s?|45|35)(?![0-9a-zA-Z])(?=[^\\n]{0,80}(?:sound\\s?core|anker))'
    ]),
    ("SENM4", [
        'sennheiser[^\\n]{0,40}momentum\\s*(?:wireless\\s*)?4(?![0-9a-zA-Z])',
        '\\bmomentum\\s*(?:wireless\\s*)?4(?![0-9a-zA-Z])(?=[^\\n]{0,80}(?:sennheiser|젠하이저))',
        '젠하이저\\s*모멘텀\\s*4(?![0-9a-zA-Z])'
    ]),
    ("SENMOM", [
        'sennheiser[^\\n]{0,40}momentum(?![0-9a-zA-Z])',
        '\\bmomentum(?![0-9a-zA-Z])(?=[^\\n]{0,80}(?:sennheiser|젠하이저))',
        '젠하이저\\s*모멘텀(?![0-9a-zA-Z])'
    ]),
    ("SENACC", ['\\baccentum(?![0-9a-zA-Z])']),
    ("BTS360", ['\\bbeats\\s*360(?![0-9a-zA-Z])']),
    ("BTSSTP", ['\\bbeats\\s*studio\\s*pro(?![0-9a-zA-Z])']),
    ("BTSSOLO4", ['\\bbeats\\s*solo\\s*4(?![0-9a-zA-Z])']),
    ("JBLTOUR1", ['\\bjbl\\s*tour\\s*one\\s*(?:m\\s?\\d)?(?![0-9a-zA-Z])']),
    ("JBLTUNE", [
        '\\bjbl\\s*tune\\s*(?:beam|flex|buds|\\d{2,3})(?:\\s*\\d{0,3})?(?:nc|bt|tws)?(?![0-9a-zA-Z])'
    ]),
    ("NTHP1", ['\\bnothing\\s*headphone\\s*\\(?\\s*1\\s*\\)?(?![0-9a-zA-Z])']),
    ("NTHPA", ['\\bnothing\\s*headphone\\s*\\(?\\s*a\\s*\\)?(?![0-9a-zA-Z])']),

    # ═══════════ 제품군 확장 · 이어버즈 ═══════════
    ("ANKLIB5P", ['\\bliberty\\s*5\\s*pro(?![0-9a-zA-Z])']),
    ("ANKLIB5", ['\\bliberty\\s*5(?![0-9a-zA-Z])']),
    ("ANKLIB4P", ['\\bliberty\\s*4\\s*pro(?![0-9a-zA-Z])']),
    ("ANKLIB4", ['\\bliberty\\s*4(?:\\s*nc)?(?![0-9a-zA-Z])']),
    ("ANKSLEEP", [
        '\\bsleep\\s*a[0-9]0(?![0-9a-zA-Z])',
        '(?:sound\\s?core|anker)(?:\\s*by\\s*anker)?[\\s\\-]*sleep\\s*(?:earbuds?)?\\s*\\d?(?![0-9a-zA-Z])',
        '\\bsleep\\s*earbuds?\\s*\\d?(?![0-9a-zA-Z])(?=[^\\n]{0,80}(?:sound\\s?core|anker))'
    ]),
    ("ANKAERO", ['\\baerofit\\s*\\d?(?![0-9a-zA-Z])']),
    ("ANKSPORTX", ['\\bsport\\s*x[0-9]0(?![0-9a-zA-Z])']),
    ("ANKP", ['\\bp[234]0i(?![0-9a-zA-Z])', '\\bp31i(?![0-9a-zA-Z])']),
    ("ANKR", ['\\br[56]0i(?![0-9a-zA-Z])']),
    ("SENMTW4", [
        '\\bmomentum\\s*(?:true\\s*wireless|tws)\\s*4(?![0-9a-zA-Z])',
        '\\bm4\\s*earbuds(?![0-9a-zA-Z])'
    ]),
    ("BTSPBP2", ['\\bpower\\s?beats\\s*pro\\s*2(?![0-9a-zA-Z])']),
    ("BTSPBP", ['\\bpower\\s?beats\\s*pro(?![0-9a-zA-Z])']),
    ("BTSPBF", ['\\bpower\\s?beats\\s*fit(?![0-9a-zA-Z])']),
    ("BTSPB", ['\\bpower\\s?beats\\s*\\d?(?![0-9a-zA-Z])']),
    ("BTSSTBP", ['\\bbeats\\s*studio\\s*buds\\s*(?:\\+|plus)(?![0-9a-zA-Z])']),
    ("BTSSTB", ['\\bbeats\\s*studio\\s*buds(?![0-9a-zA-Z])']),
    ("BTSSOLOB", ['\\bbeats\\s*solo\\s*buds(?![0-9a-zA-Z])']),
    ("BTSFP", ['\\bbeats\\s*fit\\s*pro(?![0-9a-zA-Z])']),
    ("JBLTOURP", ['\\bjbl\\s*tour\\s*pro\\s*\\d?(?![0-9a-zA-Z])']),
    ("JBLLIVE", [
        '\\bjbl\\s*live\\s*(?:buds|beam|flex|pro|\\d{2,3})(?:\\s*\\d{0,3})?(?:nc|bt)?(?![0-9a-zA-Z])'
    ]),
    ("JBLEND", ['\\bjbl\\s*endurance\\s*(?:zone|peak|race|dive|buds)?\\s*\\d?(?![0-9a-zA-Z])']),
    ("JBLVIBE", ['\\bjbl\\s*vibe\\s*(?:beam|buds|flex)?\\s*\\d?(?![0-9a-zA-Z])']),
    ("JBLWAVE", ['\\bjbl\\s*wave\\s*(?:beam|buds|flex)?\\s*\\d?(?![0-9a-zA-Z])']),

    # ═══════════ 제품군 확장 · 노트북 ═══════════
    ("GBK6E", [
        'galaxy\\s*book\\s*6\\s*edge(?![0-9a-zA-Z])', '(?:갤럭시\\s*북|갤북)\\s*6\\s*(?:엣지|에지)'
    ]),
    ("GBK6U", ['galaxy\\s*book\\s*6\\s*ultra(?![0-9a-zA-Z])', '(?:갤럭시\\s*북|갤북)\\s*6\\s*울트라']),
    ("GBK6P", ['galaxy\\s*book\\s*6\\s*pro(?![0-9a-zA-Z])', '(?:갤럭시\\s*북|갤북)\\s*6\\s*프로']),
    ("GBK6", ['galaxy\\s*book\\s*6(?![0-9a-zA-Z])', '(?:갤럭시\\s*북|갤북)\\s*6(?![0-9a-zA-Z])']),
    ("GBK5P3", [
        'galaxy\\s*book\\s*5\\s*pro\\s*360(?![0-9a-zA-Z])',
        '(?:갤럭시\\s*북|갤북)\\s*5\\s*프로\\s*360(?![0-9a-zA-Z])'
    ]),
    ("GBK5P", ['galaxy\\s*book\\s*5\\s*pro(?![0-9a-zA-Z])', '(?:갤럭시\\s*북|갤북)\\s*5\\s*프로']),
    ("GBK5", ['galaxy\\s*book\\s*5(?![0-9a-zA-Z])', '(?:갤럭시\\s*북|갤북)\\s*5(?![0-9a-zA-Z])']),
    ("GBK4E", [
        'galaxy\\s*book\\s*4\\s*edge(?![0-9a-zA-Z])', '(?:갤럭시\\s*북|갤북)\\s*4?\\s*(?:엣지|에지)'
    ]),
    ("GBK4U", ['galaxy\\s*book\\s*4\\s*ultra(?![0-9a-zA-Z])', '(?:갤럭시\\s*북|갤북)\\s*4\\s*울트라']),
    ("GBK4P", ['galaxy\\s*book\\s*4\\s*pro(?![0-9a-zA-Z])', '(?:갤럭시\\s*북|갤북)\\s*4\\s*프로']),
    ("GBK4", ['galaxy\\s*book\\s*4(?![0-9a-zA-Z])', '(?:갤럭시\\s*북|갤북)\\s*4(?![0-9a-zA-Z])']),
    ("GBK3U", ['galaxy\\s*book\\s*3\\s*ultra(?![0-9a-zA-Z])', '(?:갤럭시\\s*북|갤북)\\s*3\\s*울트라']),
    ("GBK3P3", [
        'galaxy\\s*book\\s*3\\s*(?:pro\\s*)?360(?![0-9a-zA-Z])',
        '(?:갤럭시\\s*북|갤북)\\s*3\\s*(?:프로\\s*)?360(?![0-9a-zA-Z])'
    ]),
    ("GBK3P", ['galaxy\\s*book\\s*3\\s*pro(?![0-9a-zA-Z])', '(?:갤럭시\\s*북|갤북)\\s*3\\s*프로']),
    ("GBK3", ['galaxy\\s*book\\s*3(?![0-9a-zA-Z])', '(?:갤럭시\\s*북|갤북)\\s*3(?![0-9a-zA-Z])']),
    ("GBK2P", ['galaxy\\s*book\\s*2\\s*pro(?![0-9a-zA-Z])', '(?:갤럭시\\s*북|갤북)\\s*2\\s*프로']),
    ("GBK2", ['galaxy\\s*book\\s*2(?![0-9a-zA-Z])', '(?:갤럭시\\s*북|갤북)\\s*2(?![0-9a-zA-Z])']),
    ("GBKS", ['galaxy\\s*book\\s+s(?![0-9a-zA-Z])', '(?:갤럭시\\s*북|갤북)\\s*s(?![0-9a-zA-Z])']),
    ("GBKGO", ['galaxy\\s*book\\s*go(?![0-9a-zA-Z])', '(?:갤럭시\\s*북|갤북)고(?![0-9a-zA-Z])']),
    ("GBKFLX", ['galaxy\\s*book\\s*flex(?![0-9a-zA-Z])', '(?:갤럭시\\s*북|갤북)\\s*플렉스']),
    ("GBK", [
        '(?<!guide )(?<!guide to the )galaxy\\s*books?(?![0-9a-zA-Z])',
        '갤럭시\\s*북(?![0-9a-zA-Z])', '갤북(?![0-9a-zA-Z])'
    ]),
    ("MBPRO", ['\\bmacbook\\s*pro(?![0-9a-zA-Z])', '맥북\\s*프로']),
    ("MBAIR", ['\\bmacbook\\s*air(?![0-9a-zA-Z])', '맥북\\s*에어']),
    ("MSSFL", ['\\bsurface\\s*laptop(?![0-9a-zA-Z])', '서피스\\s*랩탑']),
    ("MSSFB", ['\\bsurface\\s*book(?![0-9a-zA-Z])', '서피스\\s*북(?![0-9a-zA-Z])']),

    # ═══════════ 제품군 확장 · 태블릿 ═══════════
    ("IPADPRO", ['\\bipad\\s*pro(?![0-9a-zA-Z])', '아이패드\\s*프로']),
    ("IPADAIR", ['\\bipad\\s*air(?![0-9a-zA-Z])', '아이패드\\s*에어']),
    ("IPADMINI", ['\\bipad\\s*mini(?![0-9a-zA-Z])', '아이패드\\s*미니']),
    ("MSSFP", ['\\bsurface\\s*pro(?![0-9a-zA-Z])', '서피스\\s*프로']),
    ("NBRMAST", ["\\bred\\s?magic['’\\s\\-]*astra(?![0-9a-zA-Z])"]),
    ("TCLNXT", [
        '\\bnxt\\s?paper(?![0-9a-zA-Z])',
        "\\btcl[\\s:'’\\-]{0,3}(?:tab|note)\\s*(?:a1|10l|a3)(?![0-9a-zA-Z])"
    ]),

    # ═══════════ 제품군 확장 · 폰(미커버 브랜드) ═══════════
    ("MSSFD", ['\\bsurface\\s*duo(?![0-9a-zA-Z])', '서피스\\s*듀오']),
    ("NBRM11P", [
        '\\bred\\s?magic\\s*11\\s*s?\\s*pro(?![0-9a-zA-Z])',
        '\\bred\\s?magic\\s*11s?(?![0-9a-zA-Z])'
    ]),
    ("NBRM10", ['\\bred\\s?magic\\s*10s?(?![0-9a-zA-Z])']),
    ("NBRM", ['\\bred\\s?magic(?![0-9a-zA-Z])', '레드매직']),
    ("NBZ", ['\\bnubia\\s*z\\s*\\d{2}s?(?![0-9a-zA-Z])']),
    ("NBNEO", ['\\bnubia\\s*neo(?![0-9a-zA-Z])']),
    ("NB", ['\\bnubia(?![0-9a-zA-Z])']),
    ("ZTEAXON", [
        "\\bzte['’\\s\\-]*axon(?![0-9a-zA-Z])",
        '\\baxon\\s*(?:m|7|9|10|11|20|30|40|50|60)(?![0-9a-zA-Z])'
    ]),
    ("ZTEBLADE", ["\\bzte['’\\s\\-]*blade(?![0-9a-zA-Z])", '\\bblade\\s*[avls]\\s*\\d']),
    ("FP6", [
        '\\bfairphone\\s*6(?![0-9a-zA-Z])', '\\bfairphone\\s*\\(?gen\\.?\\s*6', '페어폰\\s*6'
    ]),
    ("FP5", ['\\bfairphone\\s*5(?![0-9a-zA-Z])', '페어폰\\s*5']),
    ("FP4", ['\\bfairphone\\s*4(?![0-9a-zA-Z])', '페어폰\\s*4']),
    ("FP3", ['\\bfairphone\\s*3(?![0-9a-zA-Z])', '페어폰\\s*3']),
    ("FP2", ['\\bfairphone\\s*2(?![0-9a-zA-Z])', '페어폰\\s*2']),
    ("FP", ['\\bfairphones?(?![0-9a-zA-Z])', '페어폰(?![0-9a-zA-Z])']),
    ("HMDSKY", ['(?:nokia[\\s/\\-]*)?\\bhmd[\\s\\-]*skyline(?![0-9a-zA-Z])']),
    ("HMD", [
        '(?:nokia[\\s/\\-]*)?\\bhmd[\\s\\-]+(?:global|pulse|vibe|fusion|crest|arc|asha|key|luma|xplora\\w*|touch|amped|barbie|t2[01]|2760|105|110|150|102|106|123)(?![0-9a-zA-Z])',
        '(?:nokia[\\s/\\-]*)?\\bhmd\\.com'
    ]),
    ("SHAQ", ['\\baquos(?![0-9a-zA-Z])', '아쿠오스', '아쿠아스']),
]

# 사전 컴파일
_COMPILED: List[Tuple[str, List[re.Pattern]]] = [
    (code, [re.compile(p, re.IGNORECASE) for p in pats])
    for code, pats in PRODUCT_PATTERNS
]


# ═════════ 타 브랜드 인접 가드 ═══════════════════════════════════════════
#
# 브랜드 한정자가 없는 패턴(`watch\\s*ultra`, `\\bfold\\s*6`, `\\bwatch\\s*6`)이
# 타사 기기를 삼켰다. 코퍼스 1/7 표본(62,209행) 실측 —
#   GWU  65/384 (16.9%) 가 'Apple Watch Ultra'
#   GW6  14/147 ( 9.5%) 가 'Redmi Watch 5/6'
#   GZF6 18/343 ( 5.2%) 가 'vivo X Fold6'
# 갤럭시워치 울트라 결함 통계의 6건 중 1건이 애플 시계였다는 뜻이다.
#
# 규칙은 **인접**이다. 매칭 구간 바로 앞(24자 이내, 사이에 다른 말 없음)에 다른
# 브랜드 토큰이 붙어 있을 때만 그 매칭을 버린다. 'iPhone 15 with the S24' 처럼
# 사이에 말이 끼면 진짜 비교문이므로 살린다 — 비교글 신호가 이 모듈의 존재 이유다.
_ADJ_WINDOW = 24
# 끼어들 수 있는 중간 토큰은 실제 제품명에 나오는 x/gt 둘로 제한한다. 임의의
# 1~2글자를 허용하면 'in honor of S25' 같은 영문이 오작동시킨다.
_ADJ_RIVAL_RE = re.compile(
    r"\b(apple|iphone|ipad|airpods|macbook|google|pixel|xiaomi|redmi|poco|"
    r"oneplus|oppo|vivo|realme|huawei|honor|motorola|moto|sony|xperia|nokia|"
    r"asus|amazfit|garmin|fitbit|bose|jabra|jbl)"
    r"(?:'s)?[\s\-]*(?:(?:x|gt)[\s\-]+)?$",
    re.IGNORECASE,
)
# 'nothing'·'beats' 는 영단어와 구별이 안 돼 제외했다('beats the S25' 오발화).
_WORD_BRAND: Dict[str, str] = {
    "apple": "apple", "iphone": "apple", "ipad": "apple",
    "airpods": "apple", "macbook": "apple",
    "google": "google", "pixel": "google",
    "xiaomi": "xiaomi", "redmi": "xiaomi", "poco": "xiaomi", "amazfit": "amazfit",
    "oneplus": "oneplus", "oppo": "oppo", "vivo": "vivo", "realme": "realme",
    "huawei": "huawei", "honor": "honor",
    "motorola": "motorola", "moto": "motorola",
    "sony": "sony", "xperia": "sony",
    "nokia": "nokia", "asus": "asus",
    "garmin": "garmin", "fitbit": "fitbit", "bose": "bose",
    "jabra": "jabra", "jbl": "jbl",
}
# 코드 접두사 → 브랜드. 없으면 삼성. 가장 긴 접두사가 이긴다(GM=갤럭시M vs GMN=가민).
#
# **신규 브랜드를 추가할 때 여기를 같이 채워야 한다.** 빠뜨리면 그 코드는 삼성으로
# 취급돼 자기 브랜드 토큰이 앞에 붙은 매칭(예: 'Garmin fenix 8')을 가드가 버린다.
_CODE_BRAND_PREFIX: Dict[str, str] = {
    "AP": "apple", "AW": "apple", "AB": "apple",
    "PX": "google", "PW": "google", "PB": "google", "FTB": "fitbit",
    "XM": "xiaomi", "RM": "xiaomi", "RDW": "xiaomi", "PC": "xiaomi",
    "AZ": "amazfit",
    "OO": "oppo", "VV": "vivo", "OP": "oneplus", "RL": "realme",
    "HO": "honor", "HW": "huawei",
    "MT": "motorola", "SN": "sony", "NK": "nokia", "AS": "asus", "NT": "nothing",
    "GMN": "garmin", "BSE": "bose", "JBR": "jabra",
    # 0040 제품군 확장 — XR·글래스·링·오디오·노트북·미커버 폰
    "ANK": "anker", "BTS": "apple", "FP": "fairphone",
    "HMD": "hmd", "IPAD": "apple", "JBL": "jbl",
    "MB": "apple", "MGL": "meta", "MQ": "meta",
    "MRB": "meta", "MRFT": "meta", "MS": "microsoft",
    "NB": "nubia", "OKM": "meta", "OURA": "oura",
    "RGC": "ringconn", "RKD": "rokid", "SEN": "sennheiser",
    "SH": "sharp", "TCL": "tcl", "ULH": "ultrahuman",
    "VTR": "viture", "WHP": "whoop", "XRL": "xreal",
    "ZTE": "zte",
}


def _brand_of(code: str) -> str:
    up = code.upper()
    for n in (4, 3, 2):
        b = _CODE_BRAND_PREFIX.get(up[:n])
        if b:
            return b
    return "samsung"


def _rival_adjacent(text: str, code: str, start: int) -> bool:
    """매칭 구간 바로 앞에 **다른** 브랜드 토큰이 붙어 있으면 True."""
    m = _ADJ_RIVAL_RE.search(text[max(0, start - _ADJ_WINDOW):start])
    if not m:
        return False
    return _WORD_BRAND[m.group(1).lower()] != _brand_of(code)


def _first_clean_span(text: str, code: str,
                      patterns: List[re.Pattern]) -> Optional[Tuple[int, int]]:
    """코드의 패턴들을 순서대로 훑어 타 브랜드에 붙지 않은 첫 매칭 구간을 준다.

    같은 패턴의 뒤쪽 출현도 본다 — 'Redmi Watch 6 ... Galaxy Watch 6' 처럼
    앞 출현만 타사인 글에서 뒤의 진짜 매칭을 살리기 위해서다.
    """
    for pat in patterns:
        for m in pat.finditer(text):
            if not _rival_adjacent(text, code, m.start()):
                return m.span()
    return None


# ═════════ 레거시 사전 결과 수용 게이트 ═══════════════════════════════════
#
# scripts/relink_products.py 에는 구형 모델까지 덮는 훨씬 넓은 사전(MODEL_MAP)이 있다.
# 라이브 사전(PRODUCT_PATTERNS)이 못 잡는 'Galaxy Note 7'·'Galaxy A32'·'Galaxy S7 edge'
# 를 잡아주므로 미태깅 보정에 쓸 가치가 크다 — 실측으로 최근 수집분의 12.5% 가 이렇게
# 버려지고 있었다.
#
# **다만 그 사전은 경쟁사 카탈로그 이전에 만들어져 타사 기기를 삼성으로 흡수한다.**
# 실측 — '홍미노트7'·'xiaomi redmi note 3' → GN7/GN3, 'Xiaomi Watch S4' → GW6.
# 그래서 라이브 사전이 먼저 판정하고(경쟁사 380종 + 브랜드 인접 가드 보유),
# 라이브가 빈손일 때만 레거시를 쓰되 이 게이트를 통과해야 받아들인다.
#
# 규칙 — 코드의 기기 계열어(note/watch/buds/fold/flip/tab)가 **타사 브랜드에만**
# 붙어 있으면 거부한다. 자사 근거가 함께 있으면 비교글이므로 유지한다.
# 실측(코퍼스 140,000행 스캔, 레거시 부여 후보 6,135건): 거부 19건(0.31%).
_LEGACY_FAMILY: List[Tuple[str, str]] = [
    ("GZFL", r"flip|플립"),
    ("GZF",  r"fold|폴드"),
    ("GN",   r"note|노트"),
    ("GGS",  r"watch|gear|워치|기어"),
    ("GW",   r"watch|워치"),
    ("GB",   r"buds|earbuds|버즈"),
    ("GTAB", r"tab\b|tablet|탭"),
]
_LEGACY_OWN = r"samsung|galaxy|삼성|갤럭시|갤워치|갤탭|갤"
_LEGACY_RIVAL = (
    r"apple|iphone|ipad|airpods|pixel|google|xiaomi|redmi|poco|oneplus|oppo|"
    r"vivo|realme|huawei|honor|motorola|moto|sony|xperia|nokia|asus|infinix|"
    r"tecno|nothing|cmf|amazfit|garmin|fitbit|bose|jabra|jbl|lenovo|lg\b|"
    r"샤오미|홍미|레드미|애플|아이폰|화웨이|오포|비보|원플러스"
)
# 'Gear' 는 삼성 전용 브랜드어라 그 자체로 자사 근거다("Gear S3" 에 galaxy 가 없어도 삼성).
# 'Gear S'(무번호)도 실제 모델이다. `\s+s\b` 로 공백을 요구해야 복수형 'gears'
# ("shifting gears")를 잡지 않는다. 'gear system' 은 s 뒤 \b 가 막는다.
_LEGACY_GEAR = re.compile(
    r"\bgear\s*s?\s*\d|\bgear\s+s\b|\bgear\s+(?:fit|sport|circle)|기어\s*s", re.I)


def accept_legacy_code(text: str, code: str) -> bool:
    """레거시 사전이 준 삼성 코드를 받아들일지 판정."""
    fam = next((f for pre, f in _LEGACY_FAMILY if code.upper().startswith(pre)), None)
    if fam is None:
        return True                      # 계열 혼동이 없는 코드(GS/GA/GM/GJ 등)
    rival = re.search(rf"(?:{_LEGACY_RIVAL})[\s\-'’]*(?:x[\s\-]*|gt[\s\-]*)?(?:{fam})",
                      text, re.I)
    if not rival:
        return True                      # 타사 인접 없음 → 안전
    if fam.find("gear") >= 0 and _LEGACY_GEAR.search(text):
        return True                      # 'Gear S3' 자체가 삼성 근거
    own = re.search(rf"(?:{_LEGACY_OWN})[\s\-'’]*\w{{0,10}}[\s\-'’]*(?:{fam})",
                    text, re.I)
    return bool(own)                     # 자사 근거가 같이 있으면 비교글이라 유지


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
                if _rival_adjacent(text, code, s):
                    continue          # 타사 기기 언급은 등장 횟수에서도 뺀다
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
        span = _first_clean_span(text, code, patterns)
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
