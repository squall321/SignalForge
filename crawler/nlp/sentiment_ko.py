"""한국어 감성 분석 — 제품 VOC 특화 사전 기반 (오프라인, 번역 불필요).

데이터의 94%가 한국어인데 영어 전용 VADER가 한국어를 못 읽어
미번역 행의 sentiment 가 무의미한 neutral 로 채워지던 문제를 해결한다.
한국어 원문에서 직접 극성을 산출하므로 번역 성공 여부와 무관.

방식: 제품 리뷰에서 빈출하는 긍/부정 표현을 substring 스캔하여 가중 합산,
      부정어(안/못/없/않) 윈도우로 극성 반전, 강조어로 증폭. tanh 정규화.
완벽한 NLP가 아니라 false-neutral 을 의미 있는 신호로 바꾸는 실용 사전.
"""
import math
from typing import Tuple

# 긍정 표현 → 가중치
_POS = {
    "좋다": 1.5, "좋아": 1.5, "좋은": 1.4, "좋네": 1.4, "좋고": 1.2, "좋음": 1.4,
    "만족": 1.8, "훌륭": 2.0, "최고": 2.0, "추천": 1.6, "괜찮": 1.0, "굿": 1.2,
    "빠르": 1.2, "선명": 1.2, "예쁘": 1.4, "이쁘": 1.4, "대박": 1.8, "갓": 1.5,
    "혜자": 1.8, "꿀": 1.3, "편하": 1.3, "쾌적": 1.4, "깔끔": 1.2, "탄탄": 1.2,
    "안정적": 1.5, "잘된다": 1.5, "잘 된다": 1.5, "잘나와": 1.4, "잘 나와": 1.4,
    "맘에 든다": 1.8, "마음에 든다": 1.8, "감동": 1.6, "강추": 2.0, "튼튼": 1.3,
    "가성비": 1.2, "쓸만": 1.0, "신세계": 1.6, "환상": 1.7, "역대급": 1.8,
}

# 부정 표현 → 가중치(음수)
_NEG = {
    "별로": -1.6, "실망": -2.0, "불편": -1.6, "느리": -1.5, "버벅": -1.8,
    "렉": -1.5, "발열": -1.6, "뜨겁": -1.4, "방전": -1.8, "광탈": -2.0,
    "안돼": -1.6, "안 돼": -1.6, "안된다": -1.6, "안 된다": -1.6, "문제": -1.2,
    "오류": -1.6, "버그": -1.6, "고장": -2.0, "먹통": -2.0, "구리": -1.6,
    "최악": -2.5, "거지같": -2.5, "쓰레기": -2.5, "짜증": -1.8, "불량": -2.2,
    "하자": -1.8, "깨짐": -1.8, "깨졌": -1.8, "비싸": -1.3, "창렬": -1.8,
    "후회": -2.0, "반품": -1.6, "교환": -1.2, "안좋": -1.8, "안 좋": -1.8,
    "떨어진다": -1.2, "튕김": -1.6, "튕긴": -1.6, "끊김": -1.5, "끊긴": -1.5,
    "심각": -1.6, "노답": -2.0, "갑갑": -1.3, "답답": -1.4, "아쉽": -1.2,
    "거슬": -1.3, "별루": -1.6, "에바": -1.4, "혹사": -1.4, "막장": -2.0,
}

# 부정어(앞에 오면 극성 반전), 강조어(증폭)
_NEGATORS = ("안 ", "안", "못 ", "못", "없", "않", "비추")
_INTENSIFIERS = ("너무", "진짜", "완전", "개", "존나", "졸라", "짱", "매우", "정말", "겁나")

# 복합어 오탐 위험 단어: 앞 글자가 한글이면 접미/합성어로 보고 무시
#   예) "색상별로/가격별로/종류별로"의 '별로'(=per/by) ≠ '별로'(=meh)
_BOUNDARY_SENSITIVE = ("별로", "별루", "갓", "굿", "꿀", "렉")


def _is_hangul(ch: str) -> bool:
    return "가" <= ch <= "힣"

_POS_TH = 0.20
_NEG_TH = -0.20


def _has_negator_before(text: str, pos: int, window: int = 6) -> bool:
    seg = text[max(0, pos - window):pos]
    return any(n in seg for n in _NEGATORS)


def _has_intensifier_before(text: str, pos: int, window: int = 8) -> bool:
    seg = text[max(0, pos - window):pos]
    return any(i in seg for i in _INTENSIFIERS)


def analyze_sentiment_ko(text: str) -> Tuple[float, str]:
    """한국어 텍스트 극성. Returns (score -1~1, label)."""
    if not text:
        return 0.0, "neutral"

    t = text[:1000]
    raw = 0.0
    for lex in (_POS, _NEG):
        for term, weight in lex.items():
            start = 0
            while True:
                idx = t.find(term, start)
                if idx == -1:
                    break
                # 복합어 오탐 가드: 위험 단어가 한글 뒤에 붙으면 접미/합성어로 간주
                if term in _BOUNDARY_SENSITIVE and idx > 0 and _is_hangul(t[idx - 1]):
                    start = idx + len(term)
                    continue
                w = weight
                if _has_intensifier_before(t, idx):
                    w *= 1.5
                if _has_negator_before(t, idx):
                    w *= -0.8  # 반전 + 약화 (이중부정/완곡 고려)
                raw += w
                start = idx + len(term)

    # tanh 정규화 → -1~1 (raw 약 ±4에서 포화)
    score = round(math.tanh(raw / 4.0), 4)
    if score >= _POS_TH:
        label = "positive"
    elif score <= _NEG_TH:
        label = "negative"
    else:
        label = "neutral"
    return score, label
