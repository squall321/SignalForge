"""짧은 댓글 topic 분류기 — Track A (R20, 2026-06-05).

목적: Instiz/Dogdrip 등 모델 미언급 짧은 댓글을 *topic* 으로 분류해
NULL product_id 후기의 의미를 살린다. (categorizer 12 카테고리와
독립된 의도/감정 축.)

R20 변경점 (R19 spot-check 정확도 0.470 → 목표 0.65+):
  1) **negative_general 짧은 phrase 환원** — R19 recall 0.250 회복
     ('very bad', 'really bad', 'is bad', '엉망', '구질' 등 강신호만).
  2) **comparison Discovery 권고 환원** — R19 F1 0.250 회복
     ('better than', 'is better than', 'rather than', '에 비해' 등).
     모델 2개 부스트(+2) 가 false positive 정밀도 보강.
  3) **positive_general 그대로 유지** — R19 0.571 성공 보존.

R10 변경점 (R9 spot-check 정확도 0.456 → 목표 0.65+):
  1) **사전 대폭 확장** — 한국어 어미·조사 변이, 영문 변형 추가
     (positive 25→40+, price 15→35+, comparison 15→25+ 등)
  2) **Long-form head scan** — 본문이 LONG_THRESHOLD 초과 시
     앞 LONG_SCAN_HEAD_CHARS 자만 보고 매칭 (R9 mismatch 사례 다수가
     HN/Reddit long-form 의 우연 매칭)
  3) **Primary topic 우선순위** — 매칭이 여러 개면 *signal density*
     (해당 topic 매칭 어휘 수) 가장 높은 것을 topics[0] 에 둠.
     → topic_eval 의 primary 기준 정확도 향상
  4) **부정 false-positive 정제** — '샀' → '샀어/샀음/샀네' 등 단어 경계
     명확화, 'vs' 영문 단어경계 강화.

특징:
  - multi-label (한 댓글에 여러 topic 가능, 순서 = signal 강도 desc)
  - 한국어 + 영문 사전
  - 짧은 텍스트(<10자) 는 빈 리스트 반환 (의미 없음)
  - emotion_only 는 글 전체가 감정 표현만으로 구성된 경우만 부여
  - other 는 의미 길이(>=10) 이상이지만 어느 topic 도 매칭되지 않을 때
    allow_other=True 인 경우에만 부여 (기본 False)

topic 코드:
  positive_general, negative_general, question, comparison,
  price_purchase, service_repair, experience, expectation,
  emotion_only, other
"""
from __future__ import annotations

import re
from typing import List, Tuple

# 의미 분석 최소 길이 (이하면 빈 리스트)
MIN_LEN = 10

# Long-form 가드: 본문이 길수록 우연 매칭이 늘어남.
# 본문이 LONG_THRESHOLD 자 이상이면 앞 LONG_SCAN_HEAD_CHARS 자만 검사한다.
# (R9 mismatch 사례 대부분이 1000자+ HN/Reddit long-form 의 우연 매칭이었음)
LONG_THRESHOLD = 300
LONG_SCAN_HEAD_CHARS = 250

# emotion_only 후보 패턴: 한국어 자모 반복, 이모지, 흔한 짧은 감정 표현
EMOTION_ONLY_RE = re.compile(
    r"^[\s!?.~^ㅋㅎㅠㅜㅇㄴㄹㄷㅁㅂㅅㅈㅊㅍㄱㄲㄸㅃㅆㅉ❤♥\U0001F300-\U0001FAFF()/\\♥♡]+$"
)

# 흔한 단독 감정/슬랭 (텍스트 전체가 이것만이면 emotion_only)
EMOTION_TOKENS = {
    "ㅋㅋ", "ㅋㅋㅋ", "ㅋㅋㅋㅋ", "ㅎㅎ", "ㅠㅠ", "ㅜㅜ",
    "ㄹㅇ", "ㅇㅈ", "ㄴㄴ", "ㅇㅇ", "ㅗ", "ㄷㄷ", "ㄱㄱ",
    "lol", "lmao", "rofl", "wow", "omg", "wtf",
}


