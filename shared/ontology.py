# 검색어를 VOC 개념(제품·세대·브랜드·기기·부품·증상·주제·시장·채널)으로 바꾸는 온톨로지
"""VOC 온톨로지 — 질의 해석의 정본.

왜 필요한가. 검색이 글자 일치라 같은 뜻도 표기가 다르면 못 찾았다.
  'S25 Ultra' 9,680건 · '갤럭시 S25 울트라' 88건 (2026-10-15 실측)
  '발열' 과 'overheating' 은 서로를 못 찾는다.
그런데 말뭉치는 이미 개념으로 주석돼 있다 — 제품(voc_product_links), 결함
부품×증상(voc_defects), 주제(categories). 질의만 같은 개념 공간으로 옮기면 된다.

개념은 새로 만들지 않고 정밀도를 다듬어 둔 기존 사전에서 가져온다(복제 금지).
  제품          crawler/base/product_match   (라이브 + 레거시 폴백, 타사 인접 가드)
  부품·증상     crawler/nlp/defect_extract   (함의 부품 _IMPLIED, 심각도 _SEVERITY)
  주제          crawler/nlp/categorizer       (categories 주석의 코드 체계)
여기 새로 두는 것은 개념 사이의 관계, 다국어 라벨, 그리고 **질의 전용 짧은 표현**뿐이다.
문서용 렉시콘은 정밀도 때문에 문맥을 요구하지만('dust' 단독은 방진 스펙), 질의는
사용자가 뜻을 직접 말한 것이라 '먼지'·'주름' 단독도 그 증상이다.

이 모듈은 DB 를 모른다. SQL 은 호출부(mcp-server/tools/semantic.py)가 만든다.
"""
import pathlib
import re
import sys
from dataclasses import asdict, dataclass, field
from typing import Dict, Iterable, List, Optional, Tuple

for _p in ("/crawler", str(pathlib.Path(__file__).resolve().parents[1] / "crawler")):
    if pathlib.Path(_p).is_dir() and _p not in sys.path:
        sys.path.append(_p)          # 끝에 붙인다 — 호출부 자신의 모듈(tools·db)이 이긴다

from base import model_codes as _mc  # noqa: E402
from base import product_match as _pm  # noqa: E402
from nlp import categorizer as _cat  # noqa: E402
from nlp import defect_extract as _dx  # noqa: E402


# ═════════ 개념 — 라벨 ═══════════════════════════════════════════════════
COMPONENT_LABELS: Dict[str, Tuple[str, str]] = {
    "device": ("기기 전체", "Whole device"),
    "hinge": ("힌지", "Hinge"),
    "display": ("디스플레이", "Display"),
    "battery": ("배터리", "Battery"),
    "camera": ("카메라", "Camera"),
    "charging_port": ("충전 포트", "Charging port"),
    "speaker": ("스피커", "Speaker"),
    "button": ("버튼", "Button"),
    "back_glass": ("후면 유리", "Back glass"),
    "frame": ("프레임·외관", "Frame"),
    "fingerprint": ("지문 센서", "Fingerprint sensor"),
    "software": ("소프트웨어", "Software"),
    "connectivity": ("통신·연결", "Connectivity"),
    "sim": ("유심", "SIM"),
    "thermal": ("발열 관리", "Thermal"),
    "s_pen": ("S펜", "S Pen"),
}
SYMPTOM_LABELS: Dict[str, Tuple[str, str]] = {
    "fire": ("발화·폭발", "Fire"), "swelling": ("배터리 부풀음", "Battery swelling"),
    "burn": ("화상", "Burn injury"), "shock": ("감전", "Electric shock"),
    "no_power": ("전원 안 켜짐", "Won't power on"), "boot_loop": ("무한 부팅", "Boot loop"),
    "not_working": ("작동 안 함", "Not working"), "no_charge": ("충전 안 됨", "Not charging"),
    "crack": ("깨짐·균열", "Crack"), "break": ("고장·파손", "Broken"),
    "water_damage": ("침수", "Water damage"), "dead_pixel": ("불량 화소", "Dead pixel"),
    "green_line": ("화면 줄", "Screen lines"), "burn_in": ("번인", "Burn-in"),
    "flicker": ("깜빡임", "Flicker"), "overheat": ("발열", "Overheating"),
    "drain": ("배터리 광탈", "Battery drain"), "lag": ("버벅임", "Lag"),
    "freeze": ("멈춤·먹통", "Freeze"), "crash": ("앱 튕김", "Crash"),
    "update_fail": ("업데이트 실패", "Update failure"), "disconnect": ("연결 끊김", "Disconnect"),
    "no_signal": ("신호 없음", "No signal"), "dust_ingress": ("먼지 유입", "Dust ingress"),
    "gap": ("유격", "Gap"), "scratch": ("긁힘", "Scratch"), "peeling": ("들뜸·벗겨짐", "Peeling"),
    "bubble": ("기포", "Bubble"), "crease": ("주름", "Crease"), "noise": ("잡음", "Noise"),
    "blurry": ("흐림·초점", "Blurry"), "discolor": ("변색", "Discoloration"),
}
TOPIC_LABELS: Dict[str, Tuple[str, str]] = {
    "battery": ("배터리", "Battery"), "camera": ("카메라", "Camera"),
    "display": ("디스플레이", "Display"), "performance": ("성능", "Performance"),
    "software": ("소프트웨어", "Software"), "build_quality": ("내구·마감", "Build quality"),
    "price": ("가격", "Price"), "design": ("디자인", "Design"),
    "connectivity": ("연결성", "Connectivity"), "ai_features": ("AI 기능", "AI features"),
    "accessories": ("액세서리", "Accessories"), "comparison": ("비교", "Comparison"),
    "review": ("리뷰", "Review"),
}
CATEGORY_LABELS: Dict[str, Tuple[str, str]] = {
    "phone": ("스마트폰", "Phone"), "tablet": ("태블릿", "Tablet"), "watch": ("워치", "Watch"),
    "buds": ("이어버드", "Earbuds"), "laptop": ("노트북", "Laptop"), "ring": ("링", "Ring"),
}

# ═════════ 관계 ═══════════════════════════════════════════════════════════
# 증상 → 함의 부품: 정본(_IMPLIED)을 그대로 쓴다. 증상 → 주제는 categories 체계와 맞춘다.
SYMPTOM_COMPONENT: Dict[str, str] = dict(_dx._IMPLIED)
SYMPTOM_SEVERITY: Dict[str, str] = dict(_dx._SEVERITY)
SYMPTOM_TOPIC: Dict[str, str] = {
    "overheat": "performance", "lag": "performance", "freeze": "performance",
    "crash": "software", "update_fail": "software", "boot_loop": "software",
    "drain": "battery", "swelling": "battery", "no_charge": "battery",
    "green_line": "display", "burn_in": "display", "flicker": "display",
    "dead_pixel": "display", "crease": "display", "bubble": "display",
    "crack": "build_quality", "break": "build_quality", "scratch": "build_quality",
    "peeling": "build_quality", "gap": "build_quality", "dust_ingress": "build_quality",
    "discolor": "build_quality", "water_damage": "build_quality",
    "disconnect": "connectivity", "no_signal": "connectivity", "blurry": "camera",
}
COMPONENT_TOPIC: Dict[str, str] = {
    "battery": "battery", "charging_port": "battery", "display": "display",
    "camera": "camera", "software": "software", "connectivity": "connectivity",
    "sim": "connectivity", "thermal": "performance", "hinge": "build_quality",
    "frame": "build_quality", "back_glass": "build_quality", "button": "build_quality",
    "s_pen": "accessories",
}

