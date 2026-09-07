# VOC 본문에서 (부품·증상·심각도) 결함 레코드를 추출하는 렉시콘 기반 추출기
"""
결함 구조화 추출.

기존 분석의 한계는 "hinge 248 / dust 100" 같은 **단어 카운트**였다. 어떤 부품이
어떤 증상을 내는지, 얼마나 심각한지가 없어 결함 리포트로 쓸 수 없었다.
이 모듈은 본문에서 (component, symptom, severity) 삼중항을 뽑는다.

설계 원칙
- 결정적(렉시콘) 방식. 전 코퍼스(40만행)를 LLM 없이 일관되게 처리할 수 있어야 한다.
- **근접도 페어링**. 부품과 증상을 단순 교차곱하면 노이즈가 폭발한다. 증상마다
  _WINDOW 안의 가장 가까운 부품과 짝지어야 "힌지에 먼지"와 "카메라가 흐림"이 섞이지 않는다.
- 부품 언급이 없는 증상도 버리지 않는다. 증상이 함의하는 기본 부품(_IMPLIED)으로 귀속하고,
  그것도 없으면 'device' 로 둔다("안 켜진다" 같은 전체 고장).
- 한국어/영어 패턴을 함께 둔다. 번역 실패로 원문만 남은 행도 잡아야 하기 때문.
"""
import re
from typing import List, Tuple

# 증상이 부품 언급 없이 나올 때 귀속할 기본 부품
_IMPLIED = {
    "overheat": "thermal",
    "drain": "battery",
    "swelling": "battery",
    "no_charge": "charging_port",
    "boot_loop": "software",
    "freeze": "software",
    "lag": "software",
    "crash": "software",
    "update_fail": "software",
    "disconnect": "connectivity",
    "no_signal": "connectivity",
    "water_damage": "device",
    "not_working": "device",
    "crease": "display",
    "bubble": "display",
}

# 심각도 — safety(위해) > non_functional(사용불가) > degraded(성능저하) > cosmetic(외관)
_SEVERITY = {
    "fire": "safety", "swelling": "safety", "burn": "safety", "shock": "safety",
    "no_power": "non_functional", "boot_loop": "non_functional",
    "no_charge": "non_functional", "dead_pixel": "non_functional",
    "crack": "non_functional", "break": "non_functional", "water_damage": "non_functional",
    "not_working": "non_functional",
    "overheat": "degraded", "drain": "degraded", "flicker": "degraded",
    "green_line": "degraded", "burn_in": "degraded", "lag": "degraded",
    "freeze": "degraded", "crash": "degraded", "disconnect": "degraded",
    "no_signal": "degraded", "noise": "degraded", "blurry": "degraded",
    "update_fail": "degraded", "dust_ingress": "degraded", "gap": "degraded",
    "bubble": "degraded",
    "scratch": "cosmetic", "peeling": "cosmetic", "crease": "cosmetic",
    "discolor": "cosmetic",
}

# 영문 토큰은 반드시 단어경계를 건다. 경계 없이 두면 'os' 가 cost/most/position/closed
# 안에서, 'heat' 가 wheat 에서, 'frame' 이 timeframe 에서 매칭돼 부품 귀속이 오염된다
# (실측: software 가 crease/dust 를 3,400여건씩 잘못 흡수).
_COMPONENT_SRC = {
    "hinge":        r"\bhinge\b|경첩|힌지",
    "display":      r"\b(?:display|screen|panel|amoled|lcd)\b|화면|액정|디스플레이|스크린",
    "battery":      r"\bbatter(?:y|ies)\b|배터리|밧데리",
    "camera":       r"\b(?:camera|lens|lenses)\b|카메라|렌즈",
    "charging_port": r"charging port|charge port|\busb-?c\b|충전\s*포트|c\s*타입",
    "speaker":      r"\b(?:speaker|earpiece)\b|스피커|이어피스|수화부",
    "button":       r"power button|volume button|버튼|전원키|볼륨키",
    "back_glass":   r"back glass|rear glass|후면\s*유리|뒷유리",
    "frame":        r"\b(?:frame|chassis)\b|프레임|본체|외관",
    "fingerprint":  r"\bfingerprint\b|ultrasonic sensor|지문|지문인식",
    "software":     r"\b(?:software|one ?ui|firmware|update[sd]?|android|os)\b"
                    r"|소프트웨어|펌웨어|업데이트",
    "connectivity": r"\b(?:wi-?fi|bluetooth|network|signal|lte|5g)\b"
                    r"|와이파이|블루투스|네트워크|신호",
    "sim":          r"\bsim\b|\besim\b|유심|심카드",
    "thermal":      r"\bthermal\b|\bheat(?:ing)?\b|발열|온도",
    "s_pen":        r"\bs ?pen\b|에스펜|s펜",
}