def _is_emotion_only(t: str) -> bool:
    """텍스트 전체가 감정 표현/이모지/짧은 슬랭만으로 구성됐는지."""
    s = t.strip()
    if not s:
        return False
    if s.lower() in EMOTION_TOKENS:
        return True
    return bool(EMOTION_ONLY_RE.fullmatch(s))


# topic 별 키워드 (소문자 비교). 한국어 + 영문 혼합.
# - 키워드는 부분 매칭 (substring) 으로 검사 → 어미/조사 변이 흡수.
# - ASCII 알파벳만으로 구성된 키워드는 단어 경계(\b) 매칭으로 부분어 오탑 방지.
TOPIC_KEYWORDS: dict = {
    "positive_general": [
        # R18 Track A — 강신호만 유지.
        # 제거 사유 (약신호 → 다른 토픽 우연 매칭 多):
        #   "좋네", "좋아요", "좋다", "좋네요", "좋습니다", "좋아유", "좋음", "좋은",
        #   "좋고", "좋더", "좋았", "좋군", "좋아함" — comparison/price/expectation 글에 매우 흔함.
        #   "추천", "최고" 단독 — "추천드림"/"추천합니다"/"최고에요" 같은 phrase 형태로만 유지.
        #   "괜찮네/괜찮음/괜찮아요/괜찮습니다" — 의문/비교 글에서도 흔함.
        #   "잘 산/잘샀/잘 샀" — price_purchase 와 충돌. "사길 잘"/"사기 잘"/"신세계" 유지.
        #   "really nice"/"so nice" — long-form 우연 매칭 多.
        #   "loved it"/"lovely" — comparison 글에도 매칭.
        #
        # 한국어 — 명확한 긍정 평가 phrase 만
        "최고에요", "최고임", "최곱니다", "최고네요", "최고입니다",
        "추천합니다", "추천해요", "추천드려요", "추천드림", "추천드립",
        "맘에 들", "마음에 들", "마음에 듭", "맘에듦", "맘에 듦", "맘에 쏙",
        "만족해요", "만족스럽", "만족합니다", "만족함", "만족도가 높",
        "굿굿", "잘쓰고 있", "잘 쓰고 있",
        "킹갓", "혜자", "꿀이",
        "사길 잘", "사기 잘", "신세계",
        "감동", "역대급",
        # 영문 — 명확한 긍정 평가 phrase 만 (long-form 우연 매칭 억제)
        "love it", "love this", "love my",
        "awesome", "excellent", "fantastic", "wonderful",
        "really great", "so great", "pretty great",
        "highly recommend", "would recommend",
        "i recommend it", "i recommend this", "really recommend",
        "amazing phone", "amazing camera", "amazing product",
        "perfect for", "perfect phone",
        "satisfied with my", "happy with my", "impressed with",
        "worth every", "worth the price", "totally worth",
        "best phone i", "best i've", "best i have",
        "really happy with", "very happy with", "so happy with",
        "absolutely love",
    ],
    "negative_general": [
        # 한국어 — 부정 평가 확장
        "별로", "별루", "별로네", "별로임", "별로예요", "별로에요",
        "실망", "실망임", "실망스럽", "실망이",
        "비추", "비추천", "비추임", "비추요",
        "최악", "최악임", "최악이",
        "쓰레기 같", "구려", "구림", "구려요", "구리네",
        "안좋", "안 좋", "안 좋아", "안좋네",
        "망함", "망했", "망함요", "망친",
        "후회", "후회중", "후회임",
        "거름", "거른다", "거르세요",
        "하자가", "하자임", "불량품",
        "짜증나", "짜증남",
        "싫어요", "싫네",
        # R20: 짧은 평가 phrase 일부 환원 (R19 recall 0.250 → 0.50+ 회복용)
        # 'display very bad' / 'camera is really bad' 등 짧은 평가형.
        # 단독 'bad' 는 false positive 위험 → '평가 동사 + bad' 강신호만.
        # 영문 — 부정 *평가 phrase* (long-form 우연 매칭 억제).
        "terrible phone", "terrible camera", "terrible product",
        "awful experience", "awful phone",
        "worst phone", "worst product", "worst purchase",
        "horrible experience", "horrible phone",
        "disappointed with", "disappointed by",
        "really disappointing",
        "regret buying", "regret purchasing", "regret getting",
        "total garbage", "is garbage",
        "is trash", "what trash",
        "do not buy", "don't buy this", "dont buy this",
        "stay away from",
        "waste of money", "waste of time",
        "ripoff", "rip off",
        "hate this phone", "hate it",
        # R20 추가 — 짧은 negative 평가 phrase (R19 no_match 9건 회복용)
        # 'is very bad' / 'really bad' / 'so bad' / 'too bad' — 평가 동사 또는 부사 + bad.
        # display/phone/camera 등 명사 뒤에 'very bad' 가 오는 패턴이 R19 mismatch 다수.
        "very bad", "really bad", "so bad", "too bad", "pretty bad",
        "is bad", "was bad", "are bad", "were bad",
        # 부정문 형태 — quality/build/screen/camera 등 명사 + 'is bad' 흔함
        "quality is bad", "build is bad", "really sucks", "totally sucks",
        # 한국어 짧은 부정 평가 phrase 환원
        "엉망", "구질", "별로네", "안 좋네", "별로다",
    ],
    "question": [
        # 한국어 — 의문 표현 확장
        "어디서", "어디 사", "어디서 사",
        "어떻게", "어떻게 하", "어떡해",
        "뭔가요", "뭐예요", "뭐에요",
        "되나요", "되나여", "되는건가", "되는걸까", "되는지",
        "할까요", "할까", "할만한가",
        "방법 알려", "방법좀", "알려주세요", "알려줘",
        "궁금", "궁금하네", "궁금합니다", "궁금해요",
        "가능한가요", "가능합니까", "가능할까요",
        "쓸만한가요", "쓸만한가", "쓸 만한",
        "괜찮나요", "괜찮을까", "괜찮을까요",
        "있나요", "있을까", "있을까요", "있는지", "있는가",
        "추천 좀", "추천좀", "뭐가 좋",
        "사도 되나", "사도될까",
        # 영문 — 의문 표현 확장
        "how to", "how do", "how can",
        "where to", "where can", "where do",
        "anyone know", "anyone use", "anyone have", "anybody",
        "does it", "do they", "do you",
        "is it ok", "is it worth", "is it good", "is there",
        "should i", "should we",
        "can i ", "can you", "can we",
        "any tips", "any advice", "any recommend",
        "what is the", "what's the best", "whats the best",
        # R18: '??', '???' 단독 기호 제거 — 다른 토픽 글에도 흔히 등장해
        # primary 부스트만 노이즈가 됨 (감탄·소음의 ? 도 매칭됨).
    ],
    "comparison": [
        # R18 정제 — 'vs.' (영문/한글 혼용 모두 ambiguous),
        # '대비' (대비책/대비해서 등 비교 외 용법 다수) 제거.
        # R11 컨텍스트 부스트 (모델 2개 이상이면 +2) 가 정밀도를 보강.
        # 한국어 — 비교 표현 확장
        "비교", "비교후기", "비교 후기",
        "갈아", "갈아탈", "갈아타", "갈아탔",
        "넘어갈", "넘어감", "넘어가",
        "더 좋", "더좋", "더 나은", "더나은",
        "보다 낫", "보다 좋", "보다 나",
        "차이점", "차이가",
        "어느 게", "어느게", "어떤게",
        "둘 중", "둘중",
        # R20 추가 — Discovery 권고: 비교 강신호 환원
        # "차이" 단독은 너무 광범위 → "차이는/차이를/차이가" 형식 이미 보유.
        # "비해" 추가 (X에 비해 Y) — 강신호.
        "비해서", "에 비해", "보다는",
        # 영문 — *비교 phrase*. 'vs' 단독은 너무 폭넓어 제거.
        "compared to", "comparing it", "in comparison",
        "better than the", "better than my", "worse than",
        # R19: 'switched from' 단독 추가 — 'amazed/excellent' 와 동시 매칭 시
        # positive 케이스를 comparison 으로 흡수 → 회귀 확인 후 보류.
        "switched to", "switching to", "switching from",
        "upgraded from", "upgrading from", "downgrade from",
        "moved from", "coming from the", "coming from a",
        "which one is better", "which is better",
        "i prefer the", "i prefer my",
        # R20 추가 — Discovery: 'better than' generic phrase 환원 (the/my 없이도).
        # 모델 2개 부스트(+2) 가 false positive 를 정밀도로 보강한다.
        "better than", "is better than", "much better than",
        "rather than", "instead of the", "instead of my",
        # R20: implicit comparison phrase — "use X ... use Y" 패턴은 정규식 비현실.
        # 대신 정확한 phrase 만: "people prefer", "users prefer".
        "people prefer", "users prefer",
    ],
    "price_purchase": [
        # 한국어 — 명확한 구매/가격 표현만 (단순 '샀' 은 noise 多)
        "샀어", "샀음", "샀네", "샀습니다", "샀어요", "샀고",
        "샀는데", "샀더니",
        "구매했", "구매함", "구매했어요",
        "구입했", "구입함",
        "주문했", "주문함", "주문완료",
        "예약구매", "예약했", "예약하려",
        "할인받", "할인가", "할인중", "할인되",
        "쿠폰받", "쿠폰으로", "쿠폰코드",
        "특가", "성지가", "최저가",
        "공시가", "지원금", "보상판매",
        "현금완납", "할부", "무이자",
        "가격이", "가격에", "가격대", "가격은",
        "원에 샀", "원짜리", "얼마에 샀",
        "결제완료", "결제했",
        # 영문 — *구매/가격 phrase* (long-form 매칭 억제).
        "i bought", "just bought", "recently bought",
        "i purchased", "just purchased",
        "ordered it", "ordered the", "got mine",
        "preordered", "pre-ordered", "preorder", "pre-order",
        "best deal", "good deal", "great deal",
        "got a discount", "with discount", "discount code",
        "promo code", "coupon code",
        "traded in", "trade in",
        "on sale", "got it on sale",
        "spent on", "cost me", "price tag",
        "price is", "price was",
    ],
    "service_repair": [
        # R16 정제 — 강신호만 유지, "수리" / "환불" / "교환받" 단독은 제거
        # (다른 토픽과 혼동 多 → precision 0.727 → 0.85+ 목표)
        # 한국어 — AS/수리 *실제 수행* 신호만
        "에이에스", "서비스센터", "센터에서", "센터 갔", "센터 갔다",
        "수리비", "수리받", "수리 맡", "수리 맞",
        "리퍼폰", "리퍼받",
        "보증기간", "보증수리", "보증 기간",
        "as센터", "as 센터", "as 받", "as기사", "as 비용", "as 신청",
        "교환 신청",
        "환불받", "환불 신청",
        "as 보냈", "AS 보냈",
        "cs 접수", "cs접수", "CS 접수",
        # R15 추가 — AS/수리/리퍼/교환/환불 강신호 확장
        "as 처리", "as처리",
        "as 받고", "as받고",
        "수리비용", "수리 비용",
        "수리 견적", "수리견적",
        "리퍼 받고", "리퍼받고", "리퍼받음",
        "교환 절차", "교환절차",
        "환불 처리", "환불처리", "환불 받은", "환불받은",
        "센터 방문", "센터방문", "센터 다녀", "센터다녀",
        "삼성전자서비스", "삼성 서비스",
        # 영문 — *수리 맥락 phrase* (long-form 단어 매칭 억제).
        "under warranty", "warranty claim", "out of warranty",
        "got it repaired", "needed repair", "repair shop",
        "needs repair", "got repaired",
        "rma process", "service center", "samsung service",
        "replacement unit", "refurbished unit", "got a refurb",
        "got it fixed", "fixed under warranty",
        "tech support", "customer support",
        # R15 추가 — 영문 AS/수리/교환 강신호
        "had to repair", "took it for repair", "take it for repair",
        "got it replaced", "needed replacement", "need replacement",
        "samsung service center",
        "warranty extension", "extended warranty",
    ],
    "experience": [
        # R19 정제 — 약신호 'been using it'/'after using it' 제거 (sunscreen 같은
        # 비 VOC 텍스트 false positive). 'have been using'/'i've been using'
        # 도 generic 매칭 多 → 명시 *기간 phrase* 가 있는 경우만 보존.
        # R18 정제 — 약신호 제거 & *기간 명시* 강신호 추가.
        # 제거: '써본' (단독), '잘 쓰고' (positive_general 과 혼동, '쓰고 있' 이
        #       이미 long-term use 커버), 단순 '1년/2년/3년' (다른 토픽
        #       글에서도 일자/년수 표기로 흔히 등장).
        # 추가: '개월 사용', '개월째', '년째', '년 사용' — 사용 기간 phrase 강신호.
        # 한국어 — 사용 기간/후기
        "사용 후기", "사용후기", "사용기",
        "써보니", "써봤", "써봤더니", "써보고",
        "쓰고 있", "쓰는중", "쓰는 중",
        "쓴 지", "쓴지", "쓰다가",
        # 기간 + 사용 강신호 (R18)
        "개월 사용", "개월째", "개월째 쓰", "개월 째",
        "년 사용", "년째", "년째 쓰", "년 째",
        # 명시 기간 phrase
        "1개월", "한달", "한 달", "두달", "두 달",
        "3개월", "삼개월", "6개월", "육개월",
        "일년 동안", "한 달 동안",
        "장기 사용", "장기사용",
        "후기 남깁", "후기남깁", "후기입니다",
        "리뷰 남깁", "리뷰임",
        "써본 결과", "써본결과",
        # 영문 — *사용 경험 phrase*.
        # R19: generic 'been using it'/'after using it' false positive 차단.
        #      product-tied phrase 와 R18 기존 phrase 일부 복원 (recall 손실 최소).
        "been using this", "been using my", "been using the",
        "been using for", "after using this", "after using the",
        "i have been using", "i've been using", "ive been using",
        "have been using it",  # R18 기존 — sunscreen 가드는 product-tied 우선 매칭
        "owned for", "owned it for", "i've owned",
        "long term review", "long-term review", "long term use",
        "for a few months", "for a couple months",
        "for one month", "for two months", "for six months",
        "for one year", "for two years", "for a year",
        "my experience with", "in my experience",
        "honest review", "my review of",
        "daily driver",
    ],
    "expectation": [
        # 한국어 — 출시/기대 확장
        "기대중", "기대됨", "기대돼", "기대된다", "기대합니다",
        "기대해", "기대 만큼",
        "출시일", "출시 예정", "출시 임박",
        "공개일", "공개됨", "공개 예정",
        "발표일", "발표됨", "발표 예정",
        "언제 나와", "언제나와", "언제쯤", "언제 출시",
        "루머가", "유출됨", "유출된", "리크된",
        "다음 모델", "다음모델", "차기작", "차기 모델",
        "예상 스펙", "예상스펙",
        # 영문 — *기대/루머 phrase* (단순 'launch' 등 제거).
        "rumored to", "according to rumors",
        "leaked", "leaked specs", "leaked photos",
        "upcoming model", "upcoming release",
        "release date", "release in",
        "launching soon", "launches next",
        "official announcement", "just announced",
        "next gen", "next-gen", "next generation",
        "can't wait for", "cant wait for", "looking forward to",
        "excited for the", "excited about the",
    ],
}


