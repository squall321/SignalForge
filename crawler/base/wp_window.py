# WordPress REST 기간 창(after/before)을 크롤러들이 같은 방식으로 쓰게 하는 공용 모듈.
"""wp_window — WP REST 역사 수집의 공통 부품.

왜 공용인가:
  wp-json 을 쓰는 크롤러가 11개다. 기간 창 처리를 각자 복제하면 반드시 갈라진다
  (실측 전례 — 한국어 검색이 backend/MCP 로 갈라졌던 건). 특히 아래 두 가지는
  한 곳에서만 맞으면 나머지가 조용히 틀린다.

  1. **기간 필터를 무시하는 사이트가 있다.** 200 과 데이터를 주면서 after/before
     를 안 본다(실측 MobileSyrup — 2022년을 요청했는데 2026년 글을 반환).
     못 걸러내면 과거를 긁는 줄 알고 최신만 되풀이 수집한다.
  2. 창이 없을 때의 기본 동작 — 창을 안 주면 평소처럼 최신을 긁어야 한다.
     역사 백필만 창을 준다.

env (크롤러별 접두 + 공통 폴백):
  <PREFIX>_AFTER / <PREFIX>_BEFORE   예: HIPERTEXTUAL_AFTER=2022-01-01T00:00:00
  WP_BACKFILL_AFTER / WP_BACKFILL_BEFORE   접두가 없을 때 쓰는 공통값
"""
import logging
import os
from typing import Optional, Sequence

log = logging.getLogger("wp_window")


def read_window(prefix: str) -> tuple:
    """(after, before) 를 env 에서 읽는다. 없으면 ('', '')."""
    p = prefix.upper()
    after = (os.getenv(f"{p}_AFTER", "") or os.getenv("WP_BACKFILL_AFTER", "")).strip()
    before = (os.getenv(f"{p}_BEFORE", "") or os.getenv("WP_BACKFILL_BEFORE", "")).strip()
    return after, before


def window_params(after: str, before: str) -> dict:
    """WP REST 쿼리에 얹을 기간 파라미터. 창이 없으면 빈 dict."""
    out = {}
    if after:
        out["after"] = after
    if before:
        out["before"] = before
    return out


def window_respected(posts: Sequence, after: str, before: str) -> bool:
    """받은 글의 날짜가 요청한 창 안인가.

    창을 지정하지 않았으면 항상 참. 하나라도 창 안이면 참으로 본다(경계 글이
    섞이는 것은 정상). 전부 창 밖이면 그 사이트는 필터를 무시하는 것이다.
    날짜를 하나도 못 읽으면 판단을 보류한다 — 멀쩡한 매체를 끊으면 안 된다.
    """
    if not after and not before:
        return True
    lo, hi = after[:10], before[:10]
    seen_any = False
    for po in posts:
        if not isinstance(po, dict):
            continue
        d = (po.get("date_gmt") or po.get("date") or "")[:10]
        if not d:
            continue
        seen_any = True
        if (not lo or d >= lo) and (not hi or d <= hi):
            return True
    return not seen_any


def warn_ignored(source: str, after: str, before: str) -> None:
    """필터 무시를 한 줄로 알린다 — 조용히 헛돌지 않게."""
    log.warning(
        "%s — WP REST 기간 필터가 무시된다(요청 %s~%s). 이 매체는 역사 수집에 "
        "쓸 수 없다. 최신만 되풀이 긁게 되므로 창을 걷는다.",
        source, after or "~", before or "~")


def describe(after: str, before: str) -> str:
    """로그용 창 표기."""
    if not after and not before:
        return "전체"
    return f"[{after or '~'}~{before or '~'}]"
