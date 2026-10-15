# 수집 헬스 알림이 사이트마다 쪼개져 채널을 익사시키지 않는지 고정한다.
"""막는 고장(2026-10-15 실측).

알림 metric 이 `collection.<사이트>` 라 쿨다운이 사이트 단위로 걸렸고, 활성
사이트 38곳이 시간마다 각자 발화해 **하루 279.9건**이 됐다. 30일 8,396건으로
전체 알림의 **98.2%** 였다. 그 양이면 —
  · 알림 채널이 묻힌다(웹훅을 붙여도 소음이다)
  · Alerts 화면이 이 룰로 도배돼 다른 알림에 도달할 수 없다
  · "수집이 멈췄다"는 진짜 신호가 같은 모양의 280건에 섞인다

고친 방향은 양을 줄이는 게 아니라 **신호를 바꾸는 것**이다.
"같은 사이트가 계속 죽어 있다"는 반복은 소음이고, "새로 죽은 사이트가
생겼다"가 신호다. 사이트 상세는 스냅샷(reports/collection_health_*.json)에
전량 남으므로 알림은 종류 단위로 묶고 payload 에 목록을 싣는다.

실측 — 사이트 위반 21건 → 알림 2종, 반복 실행은 inserted:0 skipped:2.
"""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from insight.collection_health import _kind, group_violations  # noqa: E402


def _v(code, metric, severity="critical", reason="r"):
    return {"code": code, "metric": metric, "severity": severity,
            "reason": reason, "value": 0.0, "threshold": 3.0}


# ── 종류 판정 ────────────────────────────────────────────────────────────
def test_kind_classification():
    assert _kind("collection.zero_yield.gsmarena")[0] == "collection.zero_yield"
    assert _kind("collection.null_date.dogdrip")[0] == "collection.null_date"
    assert _kind("collection.xda")[0] == "collection.drop"
    assert _kind("")[0] == "collection.other"


def test_specific_prefix_wins_over_general():
    """'collection.' 이 먼저 매칭되면 zero_yield·null_date 가 전부 drop 으로 뭉개진다."""
    assert _kind("collection.zero_yield.x")[0] != "collection.drop"
    assert _kind("collection.null_date.x")[0] != "collection.drop"


# ── 묶기 ────────────────────────────────────────────────────────────────
def test_many_sites_collapse_to_few_alerts():
    """이게 핵심이다 — 사이트 수만큼 알림이 나오면 채널이 익사한다."""
    vs = [_v(f"site{i}", f"collection.zero_yield.site{i}") for i in range(15)]
    vs += [_v(f"drop{i}", f"collection.drop{i}") for i in range(10)]
    g = group_violations(vs)
    assert len(g) == 2, f"25건이 {len(g)}종으로 묶였다 — 기대 2종"
    assert len(g["collection.zero_yield"]["codes"]) == 15
    assert len(g["collection.drop"]["codes"]) == 10


def test_site_list_is_preserved():
    """묶으면서 정보를 잃으면 안 된다 — 어느 사이트인지 알아야 조치할 수 있다."""
    vs = [_v("gsmarena", "collection.zero_yield.gsmarena", reason="차단됨"),
          _v("quora", "collection.zero_yield.quora", reason="Cloudflare")]
    g = group_violations(vs)["collection.zero_yield"]
    assert g["codes"] == ["gsmarena", "quora"], g["codes"]
    assert g["reasons"]["gsmarena"] == "차단됨"
    assert g["reasons"]["quora"] == "Cloudflare"


def test_codes_are_sorted_for_stable_diff():
    """직전 발화와 집합을 비교하므로 순서가 흔들리면 안 된다."""
    g = group_violations([_v("zeta", "collection.zero_yield.zeta"),
                          _v("alpha", "collection.zero_yield.alpha")])
    assert g["collection.zero_yield"]["codes"] == ["alpha", "zeta"]


def test_duplicate_site_counted_once():
    vs = [_v("x", "collection.zero_yield.x"), _v("x", "collection.zero_yield.x")]
    assert group_violations(vs)["collection.zero_yield"]["codes"] == ["x"]


def test_critical_wins_in_group_severity():
    """한 사이트라도 critical 이면 묶음도 critical 이어야 한다 — 아니면 묻힌다."""
    vs = [_v("a", "collection.zero_yield.a", severity="warning"),
          _v("b", "collection.zero_yield.b", severity="critical")]
    assert group_violations(vs)["collection.zero_yield"]["severity"] == "critical"


def test_value_is_affected_site_count():
    """알림 value 가 사이트 수면 '몇 곳이 문제인가'를 바로 읽을 수 있다."""
    vs = [_v(f"s{i}", f"collection.zero_yield.s{i}") for i in range(7)]
    assert group_violations(vs)["collection.zero_yield"]["value"] == 7.0


def test_empty_input_is_safe():
    assert group_violations([]) == {}


# ── 발화 조건 (소스 규약) ────────────────────────────────────────────────
def test_insert_fires_only_on_new_sites_or_heartbeat():
    """상태가 그대로면 반복 발화하지 않아야 한다 — 그게 280건의 원인이었다."""
    src = (ROOT / "insight" / "collection_health.py").read_text()
    body = src.split("async def insert_alert_events", 1)[1].split("\n# ──", 1)[0]
    assert "group_violations" in body, "묶지 않고 사이트별로 INSERT 한다"
    assert "new_codes" in body or "added" in body, "신규 사이트를 구분하지 않는다"
    assert "HEARTBEAT_SEC" in body, "상태가 그대로면 영원히 침묵한다 — heartbeat 가 없다"
    assert "payload->>'metric' = $2" in body, "쿨다운 조회가 종류 단위가 아니다"


def test_metric_in_payload_is_the_kind_not_the_site():
    """payload.metric 이 사이트를 포함하면 쿨다운이 다시 사이트 단위가 된다."""
    src = (ROOT / "insight" / "collection_health.py").read_text()
    body = src.split("async def insert_alert_events", 1)[1].split("\n# ──", 1)[0]
    assert '"metric": kind' in body, 'payload 의 metric 이 종류(kind)가 아니다'