# ═════════ 질의 전용 짧은 표현 ═════════════════════════════════════════════
# 문서용 렉시콘(_dx._SYMPTOM_SRC)은 정밀도 때문에 문맥을 요구한다. 질의에서는 사용자가
# 뜻을 직접 말하므로 단독 표현도 받는다. 여기 'hot'·'slow' 처럼 넓은 말은 **질의 인식에만**
# 쓰고 본문 검색어(EXPANSION_EXTRA)에는 넣지 않는다.
QUERY_SYMPTOM_SRC: Dict[str, str] = {
    "overheat": r"발열|과열|열\s*나|열이\s*나|열남|뜨끈|후끈|뜨거|뜨겁|\boverheat\w*|\bheating\b|\bhot\b",
    "drain": r"광탈|배터리\s*(?:소모|빨리|금방)|빨리\s*닳|방전|\bdrain\w*",
    "lag": r"(?<![가-힣])렉|버벅|느려|느림|\blag\w*|\bslow\w*|\bstutter\w*",
    "freeze": r"멈춤|멈춰|먹통|프리징|\bfreez\w*|\bfrozen\b",
    "crash": r"튕김|튕겨|강종|강제\s*종료|\bcrash\w*",
    "update_fail": r"(?:업데이트|업뎃)\s*(?:실패|오류|안\s*됨|안\s*돼)|\bupdate\s+(?:fail\w*|error|stuck)",
    "no_charge": r"충전\s*(?:이\s*)?(?:안|불가)|\bnot\s+charging\b|\bwon'?t\s+charge\b",
    "green_line": r"(?:녹색|초록|세로|가로|핑크|분홍|보라)\s*줄|화면\s*줄|\bgreen\s+lines?\b|\blines?\s+on\s+(?:the\s+)?screen\b",
    "burn_in": r"번인|잔상|\bburn[- ]?in\b",
    "flicker": r"깜빡|깜박|점멸|\bflicker\w*",
    "dead_pixel": r"불량\s*화소|데드\s*픽셀|\bdead\s+pixels?\b",
    "crack": r"깨짐|깨졌|금\s*(?:이\s*)?감|균열|크랙|\bcrack\w*",
    "break": r"고장|파손|망가|\bbroken\b|\bbroke\b",
    "scratch": r"기스|스크래치|흠집|긁힘|\bscratch\w*",
    "peeling": r"들뜸|벗겨|\bpeel\w*",
    "crease": r"주름|\bcrease\w*",
    "bubble": r"기포|\bbubbl\w*",
    "gap": r"유격|틈새|\bgaps?\b",
    "dust_ingress": r"먼지|이물질|이물|\bdust\b|\bdebris\b|\blint\b",
    "discolor": r"변색|누렇|황변|\bdiscolou?r\w*|\byellowing\b",
    # '노이즈캔슬링'(ANC)은 기능이지 잡음 결함이 아니다 — '버즈3 노이즈캔슬링 불만' 이 잡음으로 잡혔다
    "noise": r"잡음|소음|노이즈(?!\s*캔슬)|지직|\bbuzz\w*|\bcrackl\w*|\brattl\w*",
    "blurry": r"흐릿|흐림|초점|블러|\bblurr?y\b|\bout\s+of\s+focus\b",
    "disconnect": r"끊김|끊겨|연결\s*끊|\bdisconnect\w*",
    "no_signal": r"신호\s*(?:없|안)|안\s*터짐|\bno\s+(?:signal|service)\b",
    "swelling": r"배부름|부풀|스웰링|\bswell\w*|\bswollen\b|\bbulg\w*",
    "fire": r"발화|폭발|불이\s*났|\bfire\b|\bexplod\w*",
    "water_damage": r"침수|\bwater\s+damage\b",
    "boot_loop": r"무한\s*부팅|부팅\s*반복|\bboot\s*loop\w*",
    "no_power": r"안\s*켜|전원\s*(?:이\s*)?안|\bwon'?t\s+turn\s+on\b",
    "not_working": r"작동\s*안|\bnot\s+working\b|\bstopped\s+working\b",
    "burn": r"화상",
    "shock": r"감전|\belectric\s+shock\b",
}

# LLM 개념 추론에 주는 설명 — 라벨만 주면 '작동 안 함'을 '앱 튕김'으로, '렌즈 안 먼지'를 '깨짐'으로
# 헷갈렸다(평가 26건 중 4건). 헷갈리는 쌍을 갈라 주는 설명을 붙이니 새 표현 홀드아웃 19/19.
SYMPTOM_HINTS: Dict[str, str] = {
    "not_working": "a feature, button, pen, sensor or app does not respond / is not recognized",
    "crash": "an APP closes by itself or force-closes",
    "no_power": "the whole device will not turn on",
    "dust_ingress": "dust, debris or something got INSIDE (lens, hinge, screen)",
    "crack": "glass or body is cracked",
    "peeling": "film, coating or paint peels off",
    "swelling": "battery bulges and pushes the back cover up",
    "drain": "battery runs out fast / does not last",
    "overheat": "device gets hot",
    "green_line": "a line (any colour) appears on the screen",
    "noise": "crackling, buzzing or distorted sound",
    "blurry": "photos or focus are blurry",
}

# 본문 검색용 정밀 동의어. 문서 렉시콘의 리터럴 분기(자동 추출)에 더한다.
# 넓은 말('hot', 'slow', 'dead')은 넣지 않는다 — 본문에서는 오탐이 된다.
EXPANSION_EXTRA: Dict[str, List[str]] = {
    "overheat": ["overheating", "overheats", "heats up", "heating up", "thermal throttling"],
    "drain": ["battery drain", "draining", "battery dies", "광탈", "배터리 소모", "빨리 닳"],
    "lag": ["lag", "laggy", "lagging", "stuttering", "렉 걸", "느려졌"],
    "freeze": ["freezes", "freezing", "frozen screen", "프리징"],
    "crash": ["crashes", "crashing", "force close", "튕겨"],
    "update_fail": ["update failed", "failed to update", "업데이트 실패", "업뎃 실패"],
    "no_charge": ["not charging", "won't charge", "충전 안 됨", "충전이 안"],
    "green_line": ["green line", "green lines", "pink line", "녹색 줄", "초록 줄", "화면 줄"],
    "burn_in": ["burn-in", "burn in", "번인"],
    "flicker": ["flickering", "flicker", "깜빡임", "깜빡거"],
    "dead_pixel": ["dead pixel", "dead pixels", "불량 화소"],
    "crack": ["cracked screen", "cracked", "깨짐", "금이 갔"],
    "break": ["broken", "고장", "망가졌"],
    "scratch": ["scratches", "scratched", "기스", "흠집", "스크래치"],
    "peeling": ["peeling", "들뜸"],
    "crease": ["crease", "주름"],
    "bubble": ["bubbles under", "기포"],
    "gap": ["hinge gap", "유격"],
    "dust_ingress": ["dust inside", "dust under", "먼지 유입", "먼지가 들어", "이물질"],
    "discolor": ["yellowing", "discoloration", "변색"],
    "noise": ["crackling", "buzzing", "잡음", "지직"],
    "blurry": ["blurry", "out of focus", "초점이 안", "흐릿"],
    "disconnect": ["disconnects", "keeps disconnecting", "연결 끊김", "끊김"],
    "no_signal": ["no signal", "no service", "신호 없음"],
    "swelling": ["swollen battery", "battery swelling", "배부름", "스웰링"],
    "fire": ["caught fire", "exploded", "발화", "폭발"],
    "water_damage": ["water damage", "침수"],
    "boot_loop": ["boot loop", "bootloop", "무한 부팅"],
    "no_power": ["won't turn on", "not turning on", "안 켜져", "전원이 안"],
    "not_working": ["not working", "stopped working", "작동 안"],
    "burn": ["화상"],
    "shock": ["electric shock", "감전"],
}
COMPONENT_EXPANSION_EN: Dict[str, List[str]] = {
    "hinge": ["hinge"], "display": ["display", "screen", "amoled"], "battery": ["battery"],
    "camera": ["camera", "lens"], "charging_port": ["charging port", "usb-c port"],
    "speaker": ["speaker", "earpiece"], "button": ["power button", "volume button"],
    "back_glass": ["back glass"], "frame": ["frame"], "fingerprint": ["fingerprint"],
    "software": ["software", "one ui", "firmware"], "connectivity": ["wifi", "bluetooth", "signal"],
    "sim": ["sim card", "esim"], "thermal": ["thermal", "heat"], "s_pen": ["s pen"],
}

QUERY_TOPIC_SRC: Dict[str, str] = {
    "battery": r"배터리|\bbatter(?:y|ies)\b|충전|\bcharging\b",
    "camera": r"카메라|\bcameras?\b|사진|\bphotos?\b|촬영|동영상|\bvideos?\b|\bzoom\b|(?<![가-힣])줌|야간\s*모드|\bnight\s+mode\b|셀카|\bselfies?\b",
    "display": r"화면|디스플레이|\bdisplays?\b|\bscreens?\b|밝기|\bbrightness\b|주사율|\brefresh\s+rate\b|액정",
    "performance": r"성능|\bperformance\b|속도|\bspeed\b|벤치|\bbenchmarks?\b|게임|\bgaming\b|\bfps\b",
    "software": r"소프트웨어|\bsoftware\b|업데이트|업뎃|\bupdates?\b|\bone\s?ui\b|원\s?ui|버그|\bbugs?\b|안드로이드|\bandroid\b|펌웨어|\bfirmware\b",
    "build_quality": r"내구성|\bdurab\w*|마감|\bbuild\s+quality\b|품질|방수|방진|\bip6[78]\b",
    "price": r"가격|\bprices?\b|\bpricing\b|가성비|비싸|저렴|할인|\bdiscounts?\b|보상\s*판매|\btrade-?in\b|\bexpensive\b|\bcheap\w*",
    "design": r"디자인|\bdesign\w*|색상|\bcolou?rs?\b|무게|\bweight\b|두께|그립감",
    "connectivity": r"통신|와이파이|\bwi-?fi\b|블루투스|\bbluetooth\b|\b5g\b|\blte\b|신호|\bsignal\b|통화\s*품질|\bcall\s+quality\b|\bnfc\b",
    "ai_features": r"갤럭시\s*ai|\bgalaxy\s+ai\b|\bai\s*기능|\bai\s+features?\b|서클\s*투\s*서치|\bcircle\s+to\s+search\b|실시간\s*(?:번역|통역)|\blive\s+translate\b|빅스비|\bbixby\b|생성형|인공지능|\bgemini\b|제미나이",
    "accessories": r"케이스|\bphone\s+cases?\b|액세서리|\baccessor\w*|충전기|\bchargers?\b|보호\s*필름|\bscreen\s+protectors?\b|스트랩|\bstraps?\b",
    "comparison": r"비교|\bvs\.?(?=\s|$)|\bversus\b|대비|\bcompared?\s+(?:to|with)\b|\bcomparison\b|갈아타|기변",
    "review": r"리뷰|\breviews?\b|후기|사용기|개봉기|\bunboxing\b|장단점|\bhands[- ]on\b",
}