_SYMPTOM_SRC = {
    "fire":        r"caught fire|exploded|발화|폭발|불이\s*났",
    # 어간만 두면 'swollen'(가장 흔한 형태)을 놓친다 — 실측 FN(배터리 스웰링, safety 급).
    # 반대로 맨 'swell' 은 칭찬 속어("that's swell")라 굴절형만 받는다.
    "swelling":    r"\bswell(?:s|ed|ing)\b|\bswollen\b|\bbulg\w*|배부름|부풀|부어\s*올",
    "burn":        r"burn(?:ed|t)?\s+(?:my|the|his|her)?\s*(?:hand|finger|skin)|화상",
    "shock":       r"electric shock|감전",
    # "didn't turn back on"·"failed to power on" 계열이 통째로 빠져 있었다(실측 FN).
    "no_power":    r"won'?t turn on|not turning on|"
                   r"\b(?:won'?t|wont|will not|does\s?n'?t|does not|did\s?n'?t|did not|"
                   r"would\s?n'?t|would not|could\s?n'?t|may not|might not|cannot|can'?t)\s+"
                   # 'turn up'(나타나다)은 빼고 'power/boot up' 만 받는다 —
                   # "they didn't turn up"(투표 얘기)이 전원사망으로 잡혔다.
                   r"(?:(?:turn|switch)\s+(?:back\s+)?on|(?:power|boot)\s+(?:back\s+)?(?:on|up))\b|"
                   r"not\s+(?:(?:turning|switching)\s+(?:back\s+)?on|"
                   r"(?:powering|booting)\s+(?:back\s+)?(?:on|up))\b|"
                   r"fail(?:s|ed|ing)?\s+to\s+(?:turn|power|boot)\s+(?:back\s+)?on\b|"
                   r"\bdead\b|안\s*켜지|켜지지\s*않|전원이\s*안|전원이\s*들어오지\s*않|"
                   r"부팅(?:이)?\s*안\s*(?:되|됨|돼)",
    "boot_loop":   r"boot ?loop|bootloop|무한\s*부팅|부팅\s*반복|"
                   r"random(?:ly)? reboot|reboot(?:s|ing)\s+(?:every|randomly|constantly)|"
                   r"\bkeeps?\s+(?:restarting|rebooting)\b|재부팅",
    # "안 열려요"·"won't open"·"unresponsive" 처럼 '동작 안 함'의 흔한 변형이 빠져 있었다.
    "not_working": r"not working|does\s?n'?t work|does not work|stopped working|"
                   r"(?:won'?t|wont|does\s?n'?t|does not|is\s?n'?t|will not)\s+"
                   r"(?:open|launch|start|load|respond|register)\b|"
                   r"not\s+(?:opening|launching|loading|responding|registering)\b|"
                   r"\bunresponsive\b|"
                   r"작동\s*(?:하지\s*)?않|안\s*됨|안\s*돼|안\s*됩니다|"
                   r"열리지\s*않|안\s*열(?:리|려)|실행(?:이)?\s*안\s*(?:되|됨|돼)|반응(?:이)?\s*없",
    "no_charge":   r"won'?t charge|not charging|충전\s*(?:이)?\s*안",
    "crack":       r"\bcrack(?:ed|s|ing)?\b|깨졌|금이\s*갔|파손",
    "break":       r"broke|broken|고장|망가",
    "water_damage": r"water damage|liquid damage|침수",
    "dead_pixel":  r"dead pixel|stuck pixel|불량\s*화소|데드\s*픽셀",
    # 색만 다른 같은 결함(핑크/마젠타/보라 줄)을 못 잡았다. red/black/blue 는 관용구
    # ("bottom line", "red line")가 많아 **화면 문맥이 붙을 때만** 받는다.
    "green_line":  r"green line|vertical line|"
                   r"\b(?:pink|magenta|purple|greenish)\s+lines?\b|"
                   r"\blines?\s+(?:on|across|down)\s+(?:the\s+|my\s+)?"
                   r"(?:screen|display|panel)\b|"
                   r"초록\s*줄|녹색\s*줄|세로\s*줄|가로\s*줄|분홍\s*줄|핑크\s*줄|보라\s*줄|"
                   r"화면(?:에)?\s*줄(?:이|을)?\s*(?:생|가)",
    "burn_in":     r"burn-?in|번인",
    "flicker":     r"flicker|깜빡|점멸",
    # "noticeably hot"·"heats up" 같은 완곡 표현이 빠져 있었다. 맨 'hot' 은 받지 않고
    # 정도부사/동사가 붙은 형태만 받는다("hot deal" 오탐 방지).
    # (?!-) 는 'hot-swappable' 이 발열로 잡히던 것 차단.
    "overheat":    r"overheat|too hot\b(?!-)|"
                   r"\b(?:really|very|super|extremely|noticeably|insanely|burning|so)\s+hot\b(?!-)|"
                   r"\bget(?:s|ting)?\s+(?:really\s+|very\s+|super\s+|so\s+)?hot\b(?!-)|"
                   r"\bhot\s+to\s+the\s+touch\b|\bheats?\s+up\b|"
                   r"발열|뜨거워|뜨겁|열이\s*많이|과열",
    # 'battery life' 리터럴은 뺐다. 리뷰 기사 대부분에 등장하는 중립 표현이라
    #  ("tuned for all-day battery life") 커버리지가 늘면 자동으로 '방전 급등'이 났다.
    # "runs out fast"·"doesn't last"·"not holding a charge" 가 없어 유튜브/레딧의
    # 가장 흔한 방전 표현을 통째로 놓쳤다(실측 FN). 중립 문구를 피하려고 어휘를
    # 'battery' 근접 25자 안으로 묶는다.
    "drain":       r"\bdrain(?:s|ed|ing)?\b|batter(?:y|ies)\s+(?:drain|dies|dying)|"
                   r"batter(?:y|ies)[^.!?]{0,25}\b(?:runs?\s+out|running\s+out|ran\s+out|"
                   r"run(?:s|ning)?\s+down|does\s?n'?t\s+last|does not last|won'?t\s+last|"
                   r"drops?\s+(?:fast|quickly|rapidly)|draining)\b|"
                   r"\b(?:not|won'?t|does\s?n'?t|was\s?n'?t|is\s?n'?t)\s+hold(?:ing)?\s+"
                   r"(?:a\s+)?charge\b|"
                   r"배터리\s*(?:가|는)?\s*(?:빨리|금방|훅|순식간)|배터리\s*광탈|방전",
    "lag":         r"\blag(?:s|gy|ging)?\b|stutter|버벅|렉\b",
    "freeze":      r"\bfreez(?:e|es|ing)\b|\bhang(?:s|ing)?\b|멈춤|먹통",
    # 'closing' 은 3인칭 keeps 만 받는다 — "I can't keep closing my eyes" 오탐.
    "crash":       r"crash(?:es|ing)?\b|force close|"
                   r"\bkeeps?\s+crashing\b|\bkeeps\s+(?:closing|force.?clos\w+)\b|"
                   r"튕김|강제\s*종료",
    # "won't update"·"update stuck" 이 없어 업데이트 실패 제보가 lag/freeze 로만 잡혔다.
    # 선행 \b 필수 — 없으면 "signifi|cant upgrade" 가 매치된다(실측 오탐 5건).
    # 'won't upgrade' 는 구매 의사라 빼고, update 쪽에만 허용한다.
    "update_fail": r"update (?:failed|broke|bricked)|"
                   r"\b(?:won'?t|wont|does\s?n'?t|can'?t|cannot|unable to|"
                   r"fail(?:s|ed|ing)? to)\s+(?:update|install the update)\b|"
                   r"\b(?:can'?t|cannot|unable to|fail(?:s|ed|ing)? to)\s+upgrade\b|"
                   r"\bupdate\b[^.!?]{0,20}\b(?:stuck|fail(?:s|ed|ing)?|won'?t install|"
                   r"keeps? failing)\b|"
                   r"stuck\s+(?:on|at)\s+(?:the\s+)?update\b|"
                   r"업데이트\s*(?:후|이후).{0,10}(?:문제|버그|오류)|"
                   r"업데이트(?:가)?\s*(?:안\s*(?:되|됨|돼)|실패|멈춤)",
    "disconnect":  r"disconnect|drop(?:s|ping) connection|연결\s*끊",
    "no_signal":   r"no signal|no service|신호\s*없|먹통",
    # 'dust' 단독은 "IP68 dust resistance"(방진 스펙)·"collecting dust"(안 쓴다는 관용구)를
    # 전부 결함으로 셌다(표본에서 firsthand 0%). **유입 문맥**을 요구한다.
    "dust_ingress": r"\b(?:dust|debris|lint|particles?|powder)\b[^.!?]{0,30}"
                    r"\b(?:in|into|inside|under|behind|enter\w*|got\s+in|trapped|stuck)\b"
                    r"|\b(?:in|into|inside|under)\b[^.!?]{0,20}\b(?:dust|debris|lint|powder)\b"
                    r"|\b(?:collect\w*|gather\w*|accumulat\w*)\s+(?:dust|debris|lint|powder)\b"
                    r"|먼지[^.!?]{0,10}(?:들어|끼|유입|낌)|이물[^.!?]{0,10}(?:들어|유입|낌)",
    # 'gap' 단독은 "five-year gap"·"price gap" 을 결함으로 셌다. 물리적 유격만 잡는다.
    "gap":         r"\bgap(?:s|ping)?\b[^.!?]{0,25}"
                   r"\b(?:hinge|screen|display|frame|body|edge|panel|case)\b"
                   r"|\b(?:hinge|screen|display|frame|body|edge|panel)\b[^.!?]{0,25}\bgap(?:s|ping)?\b"
                   r"|유격|틈새|틈이\s*(?:벌|생)",
    "scratch":     r"\bscratch(?:es|ed)?\b|긁힘|기스",
    "peeling":     r"peel(?:ing|ed)?|들뜸|벗겨",
    # 폴더블 내부 화면/보호필름 기포 — 증상 클래스 자체가 없어 전부 놓쳤다.
    # 'bubble' 단독은 Android Bubbles 기능 / bubble level / filter bubble 이라
    # **전치사(under/on/in) + 화면** 또는 **필름·보호필름 근접**을 요구한다.
    "bubble":      r"\bbubbl(?:e|es|ed|ing)\b[^.!?]{0,25}"
                   r"\b(?:under|underneath|beneath|on|in|along)\s+(?:the\s+|my\s+)?"
                   r"(?:screen|display|protector|film|panel|glass)\b"
                   r"|\b(?:screen\s*protector|protector|film)\b[^.!?]{0,30}"
                   r"\bbubbl(?:e|es|ed|ing)\b"
                   r"|\bbubbl(?:e|es|ed|ing)\b[^.!?]{0,20}\b(?:screen\s*protector|protector|film)\b"
                   r"|(?:필름|액정|화면|보호\s*필름)[^.!?]{0,10}기포|기포[^.!?]{0,10}(?:필름|액정|화면)",
    # 'crease' 는 폴더블 리뷰마다 등장하는 중립 서술이라(표본 firsthand 0%,
    # "주름이 직사광선 아니면 거의 안 보인다" 같은 칭찬 포함) 불만 문맥을 요구한다.
    "crease":      r"\bcrease[ds]?\b[^.!?]{0,40}"
                   r"\b(?:worse|deeper|visible|noticeable|annoying|bother\w*|problem|issue|"
                   r"prominent|got|getting|bad)\b"
                   r"|\b(?:deep|bad|worse|annoying|visible|noticeable)\b[^.!?]{0,20}\bcrease[ds]?\b"
                   r"|주름[^.!?]{0,15}(?:심|깊|거슬|불편|눈에\s*띄|생겼)",
    "noise":       r"\bcrackl\w*|\brattl\w*|\bbuzzing\b|잡음|소음",
    "blurry":      r"blurry|out of focus|흐릿|초점\s*안",
    "discolor":    r"discolor|yellowing|변색|누렇",
}