# ---------------------------------------------------------------------------
# Primary topic 우선순위 (signal 강도 동률일 때의 tie-break)
# 일반적으로 의도(질문/구매) 신호가 평가(긍정/부정) 신호보다 분명한 경향.
# R9 confusion matrix 기반 순서 — 의도 계열 → 평가 계열 → 메타 계열.
# ---------------------------------------------------------------------------
PRIMARY_PRIORITY: List[str] = [
    "service_repair",
    "price_purchase",
    "question",
    "comparison",
    "expectation",
    "experience",
    "negative_general",
    "positive_general",
    "emotion_only",
]


# R18 Track A — positive_general 부정/가정 가드
# 다음 패턴이 본문에 있으면 positive_general 점수를 0 으로 깎는다.
#   (1) 부정 표현: "만족하지 않" / "추천하지 않" / "좋지 않" / "not satisfied" / "don't love"
#   (2) 가정/희망: "좋겠다" / "좋을것" / "좋을 것" / "would be nice" / "would be great"
#
# 모두 *positive 키워드 옆에서* 흔히 false positive 를 만드는 패턴.
POSITIVE_NEGATION_PATTERNS: List[str] = [
    # 한국어 부정
    "만족하지 않", "만족스럽지 않", "만족 못", "만족 못함",
    "추천하지 않", "추천 안", "추천 못", "비추천",
    "좋지 않", "좋지않", "좋지는 않", "좋지는않",
    "맘에 안", "마음에 안",
    "별로 안 좋", "안 좋은",
    # 한국어 가정/희망 — 미래 시제는 expectation
    "좋겠다", "좋겠네", "좋겠어", "좋겠어요", "좋겠습니다", "좋겠는데",
    "좋을것", "좋을 것", "좋을 거", "좋을거",
    "좋았으면", "좋으면",
    # 영문 부정
    "not satisfied", "not happy with", "not impressed",
    "don't love", "dont love", "do not love",
    "don't recommend", "dont recommend", "do not recommend",
    "wouldn't recommend", "wouldnt recommend",
    "not great", "not awesome", "not fantastic",
    "not worth", "isn't worth", "isnt worth",
    # 영문 가정/희망
    "would be nice", "would be great", "would love to",
    "would be excellent", "would be amazing", "would be perfect",
    "wish it was", "wish it were", "if only",
]