# 질의 전용 제품 약칭 — 레거시 사전은 '갤럭시 노트9' 는 잡지만 맨 '노트9'·'워치4' 는 못 잡는다.
# 문서 쪽에 넣으면 '노트 9개'(공책 아홉 권) 같은 오탐이 생기므로 질의에서만 쓴다.
# 앞에 타사 브랜드가 붙으면('레드미 노트9') 버린다. 코드가 카탈로그에 없으면 호출부가 버린다.
QUERY_PRODUCT_ALIASES: List[Tuple[str, str]] = [
    (r"(?<![가-힣0-9a-z])노트\s?(\d{1,2})\s*(?:울트라|ultra)", "GN{}U"),
    (r"(?<![가-힣0-9a-z])노트\s?(\d{1,2})\s*(?:플러스|\+|plus)", "GN{}P"),
    (r"(?<![가-힣0-9a-z])노트\s?(\d{1,2})(?![0-9])(?!\s*(?:개|장|페이지|권|쪽|번째))", "GN{}"),
    (r"(?<![가-힣0-9a-z])워치\s?(\d)(?![0-9])\s*(?:클래식|classic)", "GW{}C"),
    (r"(?<![가-힣0-9a-z])워치\s?(\d)(?![0-9])", "GW{}"),
    (r"(?<![가-힣0-9a-z])버즈\s?(\d)(?![0-9])\s*(?:프로|pro)", "GB{}P"),
    (r"(?<![가-힣0-9a-z])버즈\s?(\d)(?![0-9])", "GB{}"),
]
_RIVAL_BEFORE = re.compile(r"(?:레드미|홍미|샤오미|redmi|xiaomi|화웨이|huawei|애플|apple|가민|garmin)\s*$")

# 세대(시리즈) 표지 — 'S25' 는 기본 모델, 'S25 시리즈' 는 S25·S25+·Ultra·FE 전부
FAMILY_MARKER = re.compile(
    r"시리즈|라인업|전\s*모델|모든\s*모델|계열|\bseries\b|\bline-?up\b|\ball\s+models\b|\bfamily\b")

# 번호 없는 시리즈 이름 — 전 세대. (code_regex 는 호출부가 카탈로그에 적용한다)
SERIES: Dict[str, Dict] = {
    "zfold": {"label": ("갤럭시 Z 폴드 전 세대", "Galaxy Z Fold (all)"),
              "src": r"(?<![0-9a-z])z\s?fold(?!\s?\d)|갤럭시\s*z?\s*폴드(?!\s*\d)|(?<![가-힣])폴드(?!\s*\d)",
              "code_regex": r"^GZF\d+$"},
    "zflip": {"label": ("갤럭시 Z 플립 전 세대", "Galaxy Z Flip (all)"),
              "src": r"(?<![0-9a-z])z\s?flip(?!\s?\d)|갤럭시\s*z?\s*플립(?!\s*\d)|(?<![가-힣])플립(?!\s*\d)",
              "code_regex": r"^GZFL\d+$"},
    "galaxy_note": {"label": ("갤럭시 노트 전 세대", "Galaxy Note (all)"),
                    "src": r"갤럭시\s*노트(?!\s*\d)|\bgalaxy\s+note(?!\s*\d)",
                    "code_regex": r"^GN\d+"},
    "galaxy_s": {"label": ("갤럭시 S 전 세대", "Galaxy S (all)"),
                 "src": r"갤럭시\s*s\s*(?:시리즈|라인)|\bgalaxy\s+s\s+(?:series|line)\b",
                 "code_regex": r"^(?:GS\d+[UPE]?|GFE\d+)$"},
}
# 폼팩터 — 호출부가 카탈로그 이름(name_en)에 적용한다
FORMS: Dict[str, Dict] = {
    "foldable": {"label": ("폴더블", "Foldable"), "src": r"폴더블|\bfoldables?\b|접는\s*폰",
                 "name_regex": r"fold|flip|razr|mate\s*x|find\s*n|magic\s*v"},
}
# 기기 종류 (브랜드 한정이 붙으면 함께) — (src, category, brand|None)
DEVICE_TERMS: List[Tuple[str, str, Optional[str]]] = [
    (r"갤럭시\s*탭|갤탭|\bgalaxy\s+tab(?!\s*s?\d)", "tablet", "samsung"),
    (r"태블릿|\btablets?\b", "tablet", None),
    (r"갤럭시\s*워치|갤워치|\bgalaxy\s+watch(?!\s*\d)", "watch", "samsung"),
    (r"애플\s*워치|\bapple\s+watch(?!\s*\d)", "watch", "apple"),
    (r"스마트\s*워치|\bsmart\s*watch(?:es)?\b|(?<![가-힣])워치(?!\s*\d)", "watch", None),
    (r"갤럭시\s*버즈|\bgalaxy\s+buds(?!\s*\d)|(?<![가-힣])버즈(?!\s*\d)", "buds", "samsung"),
    (r"에어팟|\bairpods(?!\s*\d)", "buds", "apple"),
    (r"이어폰|이어버드|\bearbuds?\b|\bearphones?\b", "buds", None),
    (r"갤럭시\s*북|\bgalaxy\s+book(?!\s*\d)", "laptop", "samsung"),
    (r"노트북|\blaptops?\b", "laptop", None),
    (r"갤럭시\s*링|\bgalaxy\s+ring\b", "ring", "samsung"),
    (r"스마트폰|휴대폰|핸드폰|\bsmartphones?\b|\bphones?\b", "phone", None),
]
# 브랜드 — (src, 본문 검색어). 제품이 잡히면 브랜드는 제품이 이미 함의한다.
BRANDS: Dict[str, Tuple[str, List[str]]] = {
    # '갤럭시 AI' 는 브랜드가 아니라 기능(주제 ai_features)이다
    "samsung": (r"삼성|\bsamsung\b|갤럭시(?!\s*ai\b)|\bgalaxy\b(?!\s*ai\b)", ["samsung", "galaxy", "삼성", "갤럭시"]),
    "apple": (r"애플|\bapple\b|아이폰|\biphones?\b|아이패드|\bipad\b", ["apple", "iphone", "아이폰", "애플"]),
    "google": (r"구글|\bgoogle\b|픽셀|\bpixel\b", ["google pixel", "pixel phone", "픽셀"]),
    "xiaomi": (r"샤오미|\bxiaomi\b|레드미|\bredmi\b|\bpoco\b", ["xiaomi", "redmi", "샤오미"]),
    "huawei": (r"화웨이|\bhuawei\b", ["huawei", "화웨이"]),
    "oneplus": (r"원플러스|\boneplus\b", ["oneplus"]),
    "oppo": (r"오포|\boppo\b", ["oppo"]),
    "vivo": (r"비보|\bvivo\b", ["vivo"]),
    "motorola": (r"모토로라|\bmotorola\b", ["motorola"]),
    "sony": (r"소니|\bsony\b|엑스페리아|\bxperia\b", ["sony xperia", "xperia"]),
    "nokia": (r"노키아|\bnokia\b", ["nokia"]),
    "garmin": (r"가민|\bgarmin\b", ["garmin"]),
}
# 기기 종류의 본문 검색어 — 제품 태그가 없는 글을 범위에 넣을 때 쓴다(FTS, 영어 번역문 기준).
# 맨 'watch' 는 동사라('watch videos') 넣지 않는다.
CATEGORY_TEXT: Dict[str, List[str]] = {
    "tablet": ["tablet", "galaxy tab", "ipad"], "watch": ["smartwatch", "galaxy watch", "apple watch", "wear os"],
    "buds": ["earbuds", "galaxy buds", "airpods"], "laptop": ["laptop", "galaxy book"],
    "ring": ["galaxy ring", "smart ring"], "phone": ["phone", "smartphone"],
}
# 시장 — (code, 단어 정규식(대소문자 무시), 약어 정규식(대소문자 구분), 언어)
# 국가 필드는 플랫폼 기본값(US 96%)이 많아, 해당 언어 글도 그 시장으로 본다.
MARKETS: List[Tuple[str, str, Optional[str], Optional[str]]] = [
    ("KR", r"한국|국내|대한민국|\bkorea(?:n)?\b", r"\bKR\b", "ko"),
    ("US", r"미국|\bamerica(?:n)?\b|\bunited\s+states\b", r"\bUSA?\b", None),
    ("IN", r"인도(?!네시아)|\bindia(?:n)?\b", None, None),
    ("JP", r"일본|\bjapan(?:ese)?\b", None, "ja"),
    ("CN", r"중국|\bchina\b|\bchinese\b", None, "zh"),
    ("DE", r"독일|\bgerman(?:y)?\b", None, "de"),
    ("FR", r"프랑스|\bfrance\b|\bfrench\b", None, "fr"),
    ("GB", r"영국|\bbritain\b|\bbritish\b|\bengland\b", r"\bUK\b", None),
    ("ES", r"스페인|\bspain\b|\bspanish\b", None, "es"),
    ("IT", r"이탈리아|\bital(?:y|ian)\b", None, "it"),
    ("BR", r"브라질|\bbrazil(?:ian)?\b", None, "pt"),
    ("RU", r"러시아|\brussia(?:n)?\b", None, "ru"),
    ("VN", r"베트남|\bvietnam(?:ese)?\b", None, "vi"),
    ("ID", r"인도네시아|\bindonesia(?:n)?\b", None, "id"),
    ("TH", r"태국|\bthailand\b|\bthai\b", None, "th"),
    ("TR", r"터키|튀르키예|\bturkey\b|\bturkish\b|türkiye", None, "tr"),
]
# 채널 한국어 별칭 → 플랫폼 코드. 영어 이름은 호출부가 platforms 표에서 만든다.
CHANNEL_ALIASES: Dict[str, List[str]] = {
    "레딧": ["reddit_rss", "reddit"], "유튜브": ["youtube"], "디시": ["dcinside"],
    "디씨": ["dcinside"], "디시인사이드": ["dcinside"], "클리앙": ["clien"], "뽐뿌": ["ppomppu"],
    "엠팍": ["mlbpark"], "개드립": ["dogdrip"], "펨코": ["fmkorea"], "에펨코리아": ["fmkorea"],
    "보배드림": ["bobaedream"], "해커뉴스": ["hackernews"], "삼성멤버스": ["samsung_community"],
    "삼성 멤버스": ["samsung_community"], "삼성 커뮤니티": ["samsung_community"],
    "앱스토어": ["appstore"], "플레이스토어": ["appstore"], "구글플레이": ["appstore"],
    "퀘이사존": ["quasarzone"], "다나와": ["danawa"], "블루스카이": ["bluesky"],
    "마스토돈": ["mastodon"], "인스티즈": ["instiz"], "아마존": ["amazon_us", "amazon_de", "amazon_jp", "amazon_kr"],
}
# 채널은 **장소로 쓸 때만** 필터다. '유튜브 앱 튕김' 의 유튜브는 앱 이름이다.
_KO_PLACE = r"(?:\s*(?:에서|서|의\s*반응|반응|여론|유저|사용자|이용자|글|댓글|커뮤니티|커뮤))"
_EN_PLACE_BEFORE = r"\b(?:on|in|from|at|across)\s+(?:the\s+)?"
_EN_PLACE_AFTER = r"\s+(?:users?|posts?|comments?|threads?|reviews?|community|subreddits?|forums?)\b"