# 절(clause) 경계. 문장부호뿐 아니라 쉼표·등위접속사도 절을 나눈다.
# 문장 종결만 막으면 "The hinge has a gap and dust got in, battery drains fast" 에서
# gap/dust 가 다음 절의 battery 에 붙는다(실측 오결합).
_CLAUSE_BREAK = re.compile(r"[.!?\n,;]|\b(?:and|but|while|also|however)\b", re.IGNORECASE)

_COMPONENT_RE = {k: re.compile(v, re.IGNORECASE) for k, v in _COMPONENT_SRC.items()}
_SYMPTOM_RE = {k: re.compile(v, re.IGNORECASE) for k, v in _SYMPTOM_SRC.items()}

# ── 부정·반증 처리 ────────────────────────────────────────────────────────
# 부정 처리가 없어 "scratch-resistant"(내구성 칭찬)·"Not a scratch"(중고 판매글)·
# "less lag"(개선 언급)이 전부 결함으로 계상됐다. 증상 매치 앞/뒤 좁은 창만 본다
# (문장 전체를 보면 다른 절의 부정까지 끌어와 위음성이 커진다).
_NEG_WINDOW = 40
# 주의: won't/doesn't/didn't 같은 **조동사 부정형은 넣지 않는다**. 그것들은 부정이 아니라
# 결함 표현 자체다("hinge won't open due to powder", "doesn't work"). 넣었더니
# 실제 고장 제보가 통째로 지워졌다(위음성).
_NEG_BEFORE = re.compile(
    r"\b(?:no|not|never|without|less|fewer|zero|"
    r"barely|hardly|scarcely|rarely)\b[^.!?]{0,20}$",
    re.IGNORECASE)
