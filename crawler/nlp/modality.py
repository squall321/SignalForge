# VOC 문서의 양상(1인칭 실제 고장 / 우려 / 질문 / 전언 / 리뷰)을 가르는 렉시콘 분류기
"""
양상(modality) 분류.

결함 추출기는 "힌지가 고장났다"와 "힌지 걱정돼서 살까 고민"을 똑같이 결함 1건으로 센다.
실데이터 150건을 통독해 라벨한 결과 **firsthand(본인이 실제 겪은 고장)는 49.3%뿐**이었고,
나머지 절반은 결함 주장이 아니었다(리뷰 32·기타 17·질문 10·우려 9·전언 8).
매체(media) 27건은 firsthand 가 **0건**으로 전부 스펙·할인 기사였다.

판별의 핵심은 '1인칭 밀도'가 아니라 **결합**이다 — 실측으로 `\bi\b|\bmy\b` 단독은
정밀도 0.689(base 0.493)에 그쳤다. 구매 고민글도 "I/my"를 똑같이 쓰기 때문이다.
firsthand 를 가르는 것은 **"자기 소유 기기" × "실현된(realis) 증상"** 의 결합이다.

구조: CORE(결합 근거) − VETO(비현실·전언·기사) 의 마진 경쟁. 절대 우선순위가 아니라
마진이라 "Thinking about replacing S24U... battery is basically DEAD" 같은
우려-형태 firsthand 를 되찾는다.

용도: 결함 집계·급등 탐지에서 firsthand 만 세기 위한 필터. **삭제 근거로는 쓰지 않는다**
(정밀도 0.88 은 8건 중 1건 오분류라 원본 제거 근거로 부족). voc_defects.modality 컬럼에
저장하고 집계 단계에서 필터한다.
"""
from __future__ import annotations

import re
from typing import Any

# ==========================================================================
# 0. 공용 어휘 조각
# ==========================================================================

# 증상 술어 (영어)
SYM = (
    r"(?:broke|broken|breaks|cracked|crack(?:s|ing)|shattered|peel(?:ed|ing)|"
    r"scratch(?:ed|es)|died|dead|dying|drain(?:s|ed|ing)|"
    r"overheat(?:s|ed|ing)|freez(?:e|es|ing)|froze|frozen|"
    r"crash(?:es|ed|ing)|lag(?:s|gy|ged|ging)|stutter(?:s|ed|ing)|jittery|glitch\w*|"
    r"stopped working|stops working|not working|doesn'?t work|didn'?t work|won'?t work|"
    r"won'?t (?:turn on|charge|boot|come on)|not charging|"
    r"burn-?in|discolou?red|faded|flicker\w*|rattl\w+|creak\w*|crackl\w+|"
    r"bootloop|boot ?loop|stuck|failing|failed|fails|malfunction\w*|"
    r"blurry|blurred|swollen|swelling|bulging|disconnect\w*|cuts? out|cut out|"
    r"unusable|dead pixel|green line|goes blank|flooded|water damage|no longer works?)"
)

# 기기/부품 명사
DEV = (
    r"(?:phone|device|unit|handset|watch|buds?|earbuds?|earphone|soundbar|ring|"
    r"galaxy|samsung|iphone|pixel|fold ?\d?|flip ?\d?|note ?\d+|s\d{1,2}\b|a\d{2}\b|gw\d|"
    r"screen|display|panel|battery|camera|hinge|speaker|charger|sim|frame)"
)

# 1인칭 소유격 (가족 소유도 본인이 관측한 고장으로 취급)
POSS = r"(?:my|our|wife'?s|husband'?s|mother'?s|father'?s|son'?s|daughter'?s)"