SENTIMENT = {
    "negative": re.compile(r"불만|문제|고장|결함|이슈|짜증|최악|별로|실망|불량|하자|심해|심함|심각|"
                           r"\bcomplain\w*|\bproblems?\b|\bissues?\b|\bdefect\w*|\bbad\b|\bworst\b|\bterrible\b|\bhate\b"),
    "positive": re.compile(r"칭찬|만족|좋아|좋은|최고|장점|추천|\bprais\w*|\blove\w*|\bgreat\b|\bbest\b|\bgood\b"),
}
RECENCY: List[Tuple[re.Pattern, int]] = [
    (re.compile(r"이번\s*주|\bthis\s+week\b"), 7),
    (re.compile(r"이번\s*달|\bthis\s+month\b"), 30),
    (re.compile(r"최근|요즘|요새|근래|\blately\b|\brecent(?:ly)?\b"), 90),
]
YEAR_RE = re.compile(r"(?<![0-9a-z])(20[12]\d)\s*년?(?![0-9])")

_STOP_KO = set("관련 관해 대한 대해 대해서 어때 어때요 어떤가요 어떤지 있나요 있어요 인가요 인지 좀 정말 너무 "
               "진짜 그냥 것 거 때 후 전 중 등 및 또는 그리고 하는 하고 되는 돼요 됩니다 합니다 있는 없는 있음 "
               "없음 같은 같아요 현상 증상 사례 글 내용 정보 반응 여론 사용자 유저 사람들 많이 많은 자주 계속 "
               "심하게 생김 생겨요 발생 경우 알려줘 찾아줘 보여줘 검색 모음 정리 "
               "와요 와 가요 해요 나요 줘요 됨 함 음 임 네요 어요 아요 "
               "자꾸 자꾸만 매번 항상 가끔 갑자기 늘 또 "
               "폰 폰이 폰은 폰을 폰도 폰에 폰으로 폰에서 폰이랑 폰의 폰만".split())
_STOP_EN = set("the a an of for on in at to with about and or is are was were be been my your our any some "
               "how what why when does do did it its this that these those people users user posts post "
               "reports report getting get gets having has have show find search me".split())
# 조사 떼기 — '알림이' 를 그대로 두면 '알림' 을 못 찾는다. 받침에 따라 갈리는 조사(이/가·은/는·
# 을/를·과/와·으로/로)는 앞 음절 받침을 보고 뗀다. 안 보면 '삼성페이'·'디스플레이'·'딜레이' 의
# '이', '손난로' 의 '로' 까지 떼어 낱말이 잘렸다. 받침과 무관한 조사(도·만·에 …)는 그대로 뗀다.
_PARTICLE_ANY = re.compile(r"(?:에서|이랑|하고|까지|부터|처럼|보다|의|에|도|만|랑)$")
_PARTICLE_COND = (("으로", "batchim_not_l"), ("이", "batchim"), ("은", "batchim"), ("을", "batchim"),
                  ("과", "batchim"), ("가", "open"), ("는", "open"), ("를", "open"), ("와", "open"),
                  ("로", "open_or_l"))


def _strip_particle(t: str) -> str:
    """끝 조사 하나를 뗀다. 조사가 아니면 그대로."""
    m = _PARTICLE_ANY.search(t)
    if m:
        return t[:m.start()]
    for p, rule in _PARTICLE_COND:
        if not t.endswith(p) or len(t) == len(p):
            continue
        prev = ord(t[-len(p) - 1]) - 0xAC00
        if not 0 <= prev < 11172:
            continue
        jong = prev % 28                      # 0 = 받침 없음, 8 = ㄹ
        ok = {"batchim": jong != 0, "open": jong == 0, "open_or_l": jong in (0, 8),
              "batchim_not_l": jong not in (0, 8)}[rule]
        if ok:
            return t[:-len(p)]
    return t if not (t in dict(_PARTICLE_COND) or _PARTICLE_ANY.fullmatch(t)) else ""

# 남는 말이 '실패해요'·'충전돼요' 처럼 명사+하다/되다 활용이면 명사만 남긴다 — 활용형 그대로는
# 본문에서 거의 안 나온다('실패해요' 부분일치 대신 '실패').
_VERB_END = re.compile(r"(?:했어요|했는데|했어|했다|합니다|하네요|하는데|해서|해요|해도|하고|하면|하는|하네|"
                       r"됐어요|됐는데|됐어|됩니다|되네요|되는데|돼서|돼요|되요|돼도|되고|되면|되는|함|됨)$")
_TOKEN_RE = re.compile(r"[0-9a-z가-힣][0-9a-z가-힣+\-']*")
_WORD_CHAR = re.compile(r"[0-9a-z가-힣]")


def _c(src: str) -> re.Pattern:
    return re.compile(src, re.IGNORECASE)