# 부정어가 매치 **안쪽**에 오는 경우 — "crease is barely visible" 처럼 다중어 패턴에서
# 앞쪽만 보면 놓친다. 정도부사(약화)와 진짜 부정을 나눈다.
_NEG_INSIDE_SOFT = re.compile(
    r"\b(?:barely|hardly|scarcely|rarely)\b", re.IGNORECASE)
_NEG_INSIDE_HARD = re.compile(
    r"\b(?:not|never|no longer)\b", re.IGNORECASE)
# 증상 패턴 **자체가** 부정어를 품는 것들("not working", "not turning on",
# "won't charge"). 여기에 HARD 검사를 걸면 패턴이 스스로를 지운다 —
# 실측으로 not_working·no_power 의 영어 주력 표현이 통째로 사장돼 있었다.
_SELF_NEGATING = {"not_working", "no_power", "no_charge", "update_fail",
                  "no_signal", "drain", "dead_pixel"}
_NEG_AFTER = re.compile(
    r"^[^.!?]{0,15}\b(?:resistant|proof|free)\b", re.IGNORECASE)
# 증상별 반증 관용구 — 결함이 아닌 표현이 그 증상으로 잡히는 것 차단
_ANTI = {
    "no_power": re.compile(r"dead\s+(?:simple|easy|centre|center)", re.IGNORECASE),
    # broke/broken 은 은유가 압도적으로 많다. 문맥 허용목록(부품 근접)으로 막으면
    # "My S6 broke"·"broken Fold 6" 같은 진짜 고장까지 죽어서(실측 firsthand 4건 소실)
    # **관용구 차단목록**으로 간다. 'broke down' 은 진짜 고장이라 제외.
    "break": re.compile(
        r"brok(?:e|en)\s+(?:the\s+)?(?:news|story|record|ground|even)\b"
        r"|\bbroke\s+(?:up|out)\b"
        r"|\bbroken\s+(?:into|down\s+into)\b"
        r"|(?:almost|nearly|went|going|flat)\s+broke\b"
        r"|\bbroken\s+(?:person|people|home|heart|english|promise|system|window|clock)\b"
        r"|\bbreaking\s+news\b",
        re.IGNORECASE),
    # "when I'm not working"(사람이 근무 중이 아님) / "I won't open it"(안 열겠다는 의사)
    "not_working": re.compile(
        r"\b(?:i'?m|im|i am|we'?re|you'?re|he'?s|she'?s|they'?re|while|when)\s+not working\b"
        r"|\b(?:i|we|you|they|he|she)\s+(?:wo|do|does|did)n'?t\s+(?:open|launch|start)\b"
        # "Samsung will not launch the Galaxy Watch9"(출시 계획) — 앱 실행 실패가 아니다
        r"|\b(?:won'?t|will not|does\s?n'?t)\s+launch\s+(?:the\s+|a\s+|its\s+|their\s+|new\s+)?"
        r"(?:galaxy|iphone|pixel|watch|phone|device|product|model|version|series)\b"
        # 사람이 무반응(응급 감지 기능 설명) — 기기 무반응이 아니다
        r"|\b(?:user|patient|person|someone|he|she|they)\s+(?:becomes?|is|are|was|were)\s+"
        r"unresponsive\b",
        re.IGNORECASE),
}