# 증상 술어 (한국어 원문)
SYM_KO = (
    r"(?:깨졌|깨짐|파손|금이 ?갔|균열|고장|먹통|안 ?켜|안 ?됨|안 ?되|안되|"
    r"발열|뜨거|과열|버벅|끊김|끊기|렉|튕[겨김]|재부팅|방전|광탈|충전 ?안|"
    r"덜걱|덜그럭|들뜸|유격|삐걱|잡음|긁힘|스크래치|기스|"
    r"번인|잔상|변색|벗겨|박리|이물)"
)

# 액세서리 — 결함 주체가 케이스/필름이면 기기 고장이 아니다
ACC = r"(?:case|cover|film|screen protector|protector|tempered glass|strap|band|grip|holder|pouch)"

# ==========================================================================
# 1. CORE — "자기 기기에 실현된 결함" (firsthand 의 진짜 근거)
#    가중치는 표본 실측 P(firsthand|feature) 를 근거로 두 티어로 나눈다.
# ==========================================================================

# --- Tier 1: 결합 패턴 (실측 P = 0.80~1.00) ---
CORE1: list[tuple[float, str]] = [
    (3.0, rf"\b{POSS}\s+(?:\w+\s+){{0,3}}{DEV}\b[^.!?\n]{{0,70}}\b{SYM}"),          # my S24 ... cracked
    (3.0, rf"\b{SYM}\b[^.!?\n]{{0,45}}\b(?:of|on|with|in) {POSS}\b"),                # broken on my Fold 8
    (3.0, r"\bi ?(?:'m|am|m)? ?(?:experiencing|having|getting|facing|dealing with)\b"),
    (3.0, r"\bi (?:noticed|notice|found|experienced|encountered|discovered|observed|realized|"
          r"started (?:getting|seeing|noticing))\b"),
    (3.0, r"\bi'?(?:ve)? ?(?:have|had|been having|am having|'m having|m having)\s+"
          r"(?:a |an |the |some |this )?(?:\w+ ){0,3}(?:problem|issue|trouble|bug|glitch|error|failure)s?\b"),
    (3.0, r"\bsince (?:i )?(?:bought|purchased|got|received|installed|updated|upgraded|the update)\b"),
    (3.0, r"\b(?:it'?s|its) been (?:about |almost |nearly |over )?(?:a|an|one|two|three|\d+) ?"
          r"(?:day|week|month|year)"),
    (3.0, rf"\b{SYM}\b[^.!?\n]{{0,35}}\b(?:within|after|in) (?:about |only |just |less than )?"
          r"(?:a|an|one|two|three|\d+) ?(?:day|week|month|year)"),
    (3.0, r"\bi (?:dropped|submerged|spilled|broke|cracked|shattered|scratched|flashed)\s+(?:my|the)\b"),
    (3.0, r"\bi (?:can'?t|cannot|couldn'?t|can not) (?:use|open|charge|turn on|answer|hear|connect)\b"),
    (3.0, r"\b(?:repair (?:quote|cost|shop|price)|service cent(?:er|re)|under warranty|"
          r"out of warranty|\brma\b|took it (?:to|back)|sent it (?:in|back))"),
    (3.0, r"\bmine (?:also|too|as well|is|was|has|had|cracked|broke|died)\b"),
    (2.6, rf"\b{POSS}\s+(?:\w+\s+){{0,2}}{DEV}\s+(?:has|have|hasn'?t|haven'?t|is|isn'?t|was|wasn'?t|"
          r"won'?t|wont|doesn'?t|does not|never|only|always|randomly|suddenly|gradually|no longer)\b"),
    (2.6, rf"\b(?:it|they|mine|this|the \w+) (?:just |suddenly |randomly |finally |gradually |"
          rf"periodically |constantly )?(?:started|began|keeps?|kept|has been|have been|is|was|are|"
          rf"were|goes|went)\b[^.!?\n]{{0,35}}{SYM}"),
    (2.6, rf"\b(?:i )?(?:had|have|'ve had|bought|got|purchased|ordered|use|used|owned)\s+"
          rf"(?:a |an |the |my |two |multiple )?(?:\w+\s+){{0,2}}{DEV}\b[^.!?\n]{{0,60}}{SYM}"),
    (2.6, rf"\b(?:when|every time|whenever) i\b[^.!?\n]{{0,60}}{SYM}|"
          rf"{SYM}\b[^.!?\n]{{0,35}}\bwhen i\b"),
    # 한국어 — 서술 종결 + 증상 / 구매·사용 + 증상
    (3.0, SYM_KO + r"[^.!?\n]{0,25}(?:네요|하네|해요|더라|더만|한다|심하|있음|났|생겼|느껴|거리)"),
    (3.0, r"(?:샀는데|샀더니|쓰는데|썼는데|산 ?지|쓴 ?지|사용 ?중|사용기|후기)"
          r"[^.!?\n]{0,80}" + SYM_KO),
]

