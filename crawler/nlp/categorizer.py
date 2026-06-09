"""카테고리 분류 — 키워드 매칭 기반

12 코드 (voc_categories 일치): battery, camera, display, performance,
software, build_quality, price, design, connectivity, ai_features,
accessories, comparison, review.
추가 13번째: model_mention (Galaxy 모델 단독 언급).
추가 14번째 (옵션): others — allow_others=True 시 의미 길이 이상의 미매칭 텍스트.

P3.5/3.6 확장 (2026-06-03):
- 한국어 어미/조사/슬랭 변이 사전 대폭 확장 (배빨, 빨리닳, 발열심함, 노캔, 액정파손 …)
- Galaxy 모델 정규식 — 매칭 시 'model_mention' 카테고리 부여
- allow_others 옵션 — 최소 길이 이상의 미분류 텍스트에 'others' 부여
- 짧은 텍스트(<20자) 는 빈 리스트 유지 (분석 의미 약함)
"""
import re
from typing import List

# @lat: CATEGORY_KEYWORDS — [[categories]] 참조. 신규 카테고리 추가 시 3곳 동시 업데이트.
# 카테고리별 키워드 (소문자)
CATEGORY_KEYWORDS: dict = {
    "battery": [
        "battery", "battery life", "drain", "charging", "fast charge",
        "wired charging", "wireless charging", "power bank", "mah", "endurance",
        "배터리", "충전", "방전", "전력", "배터리광탈", "광탈", "충전기", "충전속도",
        "급속충전", "무선충전", "유선충전", "보조배터리",
        # 인포멀 확장
        "배빨", "배빨리", "빨리닳", "빨리닳음", "빠르게닳", "빨리달", "닳아요", "닳음",
        "충전느려", "충전느림", "충전빠름", "충전빠른", "급속", "방전빠름",
        "전기세", "충전케이블", "충전포트",
    ],
    "camera": [
        "camera", "photo", "picture", "zoom", "night mode", "portrait",
        "video", "4k", "8k", "selfie", "lens", "sensor",
        "telephoto", "ultrawide", "ultra wide", "expert raw",
        "카메라", "사진", "촬영", "줌", "야간", "야간모드", "셀카", "동영상",
        "광학줌", "디지털줌", "망원", "초광각", "인물모드", "렌즈",
        # 인포멀 확장
        "찍사", "찍어보니", "사진잘", "화질", "픽셀", "야간촬영", "저조도",
        "후면카메라", "전면카메라", "카툭튀", "캠코더",
    ],
    "display": [
        "screen", "display", "brightness", "amoled", "refresh rate", "120hz",
        "adaptive", "resolution", "qhd", "always on", "panel",
        "ltpo", "peak brightness", "nits",
        "화면", "디스플레이", "밝기", "주사율", "해상도", "패널", "잔상",
        "번인", "올웨이즈온", "엣지패널",
        # 인포멀 확장
        "스크린", "디플", "디피", "노치", "펀치홀", "주름", "구겨짐",
        "베젤", "테두리", "화면꺼짐", "터치먹통", "고스트터치",
    ],
    "performance": [
        "lag", "laggy", "slow", "heating", "heat", "overheat", "overheating",
        "thermal", "fps", "stutter", "stuttering", "snapdragon", "exynos",
        "performance", "smooth", "speed", "benchmark", "throttle", "throttling",
        "dimensity", "tensor", "antutu",
        "발열", "버벅", "느림", "빠름", "성능", "스냅드래곤", "엑시노스",
        "쓰로틀", "프레임", "끊김", "랙", "뜨거", "뜨겁",
        # 인포멀 확장
        "발열심함", "발열심해", "따끔", "뜨끈", "후끈", "렉", "버벅임",
        "느려짐", "빠릿", "빠릿빠릿", "최적화", "최적화안됨", "버벅거림",
    ],
    "software": [
        "oneui", "one ui", "update", "bug", "crash", "crashing", "software",
        "android", "firmware", "patch", "security update", "samsung dex",
        "bloatware", "glitch", "freeze", "reboot", "bootloop",
        "wear os", "knox", "secure folder",
        "원유아이", "원ui", "업데이트", "버그", "충돌", "소프트웨어",
        "삼성덱스", "녹스", "보안폴더", "안드로이드", "펌웨어", "튕김",
        # 인포멀 확장
        "튕겨", "리부팅", "재부팅", "꺼짐", "강종", "어플꺼짐", "앱크래시",
        "업뎃", "업뎃후", "업뎃하고", "베타", "원ui7", "원ui8", "굿락",
    ],
    "build_quality": [
        "crack", "scratch", "build", "hinge", "durability", "drop",
        "water resistant", "ip68", "ip 68", "gorilla glass", "bend", "flex",
        "armor aluminum", "titanium frame",
        "힌지", "파손", "내구성", "방수", "방진", "스크래치", "기스",
        "고릴라글래스", "유리", "액정파손",
        # 인포멀 확장
        "베젤들뜸", "흠집", "긁힘", "찍힘", "낙하", "떨어뜨림", "물에빠짐",
        "케이스", "보호필름", "강화유리", "마감", "마감불량",
    ],
    "price": [
        "price", "expensive", "cheap", "value", "worth", "cost",
        "deal", "discount", "overpriced", "affordable", "msrp", "rrp",
        "trade in", "trade-in",
        "가격", "비싸", "저렴", "가성비", "할인", "보상판매", "지원금",
        "공시지원금", "성지", "특가", "최저가",
        # 인포멀 확장
        "비쌈", "비싸요", "비싸네", "싸요", "싸네", "착한가격", "가심비",
        "현금완납", "할부", "이벤트가", "프로모션",
    ],
    "design": [
        "design", "color", "form factor", "thin", "slim", "weight",
        "compact", "aesthetic", "beautiful", "ugly", "titanium",
        "phantom black", "titanium gray", "titanium violet",
        "디자인", "색상", "무게", "얇은", "두꺼운", "두께", "그립감",
        "베젤", "마감",
        # 인포멀 확장
        "이쁨", "이뻐", "이쁜", "예쁨", "투박", "묵직", "가벼움", "가볍",
        "한손", "그립", "촉감", "재질감",
    ],
    "connectivity": [
        "wifi", "wi-fi", "bluetooth", "5g", "lte", "signal",
        "nfc", "usb", "usb-c", "sim", "esim", "connection", "network",
        "wifi 7", "wifi 6e",
        "연결", "신호", "와이파이", "블루투스", "통신", "기지국",
        "수신율", "유심", "이심",
        # 인포멀 확장
        "끊김", "와파", "블투", "페어링", "테더링", "핫스팟", "데이터",
        "통화품질", "통화끊김", "수신감도",
    ],
    "ai_features": [
        "ai", "galaxy ai", "circle to search", "live translate",
        "generative edit", "chat assist", "ai wallpaper", "note assist",
        "now brief", "now bar", "transcript assist", "sketch to image",
        "갤럭시 ai", "갤럭시ai", "서클투서치", "라이브 번역", "인공지능",
        "생성형", "녹음어시스트", "포토 어시스트",
        # 인포멀 확장
        "갤ai", "ai기능", "ai번역", "실시간번역", "통화번역", "음성인식",
        "빅스비", "bixby",
    ],
    "accessories": [
        "case", "cover", "s pen", "pen nib", "accessories", "charger",
        "cable", "earphone", "galaxy buds", "watch strap", "stylus",
        "케이스", "s펜", "충전기", "액세서리", "이어폰", "버즈",
        "워치", "갤럭시버즈", "갤럭시워치", "필름",
        # 인포멀 확장 (audio 강화)
        "음질", "통화품질", "마이크", "노캔", "anc", "노이즈캔슬링",
        "사운드", "스피커", "이어팁", "지문인식",
    ],
    "comparison": [
        "apple", "iphone", "pixel", "google pixel", "vs ", " vs",
        "compared to", "better than", "worse than", "switch from",
        "switching from", "moved from", "coming from",
        "huawei", "xiaomi", "oneplus", "oppo", "vivo",
        "아이폰", "픽셀", "비교", "전환", "기변", "갈아탔", "넘어왔",
        "샤오미", "화웨이", "원플러스",
        # 인포멀 확장
        "갈아탈까", "넘어갈까", "기변각", "넘사벽", "vs아이폰", "삼성vs",
    ],
    "review": [
        "review", "hands on", "hands-on", "first look", "impressions",
        "unboxing", "in depth", "long term", "long-term", "month later",
        "사용기", "후기", "개봉기", "리뷰", "소감", "써본", "사용해본",
        "실사용", "장단점",
        # 인포멀 확장
        "한달후기", "일주일후기", "써보니", "장기사용", "단점", "장점",
    ],
}