# 넓은 창(앞 60 / 뒤 40) 반증 — 주제 자체가 기기 결함이 아님을 알리는 단어.
# 좁은 _ANTI 로는 못 잡는다("The weather is really hot these days").
# **게이트가 있는 증상만** 검사한다 — 'overheat'/'발열' 같은 명시 어휘까지
# 날씨 단어 하나로 지우면 위음성이 커지므로, 느슨한 'hot' 계열에만 건다.
_ANTI_WIDE_GATE = {
    "overheat": re.compile(r"hot", re.IGNORECASE),
    "no_power": re.compile(r"turn", re.IGNORECASE),
}
_ANTI_WIDE = {
    "overheat": re.compile(
        r"\b(?:weather|summer|climate|outdoors?|sunny|sunlight|black hole|"
        r"business|market|season|stock)\b", re.IGNORECASE),
    # "didn't turn on the light mode"·"can't turn on the notification sound" —
    # 설정 토글에 대한 사용자 선택이지 전원 사망이 아니다.
    "no_power": re.compile(
        r"turn\s+on\s+[^.!?]{0,25}\b(?:light|vibrat\w*|sound|notification|internet|"
        r"wi-?fi|bluetooth|mode|option|setting|feature|app)\b", re.IGNORECASE),
}
_ANTI_WIDE_BEFORE, _ANTI_WIDE_AFTER = 60, 40