# --- Tier 2: 약한 1인칭 경험 신호 (Tier1 미포착분 회수용) ---
CORE2: list[tuple[float, str]] = [
    (1.4, r"\bi (?:bought|purchased|ordered|pre-?ordered|picked up|installed|upgraded to|"
          r"switched to|replaced|changed to|got myself)\b"),
    (1.4, rf"\b{POSS}\s+(?:new |old |right |left |inner |outer |front |current )?{DEV}\b"),
    (1.4, r"\bi(?:'| ha)?ve (?:had|been using|been running|owned)\b"),
    (1.2, r"\b(?:my|personal) experience\b|\bpersonal experience report\b|\bin my case\b|"
          r"\bhappened to me\b|\bfor me it\b"),
    (1.2, r"\bi (?:tried|tested|rebooted|restarted|reset|reinstalled|contacted|"
          r"called samsung|went to the (?:store|centre|center|service))\b"),
    (1.2, r"\bhad (?:this|the same) problem\b|\bsame (?:issue|problem) (?:here|for me)\b|"
          r"\bi'?m done with\b|\bi regret\b"),
    (1.0, r"\bevery time i\b|\bwhenever i\b|\bwhen i (?:scroll|open|close|fold|unfold|use|press|swipe)\b"),
    # 한국어
    (1.4, r"(?:샀는데|샀더니|구매했는데|구매한 ?지|산 ?지|바꿨는데|받았는데)"),
    (1.4, r"(?:쓰는데|썼는데|쓴 ?지|사용 ?중|사용중|쓰다가|쓰고 ?있|써보니|사용기|후기)"),
    (1.2, r"(?:겪었|겪고 ?있|당했|생겼어|생겼네|났어요|났네)"),
]

# --- 증상어 존재 자체 (약한 보조) ---
SYM_PRESENT: list[tuple[float, str]] = [
    (1.0, rf"\b{SYM}\b"),
    (1.0, SYM_KO),
]

# ==========================================================================
# 2. VETO — 비현실(irrealis) / 전언 / 기사  (실측 P(firsthand|f) = 0.00~0.25)
#    클래스 라벨도 여기서 나온다.
# ==========================================================================

VETO: dict[str, list[tuple[float, str]]] = {}

