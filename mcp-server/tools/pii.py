# MCP 로 나가는 응답의 개인정보(작성자·계정·메일·전화·IMEI·일련번호 꼴)를 기본으로 가리는 필터
"""SG-11 · SG-18 (가) — 나가는 응답은 기본으로 가린다, 원문은 SignalForge 화면에서 본다.

가리는 것
  - 작성자 이름 → '작성자#xxxxxxxx'(프로세스마다 새 키의 HMAC — 한 응답·한 기동 안에서는 같은 사람이
    같은 꼬리표라 '같은 작성자 글 여럿' 은 보이되, 이름으로 되돌릴 수 없다)
  - 본문의 메일 · 전화 · IMEI(라벨이 붙었거나 Luhn 이 맞는 15자리) · 라벨 붙은 일련번호(S/N · 시리얼 ·
    일련번호 · 제조번호 · MEID) · @계정 · u/계정
  - 원문 링크 안의 계정 칸(x.com/<계정>/status → x.com/i/web/status 로 링크는 산다, /@계정 · /u/계정 ·
    bsky profile 은 칸만 가린다)

가리지 않는 것 — 모델코드(SM-S938B · SM-R630NZAMXAR) · 펌웨어 빌드(S938BXXU1AYA4) · 가격 · 날짜 ·
링크 안의 해시. 라벨 없는 'R+10자' 일련번호 꼴은 시계 모델 SKU·펌웨어와 겹쳐(실측) 가리지 않는다.
본문의 일반 사람 이름은 찾지 않는다(개체명 인식이 없다 — @·u/ 꼴 계정만).
"""
import functools
import hashlib
import hmac
import os
import re
from typing import Any, Optional

_KEY = os.urandom(16)

AUTHOR_KEYS = frozenset({"author_name", "author", "username", "user_name", "author_id", "user_id", "handle"})
URL_KEYS = frozenset({"source_url", "url"})
TEXT_KEYS = frozenset({"content_original", "content_translated", "snippet", "content", "text",
                       "excerpt", "quote", "sample", "body"})


def author_label(name: Optional[str]) -> Optional[str]:
    if name is None or not str(name).strip():
        return name
    tag = hmac.new(_KEY, str(name).strip().lower().encode("utf-8"), hashlib.sha256).hexdigest()[:8]
    return f"작성자#{tag}"


def _luhn_ok(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)*\.[A-Za-z]{2,}")
# 라벨 + 값 — 값은 대문자·숫자(소문자 'serial killer' · 'serial port' 를 피한다), 숫자 둘 이상
_LABELED = re.compile(
    r"(?i:\b(?:s/n|sn|serial(?:\s*(?:number|no\.?|#))?|imei\s*[12]?|meid)\b|시리얼(?:\s*(?:넘버|번호))?|"
    r"일련\s*번호|제조\s*번호)"
    r"(\s*(?:[:#=]|(?i:is|was))?\s*)"
    r"([A-Z0-9][A-Z0-9-]{6,22})(?![A-Za-z0-9])")
_IMEI_SPLIT = re.compile(r"(?<![\w/=.-])\d{2}[- ]\d{6}[- ]\d{6}[- ]\d(?![\w-])")
_IMEI_15 = re.compile(r"(?<![\w/=.-])\d{15}(?!\w)")
_PHONE_KR = re.compile(r"(?<![\w.-])01[016789][-. ]?\d{3,4}[-. ]?\d{4}(?![\w-])")
_PHONE_KR_LAND = re.compile(r"(?<![\w.-])0(?:2|[3-6][1-5])[-. ]\d{3,4}[-. ]\d{4}(?![\w-])")
_PHONE_INTL = re.compile(r"(?<![\w+])\+\d{1,3}[ .-]?(?:\(\d{1,4}\)|\d{1,4})(?:[ .-]?\d{2,4}){2,4}(?![\w-])")
# 공식 계정(브랜드)은 개인정보가 아니다 — 가리면 'SamsungMobile 공지' 같은 맥락만 잃는다
_BRAND_HANDLE = re.compile(r"samsung|galaxy|apple|google|pixel|android|oneplus|xiaomi|huawei|oppo|vivo|"
                           r"motorola|nothing|qualcomm|sammobile", re.I)