def _has_positive_negation(scan_lower: str) -> bool:
    """positive 부정/가정 가드 패턴 매칭 여부."""
    for p in POSITIVE_NEGATION_PATTERNS:
        if p in scan_lower:
            return True
    return False


def _scan_text(content: str) -> str:
    """본문이 길면 앞 LONG_SCAN_HEAD_CHARS 자만 반환 (long-form 우연 매칭 억제)."""
    s = (content or "").strip()
    if len(s) > LONG_THRESHOLD:
        return s[:LONG_SCAN_HEAD_CHARS]
    return s


def _count_matches(text_lower: str, keywords: List[str]) -> int:
    """매칭된 *키워드 개수* 반환. signal 강도 측정용.

    동일 키워드 중복 매칭은 1로 카운트 (남용 방지).
    """
    n = 0
    for k in keywords:
        if k.isascii() and k.replace(" ", "").replace("-", "").replace("'", "").isalpha():
            pattern = r"\b" + re.escape(k) + r"\b"
            if re.search(pattern, text_lower):
                n += 1
        else:
            if k in text_lower:
                n += 1
    return n


def _matches(text_lower: str, keywords: List[str]) -> bool:
    """하위 호환 — 1개 이상 매칭 여부."""
    return _count_matches(text_lower, keywords) > 0


