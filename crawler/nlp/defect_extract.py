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
    "swelling":    r"swell|bulg|배부름|부풀",
    "burn":        r"burn(?:ed|t)?\s+(?:my|the|his|her)?\s*(?:hand|finger|skin)|화상",
    "shock":       r"electric shock|감전",
    "no_power":    r"won'?t turn on|not turning on|dead\b|안\s*켜지|전원이\s*안",
    "boot_loop":   r"boot ?loop|bootloop|무한\s*부팅|부팅\s*반복|"
                   r"random(?:ly)? reboot|reboot(?:s|ing)\s+(?:every|randomly|constantly)|재부팅",
    "not_working": r"not working|doesn'?t work|stopped working|작동\s*(?:하지\s*)?않|안\s*됨|안\s*돼",
    "no_charge":   r"won'?t charge|not charging|충전\s*(?:이)?\s*안",
    "crack":       r"\bcrack(?:ed|s|ing)?\b|깨졌|금이\s*갔|파손",
    "break":       r"broke|broken|고장|망가",
    "water_damage": r"water damage|liquid damage|침수",
    "dead_pixel":  r"dead pixel|stuck pixel|불량\s*화소|데드\s*픽셀",
    "green_line":  r"green line|vertical line|초록\s*줄|녹색\s*줄|세로\s*줄",
    "burn_in":     r"burn-?in|번인",
    "flicker":     r"flicker|깜빡|점멸",
    "overheat":    r"overheat|too hot|발열|뜨거워",
    "drain":       r"\bdrain(?:s|ed|ing)?\b|battery life|배터리\s*(?:가)?\s*빨리|방전",
    "lag":         r"\blag(?:s|gy|ging)?\b|stutter|버벅|렉\b",
    "freeze":      r"freez(?:e|es|ing)|hang(?:s|ing)?\b|멈춤|먹통",
    "crash":       r"crash(?:es|ing)?\b|force close|튕김|강제\s*종료",
    "update_fail": r"update (?:failed|broke|bricked)|업데이트\s*(?:후|이후).{0,10}(?:문제|버그|오류)",
    "disconnect":  r"disconnect|drop(?:s|ping) connection|연결\s*끊",
    "no_signal":   r"no signal|no service|신호\s*없|먹통",
    "dust_ingress": r"\bdust(?:y)?\b|\bdebris\b|\bparticles?\b|먼지|이물",
    "gap":         r"\bgap\b|유격|틈",
    "scratch":     r"\bscratch(?:es|ed)?\b|긁힘|기스",
    "peeling":     r"peel(?:ing|ed)?|들뜸|벗겨",
    "crease":      r"\bcrease[ds]?\b|주름",
    "noise":       r"\bcrackl\w*|\brattl\w*|\bbuzzing\b|잡음|소음",
    "blurry":      r"blurry|out of focus|흐릿|초점\s*안",
    "discolor":    r"discolor|yellowing|변색|누렇",
}

# 부착 판정 시 문장 경계를 넘지 않게 한다. 넘으면 다른 문장의 부품에 잘못 붙는다
# (실측: "hinge collected dust. Also the camera is blurry" 에서 dust→camera 오결합).
_SENT_BREAK = re.compile(r"[.!?\n]")

_COMPONENT_RE = {k: re.compile(v, re.IGNORECASE) for k, v in _COMPONENT_SRC.items()}
_SYMPTOM_RE = {k: re.compile(v, re.IGNORECASE) for k, v in _SYMPTOM_SRC.items()}

# 증상↔부품을 같은 맥락으로 볼 최대 거리(문자). 한 문장~두 문장 범위.
_WINDOW = 120
# 증상 **직후** 부품이 오면(“a green line on the screen”, “dust in the hinge”)
# 그게 문법적 부착이므로 더 가까운 앞쪽 부품보다 우선한다.
_ATTACH_WINDOW = 40
# 과도한 추출 방지 — 한 문서에서 뽑을 최대 결함 수
_MAX_PER_DOC = 12


def extract_defects(text: str, window: int = _WINDOW) -> List[Tuple[str, str, str]]:
    """본문 → [(component, symptom, severity)] (중복 제거·정렬).

    페어링 규칙(순서대로).
      1) 증상 **직후** _ATTACH_WINDOW 안의 부품 — 전치사 부착("on the screen").
         단순 최근접만 쓰면 "gap in the hinge and a green line on the screen" 에서
         green_line 이 더 가까운 hinge 에 잘못 붙는다(실측 실패 사례).
      2) 없으면 window 안의 최근접 부품(앞/뒤 무관).
      3) 그래도 없으면 증상이 함의하는 부품(_IMPLIED), 최후엔 'device'.
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
            spos, send = m.start(), m.end()

            # 1) 증상 직후 부착 우선
            best, bestd = None, _ATTACH_WINDOW + 1
            for cname, cpos in comps:
                if (send <= cpos <= send + _ATTACH_WINDOW
                        and (cpos - send) < bestd
                        and not _SENT_BREAK.search(text[send:cpos])):
                    best, bestd = cname, cpos - send

            # 2) 부착 없으면 최근접(양방향)
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