# Galaxy 모델 정규식 — 트랙 C/R6 사양 (옛 모델까지 매칭).
# 매칭 시 'model_mention' 카테고리 부여 (13번째).
# R6 (2026-06-04): Note Edge / S Edge / Active / FE / S\d+ 5G 보강.
GALAXY_MODEL_RE = re.compile(
    r"\b(?:Galaxy\s+)?"
    r"(?:S\s*\d{1,2}(?:\s*Edge|\s+Ultra|\s+Plus|\+|\s+FE|\s+5G)?"
    r"|Z\s*Fold\s*\d?"
    r"|Z\s*Flip\s*\d?"
    r"|Fold\s*\d?"
    r"|Flip\s*\d?"
    r"|Note\s*\d+(?:\s+Edge)?"
    r"|Note\s*Edge"
    r"|A\d{1,2}"
    r"|J\d{1,2}"
    r"|Tab\s*S\s*\d+"
    r"|Watch\s*Active\s*\d?"
    r"|Buds\s*Live"
    r"|Active"
    r"|FE)\b",
    re.IGNORECASE,
)

# 한국어 모델 약어 (정규식 \b 가 한글에 동작하지 않으므로 별도 패턴).
_KO_MODEL_PATTERNS = [
    re.compile(r"갤(?:럭시)?\s?[sa]\s?\d{2}"),
    re.compile(r"폴드\s?\d?"),
    re.compile(r"플립\s?\d?"),
    re.compile(r"노트\s?\d{2}"),
    re.compile(r"갤탭\s?[se]?\s?\d+"),
]