VETO["worry"] = [
    (2.6, r"\b(?:thinking (?:of|about)|planning (?:to|on)|considering|debating|"
          r"trying to (?:choose|decide)|can'?t decide|torn between|in mind to change|"
          r"looking (?:to|into) (?:buy|get|switch))\b"),
    (2.6, r"\bshould i (?:buy|get|go|update|upgrade|switch|take|choose|wait|risk|pull)\b|"
          r"\bis it (?:still )?worth\b|\bworth it in \d{4}\b"),
    (2.6, r"\b(?:i'?m|im|i am) (?:scared|afraid|worried|concerned|hesitant|nervous|paranoid|wary)\b|"
          r"\bparanoia\b|\bgiv(?:ing|es) me pause\b|\bmy (?:only )?(?:concern|worry|fear)\b"),
    (2.2, r"\bfirst time (?:buying|getting|using|owning)\b"),
    (2.2, r"\bstill (?:a )?(?:problem|issue|thing)\b"),
    (1.8, r"\b(?:before|prior to) (?:i )?(?:buy|buying|purchas\w+|getting|pulling the trigger|ordering)\b"),
    (1.8, r"\bwill (?:it|the|this|my)\b[^.?!]{0,45}\b(?:be (?:ok|okay|fine|alright)|hold up|survive|last)\b"),
    (1.6, r"\bshare (?:with me )?your experiences?\b|\bfor those (?:who|of you) (?:have|own)\b"),
    (2.6, r"(?:살까|사려는데|사려고|살 ?생각|살까요|구매 ?전|구매하려|바꿀까|바꾸려|갈아탈까)"),
    (2.4, r"(?:걱정|우려|불안|무섭|겁나|찜찜|망설|고민(?:중|이|되|입니다|임|되네))"),
    (2.0, r"(?:괜찮을까|괜찮나요|괜찮은가|문제 ?없을까|없을까요)"),
]

VETO["question"] = [
    (2.6, r"\brecommendations?\b|\bwhat do you (?:recommend|guys use|use)\b|"
          r"\bwhich (?:one )?(?:do|would) you\b"),
    (2.6, r"\bwhich (?:one )?is better\b|\bis it better\b|\bwhat'?s better\b"),
    (2.4, r"\bdoes any(?:one|body) know\b|\bcan any(?:one|body) (?:recommend|suggest|help|tell)\b|"
          r"\bis there any(?:one|body) (?:using|who|that)\b"),
    (2.4, r"\bdo i need\b|\bdo i have to\b|\bis there (?:a|any) (?:way|option|alternative|similar)\b"),
    (2.0, r"\bhow (?:do|can|should) i\b|\bany (?:advice|tips|suggestions|thoughts|ideas)\b"),
    (1.8, r"\bhow (?:does|is) (?:the|it|your) \w+ feel\b|\bwhat is \w+ and \w+\?"),
    (2.6, r"(?:추천|뭐가 ?(?:나음|좋음|좋을까|낫[나냐]))"),
    (2.4, r"(?:어떰|어떤가요|어떤지|어때요|괜찮음\?|있음\?|없음\?|되나요|인가요|나요\?|까요\?|맞나요)"),
    (2.0, r"(?:문의|알려주|여쭤|질문|조언)"),
]

VETO["hearsay"] = [
    (2.8, r"\b(?:users?|people|others|many|owners|customers)\s+(?:are |have )?"
          r"(?:report(?:s|ed|ing)?|say(?:s|ing)?|complain(?:s|ed|ing)?|claim(?:s|ed|ing)?|must)\b"),
    (2.8, r"\baccording to\b|\breportedly\b|\bit is reported\b|\breports? (?:say|suggest|indicate)\b|"
          r"\bhas been traced to\b"),
    (2.6, r"\brumou?r(?:s|ed)?\b|\bleaker\b|\btipster\b|\bleaked (?:press|renders?|specs?)\b"),
    (2.4, r"\bi'?ve seen (?:a lot|many|some|quite|several|posts|reports)\b|"
          r"\bi (?:heard|read that|saw that|saw a post)\b"),
    (2.4, r"\b(?:some|certain|a number of|early|demo)\s+(?:\w+\s+){0,2}"
          r"(?:units|devices|purchasers?|buyers?|adopters?)\b"),
    (2.2, r"\b(?:release notes?|changelog|upstream|patch notes?|version \d{6,})\b"),
    (2.2, r"^\s*>{1,}|\bre:\s*re:|\bwelcome to the samsung community\b|"
          r"\bwe understand how frustrating\b"),
    (2.4, r"(?:라던데|하던데|다더라|카더라|들었|들은|올라왔|제보|한다고 ?하)"),
    (2.2, r"(?:루머|기사(?:에|를|가)|보도|전해|알려졌)"),
]