# "collecting dust" 는 두 뜻이 겹친다 — "accessories collecting dust"(방치 관용구) vs
# "the hinge collected dust"(실제 유입). 정규식만으로는 못 가르고, **부품이 주어인지**로
# 구분한다. 앞쪽 창에 부품 언급이 없으면 관용구로 본다.
_COLLECT_DUST = re.compile(
    r"\b(?:collect\w*|gather\w*|accumulat\w*)\s+(?:dust|debris|lint|powder)\b",
    re.IGNORECASE)
_ANY_COMPONENT = re.compile("|".join(_COMPONENT_SRC.values()), re.IGNORECASE)

# 맨 'dead' 는 문맥 없이 받으면 제목·인명이 전부 전원사망이 된다
# (실측 오탐: "The Walking Dead" → device/no_power). 근처에 기기·부품 명사가
# 있을 때만 인정한다. dead_pixel 은 자체 패턴이 따로 있어 영향받지 않는다.
_AMBIGUOUS_LITERAL = {
    "no_power": re.compile(r"^dead$", re.IGNORECASE),
}
# 브랜드명(Galaxy·Pixel)은 넣지 않는다 — 은하/게임/동사로 더 자주 쓰인다.
_DEVICE_CTX = re.compile(
    "|".join(_COMPONENT_SRC.values())
    + r"|\b(?:phone|smartphone|handset|device|tablet|watch|buds?|earbuds?|charger|"
      r"laptop|unit|hardware)\b|폰|기기|휴대폰|스마트폰|단말|제품",
    re.IGNORECASE)
# 120자 — "The screen suddenly goes blank ... it looks like he's dead" 처럼
# 주어 부품과 증상이 한두 문장 떨어진 실제 제보를 살리는 폭(실측으로 정한 값).
_CTX_WINDOW = 120