def _has_model_mention(text: str) -> bool:
    """텍스트가 Galaxy 제품 모델을 언급하는가."""
    if GALAXY_MODEL_RE.search(text):
        return True
    return any(p.search(text) for p in _KO_MODEL_PATTERNS)


# 'others' 부여 최소 길이 — 너무 짧은 댓글은 분석 의미 약함.
MIN_OTHERS_LEN = 20


# @lat: classify_categories — [[nlp#Category Classification]] 참조.
def classify_categories(
    text: str,
    max_categories: int = 5,
    allow_others: bool = False,
) -> List[str]:
    """
    텍스트에서 해당하는 카테고리 코드 목록 반환.

    영어/한국어 키워드 사전 + Galaxy 모델 정규식.
    - ASCII 키워드: 단어 경계(\\b) 정규식.
    - 한국어 키워드: 단순 부분문자열 (조사·어미 변이 흡수).
    - 다어절 키워드(가중치 2): 정밀 매칭 우대.
    - Galaxy 모델 매칭 시 'model_mention' 카테고리 부여 + comparison 가산.
    - allow_others=True 시 매칭 0 이고 길이 >= MIN_OTHERS_LEN 이면 ['others'].

    Args:
        text: 분류할 텍스트.
        max_categories: 최대 카테고리 수.
        allow_others: 미매칭 + 충분한 길이일 때 'others' 부여 여부.

    Returns:
        카테고리 코드 리스트 (점수 내림차순, 예: ['battery', 'camera']).
    """
    if not text:
        return []

    text_lower = text.lower()
    scores: dict = {}

    for category, keywords in CATEGORY_KEYWORDS.items():
        score = 0
        for kw in keywords:
            if re.search(r'[\x00-\x7f]', kw):
                # ASCII: 단어 경계. 공백 포함 키워드는 escape 후 \b.
                pattern = r'\b' + re.escape(kw) + r'\b'
                hits = len(re.findall(pattern, text_lower))
            else:
                # 한국어: 단순 포함 (조사/어미 변이 흡수).
                hits = 1 if kw in text_lower else 0
            weight = 2 if ' ' in kw else 1
            score += hits * weight
        if score > 0:
            scores[category] = score

    # 모델 멘션 — 13번째 카테고리 'model_mention' 부여 + comparison 가산.
    if _has_model_mention(text_lower):
        scores["model_mention"] = scores.get("model_mention", 0) + 2
        scores["comparison"] = scores.get("comparison", 0) + 1

    if not scores:
        # others 옵션 — 길이 기준 충족 시만 부여 (짧은 슬랭은 노이즈).
        if allow_others and len(text.strip()) >= MIN_OTHERS_LEN:
            return ["others"]
        return []

    matched = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    return [cat for cat, _ in matched[:max_categories]]