VETO["review"] = [
    (3.0, r"\d{1,2}\s?%\s*OFF\b|\bR\$\s?[\d.,]+"),
    # 프로모션 기사의 Pros/Cons 목록. 사용자 후기 제목 "Pros and Cons after using..." 는 제외.
    (2.6, r"\bPros\b(?!\s+and\s+cons)[\s\S]{0,500}\bCons\b"),
    (2.6, r"\b(?:launched|unveiled|announced|introduced)\s+(?:with|in|on|at|by|the|its)\b|"
          r"\bis expected to (?:feature|come|launch|receive|be)\b"),
    (2.6, r"\b(?:it house|phonearena|phone arena|bloomberg|sammobile|theelec|9to5\w*|gsmarena|"
          r"sammyguru|blogger @|digitalchat)\b"),
    (2.4, r"\bspecifications?\b|\bspec sheet\b|\bcoupon\b|\bprime day\b|\bdiscount\b|"
          r"\bon sale\b|\bdrops? (?:to |even )?\d+%|\breaches \d+ ?% ?off\b"),
    (2.2, r"\b\d+\s?mAh\b|\b\d+[,.]?\d*\s?nits\b|\bsnapdragon \d|\bdynamic amoled\b|\bip6\d\b|\bqhd\+"),
    (1.8, r"\bcompared to (?:its predecessor|last year'?s)\b|\beverything you need to know\b|"
          r"\bwhat makes the\b"),
    (1.8, r"(?:기사|출시(?:했|한다|된다)|공개했|발표했|스펙|가격은|할인(?:중|가))"),
]

# 결함 주장 자체를 무효화하는 신호 (부정 / 액세서리 주체)
DAMPEN: list[tuple[float, str]] = [
    (2.4, r"\bnever (?:been )?(?:an? )?(?:issue|problem)\b|\bno (?:issues?|problems?|complaints?)\b|"
          r"\bnothing (?:in particular|wrong|to complain)\b"),
    (2.2, r"\bwithout (?:any )?(?:issues?|problems?)\b|\bhaven'?t had (?:any )?(?:issues?|problems?)\b|"
          r"\bnever had (?:an? )?(?:issue|problem)\b"),
    (2.0, rf"\b(?:works?|working|ran|runs)\s+(?:just |perfectly |absolutely )?"
          r"(?:fine|great|well|perfectly|flawlessly|as intended)\b"),
    (2.0, rf"\b{ACC}\b[^.!?\n]{{0,40}}\b(?:broke|cracked|broken|discolou?r\w*|peel\w*)"),
    (1.6, r"(?:이상 ?없|문제 ?없|잘 ?되|잘 ?됨|괜찮음|없었|안 ?그[럼래])"),
]


def _c(pats: list[tuple[float, str]]):
    return [(w, re.compile(p, re.IGNORECASE | re.MULTILINE)) for w, p in pats]


_CORE1, _CORE2, _SYM = _c(CORE1), _c(CORE2), _c(SYM_PRESENT)
_VETO = {k: _c(v) for k, v in VETO.items()}
_DAMPEN = _c(DAMPEN)


# 유니코드 따옴표/대시 정규화 — "wife’s" 가 wife'?s 에 매칭되지 않던 실제 버그를 막는다
_NORM = str.maketrans({"’": "'", "‘": "'", "“": '"', "”": '"',
                       "–": "-", "—": "-", " ": " "})


def normalize(text: str) -> str:
    return (text or "").translate(_NORM)


def _sum(rules, text: str, tag: str) -> tuple[float, list[str]]:
    tot, ev = 0.0, []
    for w, rx in rules:
        m = rx.search(text)
        if m:
            tot += w
            ev.append(f"{tag}+{w}:{m.group(0)[:38].strip()}")
    return tot, ev


# ==========================================================================
# 3. 결정
# ==========================================================================