_Q_SYM = {k: _c(v) for k, v in QUERY_SYMPTOM_SRC.items()}
_Q_TOPIC = {k: _c(v) for k, v in QUERY_TOPIC_SRC.items()}
_SERIES_RE = {k: _c(v["src"]) for k, v in SERIES.items()}
_FORM_RE = {k: _c(v["src"]) for k, v in FORMS.items()}
_DEVICE_RE = [(_c(s), cat, brand) for s, cat, brand in DEVICE_TERMS]
_BRAND_RE = {k: _c(v[0]) for k, v in BRANDS.items()}
_MARKET_RE = [(code, _c(w), re.compile(a) if a else None, lang) for code, w, a, lang in MARKETS]
_Q_PRODUCT_ALIAS = [(_c(s), fmt) for s, fmt in QUERY_PRODUCT_ALIASES]


# ═════════ 본문 동의어 자동 추출 ══════════════════════════════════════════
def _top_level_branches(src: str) -> List[str]:
    out, depth, cur, i = [], 0, [], 0
    while i < len(src):
        ch = src[i]
        if ch == "\\" and i + 1 < len(src):
            cur.append(src[i:i + 2]); i += 2; continue
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "|" and depth == 0:
            out.append("".join(cur)); cur = []
        else:
            cur.append(ch)
        i += 1
    out.append("".join(cur))
    return out


def literal_branches(src: str) -> List[str]:
    """정규식의 최상위 분기 중 글자 그대로인 것만 — 본문 검색어로 쓸 수 있는 표현.

    '\\b'·전방탐색은 지우고 '\\s*'·'\\s+' 는 공백으로 본다. 그룹·반복이 남는 분기는 버린다
    (전개하면 검색어가 폭발하고, 문맥 조건이 빠지면 오탐이 된다).
    """
    out = []
    for b in _top_level_branches(src):
        b = re.sub(r"\(\?[!=<][^)]*\)", "", b)
        b = b.replace(r"\b", "")
        b = re.sub(r"\\s[*+?]?", " ", b)
        b = b.replace(r"\'", "'").replace(r"\-", "-")
        if re.search(r"[\\()\[\]{}?*+^$.|]", b):
            continue
        b = " ".join(b.split())
        if len(b) >= 2:
            out.append(b)
    return out


def _dedup(xs: Iterable[str]) -> List[str]:
    seen, out = set(), []
    for x in xs:
        k = x.lower()
        if k not in seen:
            seen.add(k)
            out.append(x)
    return out


def symptom_terms(symptom: str) -> List[str]:
    """증상 하나의 본문 검색어 — 문서 렉시콘 리터럴 분기 + 정밀 동의어."""
    return _dedup(literal_branches(_dx._SYMPTOM_SRC.get(symptom, "")) + EXPANSION_EXTRA.get(symptom, []))


def component_terms(component: str) -> List[str]:
    """부품 하나의 본문 검색어."""
    return _dedup(literal_branches(_dx._COMPONENT_SRC.get(component, "")) + COMPONENT_EXPANSION_EN.get(component, []))


# ═════════ 제품 세대 ═══════════════════════════════════════════════════════
_FAMILY_RULES = [
    (re.compile(r"^GS(\d+)(?:U|P|E)?$"), "GS{}"),
    (re.compile(r"^GFE(\d+)$"), "GS{}"),             # FE 는 같은 번호의 S 세대에 속한다
    (re.compile(r"^GZF(\d+)$"), "GZF{}"),
    (re.compile(r"^GZFL(\d+)$"), "GZFL{}"),
    (re.compile(r"^GW(\d+)(?:C|P)?$"), "GW{}"),
    (re.compile(r"^GTABS(\d+)(?:U|P|F)?$"), "GTABS{}"),
    (re.compile(r"^GTS(\d+)(?:FP|P|L|E|F)?$"), "GTABS{}"),
    (re.compile(r"^GN(\d+)(?:U|P)?$"), "GN{}"),
    (re.compile(r"^GB(\d+)(?:P|FE)?$"), "GB{}"),
    (re.compile(r"^AP(\d+)(?:P|PM|PL|E|MI|S|SM|R)?$"), "AP{}"),
    (re.compile(r"^PX(\d+)(?:A|P|PF|PXL|XL)?$"), "PX{}"),
]


def family_rule(code: str) -> Optional[str]:
    """세대 규칙이 있는 코드면 세대 키, 없으면 None."""
    up = (code or "").upper()
    for rx, fmt in _FAMILY_RULES:
        m = rx.match(up)
        if m:
            return fmt.format(m.group(1))
    return None


def family_key(code: str) -> str:
    """제품 코드 → 세대 키. 'GS25U'·'GFE25' → 'GS25'. 규칙에 없으면 코드 자신."""
    return family_rule(code) or (code or "").upper()


# ═════════ 해석 결과 ═══════════════════════════════════════════════════════
@dataclass
class Concept:
    type: str           # product·series·form·category·brand·symptom·component·topic·market·channel
    key: str
    via: str            # 질의 속 근거 표현
    role: str           # scope(범위) · match(후보) · boost(가산) · derived(관계로 추가)


@dataclass
class Interpretation:
    query: str
    norm: str
    concepts: List[Concept] = field(default_factory=list)
    products: List[str] = field(default_factory=list)
    # 한 묶음으로 세는 제품 여럿 — (라벨, 코드들). 출시 전 세대 모델코드·제품 여럿에 걸린 과제. 비교 판정이 하나로 센다.
    product_sets: List[Tuple[str, Tuple[str, ...]]] = field(default_factory=list)
    family: bool = False
    compare_all: bool = False
    series: List[str] = field(default_factory=list)
    forms: List[str] = field(default_factory=list)
    categories: List[Tuple[str, Optional[str]]] = field(default_factory=list)
    brands: List[str] = field(default_factory=list)
    symptoms: List[str] = field(default_factory=list)
    components: List[str] = field(default_factory=list)
    boost_components: List[str] = field(default_factory=list)
    topics: List[str] = field(default_factory=list)
    boost_topics: List[str] = field(default_factory=list)
    markets: List[str] = field(default_factory=list)
    languages: List[str] = field(default_factory=list)
    channels: List[str] = field(default_factory=list)
    sentiment: Optional[str] = None
    days: Optional[int] = None
    year: Optional[int] = None
    residual: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    plm_tasks: List[str] = field(default_factory=list)        # 질의가 과제·개발모델로 짚은 PLM 과제 key
    plm_caution: Optional[dict] = None                        # 해석한 제품이 PLM 프로젝트 여럿에 걸리면(SG-04)

    @property
    def has_concepts(self) -> bool:
        return bool(self.products or self.product_sets or self.series or self.forms or self.categories or self.brands
                    or self.symptoms or self.components or self.topics or self.markets or self.channels)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["has_concepts"] = self.has_concepts
        return d


def normalize(text: str) -> str:
    return " ".join((text or "").lower().split())


class _Spans:
    """질의에서 개념이 차지한 구간. 남는 말(잔여어)을 구하는 데 쓴다."""

    def __init__(self, norm: str):
        self.norm, self.taken, self.glued, self.compound = norm, [], [], []

    def free(self, s: int, e: int) -> bool:
        return not any(s < te and ts < e for ts, te in self.taken)

    def take(self, s: int, e: int, *, glue: bool = False, compound: bool = False):
        """glue — 어미가 붙는 개념(증상·부품·감성). 남는 말을 구할 때 그 낱말 끝까지 지운다
        ('버벅여요' 에서 '버벅' 을 잡으면 '여요' 는 남는 말이 아니다).
        compound — 브랜드. 두 글자 넘는 말이 붙어 있으면 남는 말은 합성어 통째다 — '삼성페이' 의
        '페이' 만 남기면 네이버페이·카카오페이 글까지 가산을 받았다(2026-10-18 실측)."""
        self.taken.append((s, e))
        if glue:
            self.glued.append((s, e))
        if compound:
            self.compound.append((s, e))

    def masked(self, fill: str = " ", *, whole_words: bool = False) -> str:
        """차지된 구간을 fill 로 가린 질의. 길이는 그대로다.

        레거시 매처에 넘길 때는 공백이 아닌 문자('\\x00')로 가린다 — 레거시 정규화가
        연속 공백을 하나로 줄여 위치가 어긋나기 때문이다. whole_words 면 glue 구간을 낱말
        끝까지 넓히고, 합성어에 붙은 브랜드는 가리지 않는다(잔여어용).
        """
        chars = list(self.norm)
        spans = list(self.taken)
        if whole_words:
            for s, e in self.compound:
                j = e
                while j < len(chars) and _WORD_CHAR.match(chars[j]):
                    j += 1
                rest = self.norm[e:j]                    # '페이'·'헬스' 는 합성어, '가'·'처럼'·'폰이' 는 아니다
                stem = _strip_particle(rest)
                if len(rest) >= 2 and stem and stem not in _STOP_KO:
                    spans = [sp for sp in spans if sp != (s, e)]
            for s, e in self.glued:
                while e < len(chars) and _WORD_CHAR.match(chars[e]):
                    e += 1
                spans.append((s, e))
        for s, e in spans:
            for i in range(s, min(e, len(chars))):
                chars[i] = fill
        return "".join(chars)