def _is_negated(text: str, start: int, end: int, symptom: str) -> bool:
    """증상 매치가 부정·반증 문맥이면 True (결함으로 세지 않음)."""
    if _NEG_BEFORE.search(text[max(0, start - _NEG_WINDOW):start]):
        return True
    if _NEG_INSIDE_SOFT.search(text[start:end]):
        return True
    if symptom not in _SELF_NEGATING and _NEG_INSIDE_HARD.search(text[start:end]):
        return True
    amb = _AMBIGUOUS_LITERAL.get(symptom)
    if amb and amb.match(text[start:end]) and not _DEVICE_CTX.search(
            text[max(0, start - _CTX_WINDOW):end + _CTX_WINDOW]):
        return True
    if symptom == "dust_ingress":
        lead = text[max(0, start - 40):start]
        if _COLLECT_DUST.search(text[max(0, start - 40):end + 10]) \
                and not _ANY_COMPONENT.search(lead):
            return True
    if _NEG_AFTER.search(text[end:end + _NEG_WINDOW]):
        return True
    gate = _ANTI_WIDE_GATE.get(symptom)
    if gate and gate.search(text[start:end]) and _ANTI_WIDE[symptom].search(
            text[max(0, start - _ANTI_WIDE_BEFORE):end + _ANTI_WIDE_AFTER]):
        return True
    anti = _ANTI.get(symptom)
    # 앞 30자 — "if the user becomes unresponsive" 처럼 주어가 한 칸 더 앞에 오는 관용구까지 본다
    return bool(anti and anti.search(text[max(0, start - 30):end + 20]))

# 증상↔부품을 같은 맥락으로 볼 최대 거리(문자). 한 문장~두 문장 범위.
_WINDOW = 120
# 과도한 추출 방지 — 한 문서에서 뽑을 최대 결함 수
_MAX_PER_DOC = 12


def _clause_bounds(text: str, pos: int) -> Tuple[int, int]:
    """pos 가 속한 절의 [시작, 끝) 범위."""
    start = 0
    end = len(text)
    for m in _CLAUSE_BREAK.finditer(text):
        if m.end() <= pos:
            start = m.end()
        elif m.start() >= pos:
            end = m.start()
            break
    return start, end


def extract_defects(text: str, window: int = _WINDOW) -> List[Tuple[str, str, str]]:
    """본문 → [(component, symptom, severity)] (중복 제거·정렬).

    페어링 규칙(순서대로).
      1) **같은 절 안의** 부품 중 가장 가까운 것. 절을 넘으면 다른 주어의 부품에
         붙는다("gap and dust got in, battery drains" 에서 gap→battery 오결합).
      2) 절 안에 없으면 window 안의 최근접 부품(절 넘어감 허용 — 대명사적 참조 대응).
      3) 그래도 없으면 증상이 함의하는 부품(_IMPLIED), 최후엔 'device'.

    한계: "dust got in" 처럼 절 안에 부품이 없고 앞 절을 가리키는 조응(anaphora)은
    최근접으로 떨어져 틀릴 수 있다. 정확히 풀려면 구문 분석이 필요해 여기선 감수한다.
    """
    if not text:
        return []

    comps: List[Tuple[str, int]] = []
    for name, pat in _COMPONENT_RE.items():
        for m in pat.finditer(text):
            comps.append((name, m.start()))

    out = set()
    for sname, spat in _SYMPTOM_RE.items():
        for m in spat.finditer(text):
            spos = m.start()
            if _is_negated(text, spos, m.end(), sname):
                continue
            cs, ce = _clause_bounds(text, spos)

            # 1) 같은 절 안 최근접
            best, bestd = None, window + 1
            for cname, cpos in comps:
                if cs <= cpos < ce:
                    d = abs(cpos - spos)
                    if d < bestd:
                        best, bestd = cname, d

            # 2) 절 안에 없으면 전체에서 최근접
            if best is None:
                bestd = window + 1
                for cname, cpos in comps:
                    d = abs(cpos - spos)
                    if d < bestd:
                        best, bestd = cname, d

            component = best if best is not None else _IMPLIED.get(sname, "device")
            out.add((component, sname, _SEVERITY.get(sname, "degraded")))
            if len(out) >= _MAX_PER_DOC:
                return sorted(out)
    return sorted(out)