T_MARGIN = 1.0        # CORE - VETO 가 이 이상이면 firsthand (경쟁 신호를 이김)
T_VETO = 1.8          # VETO 가 이 이상이면 해당 양상 라벨을 채택
T_CORE = 2.6          # VETO 가 약할 때 CORE 단독으로 firsthand 로 보내는 임계
CORE1_CAP = 6.0       # Tier1 누적 상한 (긴 글이 과점수 받는 것 방지)
CORE2_CAP = 3.0
W_MEDIA = 3.0         # platform_kind=media 감점
SHORT_LEN = 140       # 짧은 커뮤니티 불평은 1인칭이 생략되기 쉽다


def classify(text: str, platform_kind: str | None = None,
             use_meta: bool = True) -> dict[str, Any]:
    """텍스트의 발화 양상을 분류한다.

    Args:
        text: 제목+본문 결합 텍스트.
        platform_kind: platforms.kind (media/community/official/...). 메타 신호.
        use_meta: False 면 platform_kind 를 무시하고 순수 렉시콘만 사용.

    Returns:
        {"label", "core", "veto", "veto_label", "margin", "evidence"}
    """
    t = normalize(text).strip()
    if not t:
        return {"label": "other", "core": 0.0, "veto": 0.0,
                "veto_label": "other", "margin": 0.0, "evidence": []}

    ev: list[str] = []

    c1, e = _sum(_CORE1, t, "core1"); ev += e
    c2, e = _sum(_CORE2, t, "core2"); ev += e
    sp, e = _sum(_SYM, t, "sym"); ev += e
    dm, e = _sum(_DAMPEN, t, "damp"); ev += e

    # 부정 절은 대개 "다른 기기는 멀쩡했다"는 대비 서술이라, 실현 결함 결합(Tier1)이
    # 이미 잡힌 글에서는 상쇄력을 낮춘다. (예: "Never had an issue with Buds 2 Pro,
    # and now ..." 앞에 본인 Buds 3 Pro 고장 서술이 있는 경우)
    damp_w = 0.35 if c1 > 0 else 0.9
    core = min(c1, CORE1_CAP) + min(c2, CORE2_CAP) + sp - damp_w * dm

    # 1인칭이 없어도 짧은 커뮤니티 글에서 증상 단정은 불평인 경우가 많다
    if sp > 0 and c1 == 0 and c2 == 0 and len(t) <= SHORT_LEN:
        core += 2.2
        ev.append("short_complaint")

    veto_scores: dict[str, float] = {}
    for k, rules in _VETO.items():
        v, e = _sum(rules, t, k)
        veto_scores[k] = v
        ev += e

    if use_meta:
        if platform_kind == "media":
            veto_scores["review"] += W_MEDIA
            ev.append(f"meta:media+{W_MEDIA}")
        elif platform_kind in ("aggregator", "research", "regulatory"):
            veto_scores["review"] += 1.5

    # 제목이 '?'로 끝나면 문의/우려 쪽 가산 (약하게)
    head = t.split("\n", 1)[0][:180].rstrip()
    if head.endswith("?"):
        veto_scores["question"] += 0.8
        veto_scores["worry"] += 0.5
        ev.append("title_qmark")

    veto_label = max(veto_scores, key=lambda k: veto_scores[k])
    veto = veto_scores[veto_label]
    margin = core - veto

    if margin >= T_MARGIN:
        label = "firsthand"
    elif veto >= T_VETO:
        label = veto_label
    elif core >= T_CORE:
        label = "firsthand"
    else:
        label = "other"

    return {"label": label,
            "core": round(core, 2),
            "veto": round(veto, 2),
            "veto_label": veto_label,
            "veto_scores": {k: round(v, 2) for k, v in veto_scores.items()},
            "margin": round(margin, 2),
            "evidence": ev}


def is_firsthand(text: str, platform_kind: str | None = None) -> bool:
    """이진 판정 헬퍼 — 파이프라인 적용용."""
    return classify(text, platform_kind)["label"] == "firsthand"