def _find(rx: re.Pattern, text: str, spans: _Spans, *, require_free=True):
    for m in rx.finditer(text):
        if m.end() > m.start() and (not require_free or spans.free(m.start(), m.end())):
            return m
    return None


def _add(it: Interpretation, ctype: str, key: str, via: str, role: str):
    if not any(c.type == ctype and c.key == key for c in it.concepts):
        it.concepts.append(Concept(ctype, key, via, role))


def _add_products(it: Interpretation, codes: Iterable[str], label: str, via: str):
    """제품 하나면 products(이름으로 찾은 것과 같은 자리 — 같은 뜻 = 같은 결과), 여럿이면 한 묶음(product_sets)."""
    codes = list(dict.fromkeys(codes))
    if len(codes) == 1:
        if codes[0] not in it.products:
            it.products.append(codes[0])
            _add(it, "product", codes[0], via, "scope")
    elif codes:
        it.product_sets.append((label, tuple(codes)))
        _add(it, "product", ",".join(codes), via, "scope")


def _detect_model_codes(it: Interpretation, spans: _Spans):
    """모델코드(SM-S938B) — 이름보다 정확하다. 출시 전 세대의 코드는 그 세대 전부를 한 묶음으로."""
    for raw, base, s, e in _mc.iter_codes(spans.norm):
        if not spans.free(s, e):
            continue
        prods = _mc.family_of(base)
        if not prods:
            it.notes.append(f"모델코드 {raw.upper()} 는 사전에 없어 본문 낱말로 찾는다")
            continue
        spans.take(s, e)
        _add_products(it, prods, f"모델코드 {raw.upper()}", raw)
        if len(prods) > 1:
            it.notes.append(f"모델코드 {raw.upper()} 는 출시 전이라 모델을 못 정해 세대 전체({', '.join(prods)})로 봤다")


_PLM_TOKEN = re.compile(r"[0-9a-z가-힣][0-9a-z가-힣_\-.]*")
_HANGUL_END = re.compile(r"[가-힣]$")


def _model_base(m: dict) -> Optional[str]:
    """개발모델의 기본 모델코드 — 코어(rootdesign)는 model_code, SP 기본 정의는 base_code·mass_model_code(SG-09)."""
    return next((b for b in (_mc.base_of(str(x)) for x in (m.get("model_code"), m.get("base_code"),
                                                          m.get("mass_model_code"), m.get("key")) if x) if b), None)


def _plm_product_projects(plm) -> Tuple[Dict[str, Dict[str, str]], Dict[str, Dict[str, str]]]:
    """제품 코드 → {PLM 프로젝트 key: 이름} 두 벌 — (EXACT 모델코드로만, EXACT + 출시 전 세대 묶음). 거울 객체에 붙여
    한 번만 짓는다(거울이 바뀌면 mirror() 가 새 객체를 준다).

    '여러 프로젝트에 걸린다' 는 EXACT 로만 센다 — 세대 묶음은 세대 전 모델에 걸려 프로젝트가 늘 여럿으로 보인다.
    '질의가 다른 프로젝트를 짚었다' 는 묶음까지 본다 — 출시 전 프로젝트가 자기 세대 제품을 짚은 것은 남이 아니다."""
    got = getattr(plm, "_sf_product_projects", None)
    if got is not None:
        return got
    exact: Dict[str, Dict[str, str]] = {}
    broad: Dict[str, Dict[str, str]] = {}
    for m in plm.models.values():
        base = _model_base(m)
        pk = plm.project_of(m.get("task")) if base else None
        if not pk:
            continue
        label = plm.projects.get(pk, pk)
        prod = _mc.product_of(base)
        if prod:
            exact.setdefault(prod, {})[pk] = label
        for c in _mc.family_of(base):
            broad.setdefault(c, {})[pk] = label
    plm._sf_product_projects = (exact, broad)
    return exact, broad


def _plm_named_projects(plm, norm: str, task_keys) -> Dict[str, str]:
    """질의가 짚은 PLM 프로젝트 — 프로젝트 이름·마케팅명 별칭이 질의에 있거나, 질의의 과제·개발모델이 그 프로젝트 것."""
    rx = getattr(plm, "_sf_project_rx", False)
    if rx is False:
        names = sorted(getattr(plm, "project_names", {}), key=len, reverse=True)
        rx = (re.compile(r"(?<![0-9a-z가-힣])(" + "|".join(map(re.escape, names)) + r")(?![0-9a-z가-힣])")
              if names else None)
        plm._sf_project_rx = rx
    found: Dict[str, str] = {}
    for m in (rx.finditer(norm) if rx else ()):
        for pk in plm.project_names.get(m.group(1), []):
            found.setdefault(pk, plm.projects.get(pk, pk))
    for t in task_keys or ():
        pk = plm.project_of(t)
        if pk:
            found.setdefault(pk, plm.projects.get(pk, pk))
    return found


def plm_caution(plm, codes, norm: str, task_keys=()) -> Optional[dict]:
    """해석한 제품 코드가 PLM 프로젝트 여럿에 걸리거나, 질의가 짚은 프로젝트가 그 제품의 프로젝트가 아니면 주의.

    SignalForge 제품은 프로젝트를 모른다 — 프로젝트 A 의 마케팅명·모델이 프로젝트 B 의 제품 코드로 풀리면 B 의 VOC 가
    주의 없이 실린다(포털 10-29 EX-03 · 10-30 DW-21 · OT-15). 제품 행을 프로젝트별로 나눌지는 데이터 결정(SG-04)이라
    그 전까지는 풀이를 바꾸지 않고 이 주의를 싣는다. 거울이 없으면 None."""
    codes = [c for c in dict.fromkeys(codes or ()) if c]
    if not plm or not codes:
        return None
    exact, broad = _plm_product_projects(plm)
    linked = {c: dict(exact.get(c, {})) for c in codes}
    multi = {c: ps for c, ps in linked.items() if len(ps) >= 2}
    named = _plm_named_projects(plm, norm, task_keys)
    reach = {pk for c in codes for pk in broad.get(c, {})}
    foreign = {pk: lab for pk, lab in named.items() if pk not in reach}
    if not multi and not foreign:
        return None
    parts = [f"{c} 는 PLM 프로젝트 {len(ps)}개({', '.join(sorted(ps.values()))})의 개발모델에 걸린다 — 이 제품 코드의 VOC 는 "
             f"그 프로젝트들을 가르지 않고 함께 센다" for c, ps in multi.items()]
    if foreign:
        own = sorted({lab for c in codes for lab in broad.get(c, {}).values()})
        parts.append(f"질의의 이름·모델은 PLM 프로젝트 {', '.join(sorted(foreign.values()))} 인데 해석한 제품"
                     f"({', '.join(codes)})의 개발모델은 " + (f"프로젝트 {', '.join(own)} 것이다" if own else "PLM 에 없다")
                     + " — 다른 제품의 VOC 일 수 있다")
    return {"products": {c: sorted(ps.values()) for c, ps in linked.items() if ps},
            "named_projects": sorted(named.values()), "foreign_projects": sorted(foreign.values()),
            "note": " · ".join(parts) + " (제품 행을 프로젝트별로 나눌지는 데이터 결정 대기 — SG-04)"}


def _plm_products(models: List[dict]) -> Tuple[List[str], List[str]]:
    """개발모델들 → (제품 코드, 사전에 없는 기본 코드). 모델코드 사전이 다리다(개발모델은 SignalForge 제품을 모른다)."""
    prods, missing = [], []
    for m in models:
        base = _model_base(m)
        got = _mc.family_of(base) if base else ()
        if got:
            prods.extend(got)
        elif base:
            missing.append(base)
    return list(dict.fromkeys(prods)), sorted(set(missing))