_HANDLE = re.compile(r"(?<![\w@./-])@([A-Za-z0-9_가-힣][A-Za-z0-9_.가-힣]{1,29})")
_REDDIT_USER = re.compile(r"(?<![\w/])(/?u/)([A-Za-z0-9_-]{3,20})\b")


def _mask_labeled(m: re.Match) -> str:
    value = m.group(2)
    if sum(c.isdigit() for c in value) < 2:
        return m.group(0)
    head = m.group(0)[:m.start(2) - m.start(0)]
    return head + ("[IMEI]" if "imei" in head.lower() or "meid" in head.lower() else "[일련번호]")


def _mask_phone_intl(m: re.Match) -> str:
    return "[전화]" if sum(c.isdigit() for c in m.group(0)) >= 9 else m.group(0)


def _mask_handle(m: re.Match) -> str:
    return m.group(0) if _BRAND_HANDLE.search(m.group(1)) else "@[계정]"


def mask_text(s: Optional[str]) -> Optional[str]:
    if not s or not isinstance(s, str):
        return s
    s = _URL_IN_TEXT.sub(lambda m: mask_url(m.group(0)), s)     # 본문에 붙여 넣은 링크의 계정 칸
    s = _EMAIL.sub("[메일]", s)
    s = _LABELED.sub(_mask_labeled, s)
    s = _IMEI_SPLIT.sub("[IMEI]", s)
    s = _IMEI_15.sub(lambda m: "[IMEI]" if _luhn_ok(m.group(0)) else m.group(0), s)
    s = _PHONE_KR.sub("[전화]", s)
    s = _PHONE_KR_LAND.sub("[전화]", s)
    s = _PHONE_INTL.sub(_mask_phone_intl, s)
    s = _HANDLE.sub(_mask_handle, s)
    s = _REDDIT_USER.sub(lambda m: m.group(1) + "[계정]", s)
    return s


_URL_IN_TEXT = re.compile(r"https?://[^\s<>\"')\]]+")
_X_STATUS = re.compile(r"^(https?://)(?:www\.|mobile\.)?(?:x|twitter)\.com/[^/?#]+/status(?:es)?/(\d+)", re.I)
_URL_AT = re.compile(r"/@[^/?#]+")
_URL_USER = re.compile(r"/(u|user|users)/[^/?#]+", re.I)
_URL_BSKY = re.compile(r"(bsky\.app/profile/)[^/?#]+", re.I)


def mask_url(u: Optional[str]) -> Optional[str]:
    if not u or not isinstance(u, str):
        return u
    m = _X_STATUS.match(u)
    if m:
        return f"{m.group(1)}x.com/i/web/status/{m.group(2)}" + u[m.end():]
    u = _URL_BSKY.sub(r"\1[계정]", u)
    u = _URL_AT.sub("/@[계정]", u)
    return _URL_USER.sub(lambda mm: f"/{mm.group(1)}/[계정]", u)


def mask(obj: Any) -> Any:
    """응답 전체를 훑어 가린다 — 키 이름으로 칸의 성격을 본다(작성자·링크·본문)."""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if k in AUTHOR_KEYS and isinstance(v, str):
                out[k] = author_label(v)
            elif k in URL_KEYS and isinstance(v, str):
                out[k] = mask_url(v)
            elif k in TEXT_KEYS and isinstance(v, str):
                out[k] = mask_text(v)
            elif isinstance(v, (dict, list, tuple)):
                out[k] = mask(v)
            else:
                out[k] = v
        return out
    if isinstance(obj, (list, tuple)):
        return [mask(x) for x in obj]
    if isinstance(obj, str):
        return mask_text(obj)
    return obj


def masked(fn):
    """도구 함수를 감싸 결과를 가린다 — 서명은 그대로(FastMCP 가 인자 스키마를 서명에서 짓는다)."""
    @functools.wraps(fn)
    async def run(*args, **kwargs):
        return mask(await fn(*args, **kwargs))
    return run