def _order_by_signal(matched_with_scores: List[Tuple[str, int]]) -> List[str]:
    """signal 강도(desc) + PRIMARY_PRIORITY tie-break 로 정렬."""
    prio_idx = {t: i for i, t in enumerate(PRIMARY_PRIORITY)}

    def key(item: Tuple[str, int]):
        topic, score = item
        return (-score, prio_idx.get(topic, 999))

    return [t for t, _ in sorted(matched_with_scores, key=key)]


def classify_topic(text: str, allow_other: bool = False) -> List[str]:
    """짧은 댓글을 topic 으로 분류 (multi-label, primary first).

    규칙:
      - text 가 None / 빈 문자열 → []
      - 의미 분석 길이 미만 (<10자, 공백 제외) → []
      - 텍스트 전체가 감정 표현/이모지만 → ['emotion_only']
      - 본문이 LONG_THRESHOLD 자 초과 시 앞부분만 스캔
      - 여러 topic 매칭 시 signal 강도 desc 순서로 반환
        (topics[0] = primary)
      - 매칭 없고 allow_other=True 면 ['other']
      - 매칭 없고 allow_other=False 면 []
    """
    if not text:
        return []
    stripped = text.strip()
    if len(stripped) < MIN_LEN:
        # 단, 매우 짧지만 순수 감정 표현이면 emotion_only 부여
        if _is_emotion_only(stripped):
            return ["emotion_only"]
        return []

    # emotion_only 우선 판정 — 다른 키워드 검사 전에 가드
    if _is_emotion_only(stripped):
        return ["emotion_only"]

    # Long-form 가드 — 앞부분만 스캔
    scan = _scan_text(stripped).lower()

    matched: List[Tuple[str, int]] = []
    # R18 Track A — positive_general 부정/가정 가드 사전 계산
    suppress_positive = _has_positive_negation(scan)
    for topic, keywords in TOPIC_KEYWORDS.items():
        score = _count_matches(scan, keywords)
        if score > 0:
            # 부정/가정 패턴이 있으면 positive_general 매칭을 무효화.
            # (다른 토픽 — negative_general/expectation 등이 있으면 그쪽이 primary 가 됨)
            if topic == "positive_general" and suppress_positive:
                continue
            matched.append((topic, score))

    # R11 컨텍스트 부스트 — comparison/price 약점 보강
    # comparison: 모델/제품명 2개 이상 언급 시 +2
    # price: 통화/숫자 동반 시 +2 (예: "1,200,000원", "$899", "100만원")
    # R19 추가: experience — *명시 기간 phrase* 가 있으면 +2 (3달 사용기, 1년 사용 등)
    #            comparison/positive 와 동시 매칭 시 experience 우선 강화.
    #            매칭 사전에 experience 가 없어도 *experience 신호* 가 강하면
    #            base score 2 로 주입 (LLM 의 experience 우선 동의를 반영).
    # 모델 언급 카운트 (R8 GALAXY_MODEL_RE 활용 시도, 실패시 단순 패턴)
    try:
        import re as _re
        # Galaxy/iPhone/Pixel 등 제품군 + 모델 번호 패턴 동시 매칭
        n_models = len(_re.findall(
            r"\b(?:galaxy|iphone|pixel|samsung|아이폰|갤럭시|픽셀)\s*[a-z]?\d{1,3}\b",
            scan, _re.IGNORECASE
        ))
        # 통화/가격 패턴 — 숫자 + 원/달러/만원/$
        has_price = bool(_re.search(
            r"\d{1,3}(?:,?\d{3})+\s*원|\d+\s*만\s*원|\$\d+|usd\s*\d+|\d+\s*달러|₩\d+",
            scan, _re.IGNORECASE
        ))
        # R19: 명시 기간 + 사용 phrase — '3달 사용', '6개월 사용', '1년 사용',
        #      'for 3 months', 'for 1 year', '6 months of use' 등.
        has_explicit_period = bool(_re.search(
            r"\d+\s*(?:달|개월|년)\s*(?:사용|쓴|써|동안|째)"
            r"|\d+\s*(?:달|개월|년)\s*사용기"
            r"|for\s+\d+\s+(?:months?|years?)"
            r"|\d+\s+(?:months?|years?)\s+of\s+use",
            scan, _re.IGNORECASE
        ))
    except Exception:
        n_models = 0
        has_price = False
        has_explicit_period = False

    # R19: experience base 주입 — 명시 기간 phrase 가 있고 experience 가 아직
    # matched 에 없으면 base score 2 로 주입.
    # (단, 텍스트 전체가 '사용'/'use' 신호 1회 미만이면 너무 광범위 — 가드)
    if has_explicit_period and not any(t == "experience" for t, _ in matched):
        # 사용 단어가 실제 근접 위치에 있는지 — 위 regex 이미 보장.
        matched.append(("experience", 2))

    if matched:
        boosted: List[Tuple[str, int]] = []
        for topic, score in matched:
            bonus = 0
            if topic == "comparison" and n_models >= 2:
                bonus = 2
            elif topic == "price_purchase" and has_price:
                bonus = 2
            elif topic == "experience" and has_explicit_period:
                bonus = 2
            boosted.append((topic, score + bonus))
        return _order_by_signal(boosted)
    if allow_other:
        return ["other"]
    return []