def _detect_plm(it: Interpretation, spans: _Spans, plm):
    """PLM 거울(SP 허브) — 개발모델명·양산모델코드·과제코드·과제명(차수 접미어를 뗀 개발명도) → 그 과제의 제품.
    제품에 이을 수 없으면 그 말은 남는 말로 둔다(본문에 개발명이 나오는 유출 글을 낱말로 찾게)."""
    if not plm:
        return
    norm = spans.norm
    hits = []                                             # (s, e, kind, models, tasks, raw)
    for name in plm.multi_names:                          # 띄어 쓴 과제명('감마 프로')은 낱말 하나로 안 잡힌다
        for m in re.finditer(rf"(?<![0-9a-z가-힣]){re.escape(name)}(?![0-9a-z가-힣])", norm):
            tks = plm.by_name[name]
            hits.append((m.start(), m.end(), "name", [x for t in tks for x in plm.task_models(t)], tks, m.group(0)))
    for m in _PLM_TOKEN.finditer(norm):
        tok = m.group(0)
        cands = [tok] + ([_strip_particle(tok)] if _HANGUL_END.search(tok) else [])
        for c in cands:
            if c in plm.by_model:
                model = plm.models[plm.by_model[c]]
                tasks = [model["task"]] if model.get("task") in plm.tasks else []
                hits.append((m.start(), m.end(), "model", [model], tasks, tok))
                break
            if c in plm.by_code:
                tk = plm.by_code[c]
                hits.append((m.start(), m.end(), "task", plm.task_models(tk), [tk], tok))
                break
            if c in plm.by_name:
                tks = plm.by_name[c]
                hits.append((m.start(), m.end(), "name", [x for t in tks for x in plm.task_models(t)], tks, tok))
                break
    for s, e, kind, models, tasks, raw in sorted(hits, key=lambda h: (h[0], -(h[1] - h[0]))):
        if not spans.free(s, e):
            continue
        it.plm_tasks.extend(t for t in tasks if t not in it.plm_tasks)
        tlabel = ", ".join(plm.tasks[t].get("display") or t for t in dict.fromkeys(tasks) if t in plm.tasks) or "-"
        prods, missing = _plm_products(models)
        if not prods:
            it.notes.append(f"과제 {tlabel} 는 제품에 이어지지 않는다 — 개발모델의 모델코드가 사전에 없다"
                            f"({', '.join(missing) or '개발모델 없음'}). 본문 낱말로 찾는다")
            continue
        spans.take(s, e)
        what = f"개발모델 {models[0]['key']} · 과제 {tlabel}" if kind == "model" else f"과제 {tlabel}"
        _add_products(it, prods, what, raw)
        it.notes.append(f"{what} → {', '.join(prods)}"
                        + (f" (사전에 없는 모델코드 {', '.join(missing)} 은 뺐다)" if missing else ""))


def _detect_products(it: Interpretation, spans: _Spans, plm=None):
    norm = spans.norm
    _detect_plm(it, spans, plm)
    _detect_model_codes(it, spans)
    live = _pm.infer_all_product_codes(norm, legacy=False)
    if live:
        codes = [c for c, _ in live]
        by = _pm._spans_by_code(norm, codes)
        for code in codes:
            sp = by.get(code) or []
            if sp:
                spans.take(*sp[0])
            if code in it.products:                   # 모델코드로 이미 잡은 제품(길목이 모델코드도 본다)
                continue
            it.products.append(code)
            _add(it, "product", code, norm[sp[0][0]:sp[0][1]] if sp else code, "scope")
    # 질의 전용 약칭('노트9', '워치4 클래식') — 레거시보다 먼저 봐야 '워치4 클래식' 이
    # 레거시의 '워치4'(GW4) 에 자리를 뺏기지 않는다
    for src, fmt in _Q_PRODUCT_ALIAS:
        for m in src.finditer(norm):
            if not spans.free(m.start(), m.end()) or _RIVAL_BEFORE.search(norm[max(0, m.start() - 12):m.start()]):
                continue
            code = fmt.format(m.group(1))
            spans.take(m.start(), m.end())
            if code not in it.products:
                it.products.append(code)
                _add(it, "product", code, m.group(0), "scope")
    # 구형 모델 — 이미 잡은 자리를 가린 뒤 레거시를 본다('S25 vs 노트 9 엣지')
    for _ in range(3):
        m = _pm.legacy_fallback_match(spans.masked("\x00"))
        if not m:
            break
        code, _norm, s, e = m
        if code in it.products:
            break
        spans.take(s, e)
        it.products.append(code)
        _add(it, "product", code, norm[s:e], "scope")


def _take_all(rx: re.Pattern, spans: "_Spans"):
    """감지된 개념의 다른 출현도 잔여어에서 지운다('vs … 비교' 의 '비교')."""
    for m in rx.finditer(spans.norm):
        if m.end() > m.start():
            spans.take(m.start(), m.end())


def _channel_patterns(lexicon: Dict[str, List[str]]):
    out = []
    for surface, codes in lexicon.items():
        s = re.escape(surface.lower())
        if re.search(r"[가-힣]", surface):
            src = s + _KO_PLACE
        else:
            src = rf"{_EN_PLACE_BEFORE}{s}\b|\b{s}{_EN_PLACE_AFTER}|\b{s}{_KO_PLACE}"
        out.append((surface, codes, re.compile(src, re.IGNORECASE)))
    # 긴 이름 먼저 — '삼성 멤버스' 가 '삼성' 보다 먼저 자리를 잡는다
    out.sort(key=lambda t: -len(t[0]))
    return out


def interpret(query: str, *, channel_lexicon: Optional[Dict[str, List[str]]] = None, plm=None) -> Interpretation:
    """질의 → 개념 해석. channel_lexicon 은 {표면형: [플랫폼 코드]} (호출부가 DB 에서 만든다).
    plm 은 PLM 거울 색인(base.plm_mirror.PlmIndex) — 호출부가 넘긴다(이 함수는 파일을 읽지 않는다)."""
    norm = normalize(query)
    it = Interpretation(query=query, norm=norm)
    if not norm:
        return it
    spans = _Spans(norm)

    # 1) 채널 — 장소 표지가 있을 때만. 제품명보다 먼저 자리를 잡아야 '레딧' 등이 안 섞인다.
    lex = dict(CHANNEL_ALIASES)
    for k, v in (channel_lexicon or {}).items():
        lex.setdefault(k, v)
    for surface, codes, rx in _channel_patterns(lex):
        m = _find(rx, norm, spans)
        if m:
            spans.take(m.start(), m.end())
            for c in codes:
                if c not in it.channels:
                    it.channels.append(c)
            _add(it, "channel", ",".join(codes), m.group(0).strip(), "scope")

    # 2) 제품 (라이브 + 레거시)
    _detect_products(it, spans, plm)
    if it.products and FAMILY_MARKER.search(norm):
        it.family = True
        m = FAMILY_MARKER.search(norm)
        spans.take(m.start(), m.end())
        it.notes.append("세대 표지가 있어 제품을 같은 세대 전 모델로 넓혔다")
    elif any(family_rule(c) for c in it.products):
        it.notes.append("모델 하나로 봤다 — 같은 세대 전부는 '<모델> 시리즈' 로 검색")

    # 3) 시리즈·폼팩터·기기 종류·브랜드 (제품 자리를 피한다)
    for key, rx in _SERIES_RE.items():
        m = _find(rx, norm, spans)
        if m:
            spans.take(m.start(), m.end())
            it.series.append(key)
            _add(it, "series", key, m.group(0), "scope")
    for key, rx in _FORM_RE.items():
        m = _find(rx, norm, spans)
        if m:
            spans.take(m.start(), m.end())
            it.forms.append(key)
            _add(it, "form", key, m.group(0), "scope")
    for rx, cat, brand in _DEVICE_RE:
        m = _find(rx, norm, spans)
        if m:
            spans.take(m.start(), m.end())
            if (cat, brand) not in it.categories:
                it.categories.append((cat, brand))
            _add(it, "category", f"{brand or '*'}:{cat}", m.group(0), "scope")
    if not (it.products or it.product_sets or it.series):
        for key, rx in _BRAND_RE.items():
            m = _find(rx, norm, spans)
            if m:
                spans.take(m.start(), m.end(), compound=True)
                if not any(b == key for _, b in it.categories):
                    it.brands.append(key)
                _add(it, "brand", key, m.group(0), "scope")
    else:
        # 제품이 이미 함의하는 브랜드 말('삼성 S25 울트라' · 'Samsung Galaxy S25')은 남는 말이 아니다 — 남기면 벡터가
        # 섞이고 가산이 붙어 'S25 울트라 발열' 과 결과가 갈렸다(1단계부터). 다른 브랜드('삼성 vs 아이폰 16')는 그대로 둔다.
        implied = {_pm._brand_of(c) for c in it.products + [c for _, cs in it.product_sets for c in cs]}
        if it.series:
            implied.add("samsung")                    # SERIES 는 모두 삼성 라인업이다
        for key, rx in _BRAND_RE.items():
            if key in implied:
                for m in rx.finditer(norm):
                    if m.end() > m.start() and spans.free(m.start(), m.end()):
                        spans.take(m.start(), m.end(), compound=True)

    # 4) 증상 — 질의 표현 + 문서 렉시콘. 부품 — 문서 렉시콘.
    def _all_free(rx):
        return [(m.start(), m.end()) for m in rx.finditer(norm)
                if m.end() > m.start() and spans.free(m.start(), m.end())]

    # 같은 낱말이 증상이자 부품일 수 있다('발열' = overheat·thermal). 그래서 증상 구간은
    # 부품까지 다 찾은 뒤에 한꺼번에 차지한다.
    sym_spans = []
    for key in SYMPTOM_LABELS:
        for rx in (_Q_SYM.get(key), _dx._SYMPTOM_RE.get(key)):
            hits = _all_free(rx) if rx is not None else []
            if hits:
                sym_spans.extend(hits)
                if key not in it.symptoms:
                    it.symptoms.append(key)
                _add(it, "symptom", key, norm[hits[0][0]:hits[0][1]], "match")
                break
    comp_spans = []
    for key, rx in _dx._COMPONENT_RE.items():
        hits = _all_free(rx)
        if hits:
            comp_spans.extend(hits)
            role = "boost" if it.symptoms else "match"
            (it.boost_components if it.symptoms else it.components).append(key)
            _add(it, "component", key, norm[hits[0][0]:hits[0][1]], role)
    for s, e in sym_spans + comp_spans:
        spans.take(s, e, glue=True)

    # 5) 주제 — 증상·부품과 같은 말에서 나온 주제는 관계로 붙은 것(가산만)
    for key, rx in _Q_TOPIC.items():
        m = _find(rx, norm, spans, require_free=False)
        if not m:
            continue
        overlaps = any(m.start() < e and s < m.end() for s, e in sym_spans + comp_spans)
        if overlaps or it.symptoms or it.components:
            if key not in it.boost_topics:
                it.boost_topics.append(key)
            _add(it, "topic", key, m.group(0), "boost")
        else:
            it.topics.append(key)
            _add(it, "topic", key, m.group(0), "match")
        _take_all(rx, spans)

    # 6) 관계 — 증상이 함의하는 부품·주제는 가산점으로
    for s in it.symptoms:
        comp = SYMPTOM_COMPONENT.get(s)
        if comp and comp != "device" and comp not in it.components + it.boost_components:
            it.boost_components.append(comp)
            _add(it, "component", comp, f"<{s}>", "derived")
        top = SYMPTOM_TOPIC.get(s)
        if top and top not in it.topics + it.boost_topics:
            it.boost_topics.append(top)
            _add(it, "topic", top, f"<{s}>", "derived")
    for comp in it.components:
        top = COMPONENT_TOPIC.get(comp)
        if top and top not in it.topics + it.boost_topics:
            it.boost_topics.append(top)
            _add(it, "topic", top, f"<{comp}>", "derived")

    # 7) 비교 의도 + 제품 둘 이상 → 모든 제품을 함께 언급한 글
    if ("comparison" in it.topics + it.boost_topics) and len(it.products) + len(it.product_sets) >= 2:
        it.compare_all = True
        it.notes.append("비교 표현이 있어 모든 제품을 함께 언급한 글로 좁혔다")

    # 8) 시장 — 약어(US·UK·KR)는 원문 대소문자로만
    for code, word_rx, abbr_rx, lang in _MARKET_RE:
        m = _find(word_rx, norm, spans)
        hit = m.group(0) if m else None
        if m:
            spans.take(m.start(), m.end())
        elif abbr_rx is not None and abbr_rx.search(query):
            hit = abbr_rx.search(query).group(0)
            for t in _TOKEN_RE.finditer(norm):
                if t.group(0) == hit.lower():
                    spans.take(t.start(), t.end())
        if hit and code not in it.markets:
            it.markets.append(code)
            if lang and lang not in it.languages:
                it.languages.append(lang)
            _add(it, "market", code, hit, "scope")
    if it.markets:
        it.notes.append("시장은 국가 필드 또는 그 언어로 쓴 글로 본다(국가 필드는 US 기본값이 많다)")

    # 9) 감성·기간·연도
    for label, rx in SENTIMENT.items():
        m = _find(rx, norm, spans, require_free=False)
        if m:
            it.sentiment = it.sentiment or label
            spans.take(m.start(), m.end(), glue=True)
    for rx, days in RECENCY:
        m = rx.search(norm)
        if m:
            it.days = days
            spans.take(m.start(), m.end())
            it.notes.append(f"'{m.group(0)}' → 최근 {days}일(발행일 기준)")
            break
    m = _find(YEAR_RE, norm, spans)
    if m:
        it.year = int(m.group(1))
        spans.take(m.start(), m.end())

    # 10) 남는 말 — 순위 가산용
    rest = []
    for t in _TOKEN_RE.findall(spans.masked(whole_words=True)):
        if re.search(r"[가-힣]", t) and not _strip_particle(t):
            continue                          # 조사만 남은 토막('갤럭시처럼' 의 '처럼')
        if re.search(r"[가-힣]", t) and len(t) >= 3:
            stem = _VERB_END.sub("", t)
            t = stem if len(stem) >= 2 else t
            if len(t) >= 3:
                t = _strip_particle(t)
        if t in _STOP_KO or t in _STOP_EN:
            continue
        if (re.search(r"[가-힣]", t) and len(t) < 2) or (not re.search(r"[가-힣]", t) and len(t) < 3):
            continue
        if t not in rest:
            rest.append(t)
    it.residual = rest[:3]
    it.plm_caution = plm_caution(plm, it.products + [c for _, cs in it.product_sets for c in cs], norm, it.plm_tasks)
    if it.plm_caution:
        it.notes.append("주의 — " + it.plm_caution["note"])
    return it


def add_symptoms(it: Interpretation, keys: Iterable[str], *, via: str, role: str = "inferred"):
    """해석 밖(LLM 추론 등)에서 온 증상을 붙이고, 사전 증상과 같은 관계(함의 부품·주제)를 단다.

    알 수 없는 키는 버린다 — 바깥 추론이 온톨로지에 없는 개념을 만들어 내지 못하게.
    """
    for key in keys:
        if key not in SYMPTOM_LABELS or key in it.symptoms:
            continue
        it.symptoms.append(key)
        _add(it, "symptom", key, via, role)
        comp = SYMPTOM_COMPONENT.get(key)
        if comp and comp != "device" and comp not in it.components + it.boost_components:
            it.boost_components.append(comp)
            _add(it, "component", comp, f"<{key}>", "derived")
        top = SYMPTOM_TOPIC.get(key)
        if top and top not in it.topics + it.boost_topics:
            it.boost_topics.append(top)
            _add(it, "topic", top, f"<{key}>", "derived")
    # 부품이 후보 경로였다면 증상이 생긴 이상 가산으로 내린다(사전 해석과 같은 규칙)
    if it.symptoms and it.components:
        it.boost_components.extend(c for c in it.components if c not in it.boost_components)
        it.components = []
        for c in it.concepts:
            if c.type == "component" and c.role == "match":
                c.role = "boost"


# ═════════ 둘러보기 ═══════════════════════════════════════════════════════
def label(ctype: str, key: str, lang: str = "ko") -> str:
    i = 0 if lang == "ko" else 1
    table = {"symptom": SYMPTOM_LABELS, "component": COMPONENT_LABELS, "topic": TOPIC_LABELS,
             "category": CATEGORY_LABELS}.get(ctype, {})
    if ctype == "series" and key in SERIES:
        return SERIES[key]["label"][i]
    if ctype == "form" and key in FORMS:
        return FORMS[key]["label"][i]
    return table.get(key, (key, key))[i]


def describe() -> dict:
    """온톨로지 전체 — 개념 종류별 목록과 관계. MCP get_ontology 가 그대로 돌려준다."""
    return {
        "types": {
            "product": f"제품 카탈로그(라이브 패턴 {len(_pm.PRODUCT_PATTERNS)}종 + 레거시 폴백)",
            "series": list(SERIES), "form": list(FORMS),
            "category": list(CATEGORY_LABELS), "brand": list(BRANDS),
            "component": list(COMPONENT_LABELS), "symptom": list(SYMPTOM_LABELS),
            "topic": list(TOPIC_LABELS), "market": [m[0] for m in MARKETS],
        },
        "symptoms": {k: {"label": SYMPTOM_LABELS[k][0], "label_en": SYMPTOM_LABELS[k][1],
                         "implies_component": SYMPTOM_COMPONENT.get(k),
                         "severity": SYMPTOM_SEVERITY.get(k), "topic": SYMPTOM_TOPIC.get(k),
                         "search_terms": symptom_terms(k)}
                     for k in SYMPTOM_LABELS},
        "components": {k: {"label": COMPONENT_LABELS[k][0], "label_en": COMPONENT_LABELS[k][1],
                           "topic": COMPONENT_TOPIC.get(k), "search_terms": component_terms(k)}
                       for k in COMPONENT_LABELS if k != "device"},
        "topics": {k: {"label": v[0], "label_en": v[1]} for k, v in TOPIC_LABELS.items()},
        "relations": {
            "product→family": "같은 번호 세대로 묶는다 (GS25U·GFE25 → GS25)",
            "symptom→component": "증상이 함의하는 부품 (defect_extract._IMPLIED)",
            "symptom→topic": "증상의 주제 (categories 체계)",
            "component→topic": "부품의 주제",
        },
    }
