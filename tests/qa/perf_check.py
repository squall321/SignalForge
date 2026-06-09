"""트랙 B 성능 검증 스크립트.

29 endpoint (products 3 + analytics 9 + dashboard 1 + kg 3 + temporal 3
+ community 6 + geo 4) × N(default 50)회 호출 →
p50 / p95 / p99 측정 → reports/perf_YYYY-MM-DD.md.

원 사양은 "22 endpoint" 였지만 실제 라우터에 노출된 GET/POST 핸들러
전수 측정이 더 의미있다.

사용:
    python -m tests.qa.perf_check \
        --base http://127.0.0.1:8000 \
        --runs 50 \
        --product GS25U \
        --out reports

캐시 효과 검증:
- `--cold` 1회 호출 후 첫 latency 기록, 동일 endpoint 두 번째부터 cache hit 측정.
- HTTP 200 만 통계 포함, 그 외는 errors 로 분리 보고.

DB 의존 endpoint 가 일부 누락된 product/country 인자에 대해 422/500 을 낼 수 있어
prefilght 단계에서 /api/v1/products 로부터 첫 코드를 가져와 사용한다.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


def _http_get(url: str, timeout: float = 30.0) -> Tuple[int, float, int]:
    """GET URL → (status, latency_ms, body_len). 네트워크 오류 시 status=-1."""
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            body = resp.read()
            return resp.status, (time.perf_counter() - t0) * 1000.0, len(body)
    except urllib.error.HTTPError as e:
        return e.code, (time.perf_counter() - t0) * 1000.0, 0
    except Exception:
        return -1, (time.perf_counter() - t0) * 1000.0, 0


def _http_post_json(url: str, payload: Dict[str, Any], timeout: float = 30.0) -> Tuple[int, float, int]:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
            return resp.status, (time.perf_counter() - t0) * 1000.0, len(body)
    except urllib.error.HTTPError as e:
        return e.code, (time.perf_counter() - t0) * 1000.0, 0
    except Exception:
        return -1, (time.perf_counter() - t0) * 1000.0, 0


def _pct(values: List[float], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    idx = max(0, min(len(s) - 1, int(round((p / 100.0) * (len(s) - 1)))))
    return s[idx]


def _discover_product(base: str, fallback: str) -> str:
    """첫 product code 를 가져온다. 실패 시 fallback."""
    try:
        with urllib.request.urlopen(f"{base}/api/v1/products", timeout=5) as resp:
            data = json.loads(resp.read())
            if isinstance(data, list) and data:
                code = data[0].get("code")
                if code:
                    return code
    except Exception:
        pass
    return fallback


def _build_endpoints(base: str, product: str, products_csv: str) -> List[Dict[str, Any]]:
    """22 endpoint 의 (이름, method, URL, payload) 리스트."""
    q = urllib.parse.quote
    eps: List[Dict[str, Any]] = []

    # products (3)
    eps.append({"name": "products.list",         "method": "GET", "url": f"{base}/api/v1/products"})
    eps.append({"name": "products.voc",          "method": "GET", "url": f"{base}/api/v1/products/{q(product)}/voc?limit=20"})
    eps.append({"name": "products.stats",        "method": "GET", "url": f"{base}/api/v1/products/{q(product)}/stats"})

    # analytics (9)
    eps.append({"name": "analytics.sentiment",   "method": "GET", "url": f"{base}/api/v1/analytics/sentiment-trend?product={q(product)}&period_days=90&granularity=week"})
    eps.append({"name": "analytics.category",    "method": "GET", "url": f"{base}/api/v1/analytics/category-dist?product={q(product)}&period_days=30"})
    eps.append({"name": "analytics.country",     "method": "GET", "url": f"{base}/api/v1/analytics/country-heatmap?product={q(product)}&period_days=30"})
    eps.append({"name": "analytics.top_issues",  "method": "GET", "url": f"{base}/api/v1/analytics/top-issues?product={q(product)}&period_days=30&top_n=10"})
    eps.append({"name": "analytics.compare",     "method": "GET", "url": f"{base}/api/v1/analytics/compare?products={q(products_csv)}&period_days=30"})
    eps.append({"name": "analytics.keyword",     "method": "GET", "url": f"{base}/api/v1/analytics/keyword-track?keyword=camera&period_days=30&granularity=day"})
    eps.append({"name": "analytics.cohort",      "method": "GET", "url": f"{base}/api/v1/analytics/cohort-compare?products={q(products_csv)}&dimension=sentiment&period_days=30"})
    eps.append({"name": "analytics.site_health", "method": "GET", "url": f"{base}/api/v1/analytics/site-health"})
    eps.append({"name": "analytics.recent",      "method": "GET", "url": f"{base}/api/v1/analytics/recent-issues?product={q(product)}&top_n=10"})

    # dashboard (1)
    eps.append({"name": "dashboard.overview",    "method": "GET", "url": f"{base}/api/v1/dashboard/overview"})

    # kg (3)
    eps.append({"name": "kg.graph",              "method": "GET", "url": f"{base}/api/v1/kg/graph?days=14&limit_edges=120"})
    eps.append({"name": "kg.node_samples",       "method": "GET", "url": f"{base}/api/v1/kg/node/keyword:camera/samples?limit=5"})
    eps.append({"name": "kg.search",             "method": "GET", "url": f"{base}/api/v1/kg/search?q=camera&limit=10"})

    # temporal (3)
    # from_date / to_date 는 최근 60일 윈도우.
    from datetime import date, timedelta as _td
    to_date = date.today().isoformat()
    from_date = (date.today() - _td(days=60)).isoformat()
    keys = products_csv.split(",")
    eps.append({
        "name": "temporal.series",
        "method": "GET",
        "url": (
            f"{base}/api/v1/analytics/temporal-series"
            f"?product={q(product)}&from_date={from_date}&to_date={to_date}&bucket=day"
        ),
    })
    eps.append({
        "name": "temporal.compare",
        "method": "GET",
        "url": (
            f"{base}/api/v1/analytics/temporal-compare"
            f"?mode=products&keys={q(keys[0])}&keys={q(keys[1] if len(keys) > 1 else keys[0])}"
            f"&from_date={from_date}&to_date={to_date}&bucket=day"
        ),
    })
    # llm-narrative 는 ollama 호출 → 본 perf 측정에서는 skip (대기시간 큼).
    # 그러나 22 endpoint 사양상 포함. 빈 payload 422 라도 응답속도 자체는 측정.
    eps.append({
        "name": "temporal.llm_narrative",
        "method": "POST",
        "url": f"{base}/api/v1/analytics/llm-narrative",
        "payload": {
            "series_payload": {
                "product": product,
                "from_date": from_date,
                "to_date": to_date,
                "bucket": "day",
                "series": [],
            },
            "lang": "ko",
        },
    })

    # community (6)
    eps.append({"name": "community.health",      "method": "GET", "url": f"{base}/api/v1/community/platforms/health"})
    eps.append({"name": "community.matrix",      "method": "GET", "url": f"{base}/api/v1/community/platforms/product-matrix"})
    eps.append({"name": "community.dispersion",  "method": "GET", "url": f"{base}/api/v1/community/platforms/dispersion"})
    eps.append({"name": "community.early",       "method": "GET", "url": f"{base}/api/v1/community/platforms/early-signal"})
    eps.append({"name": "community.clusters",    "method": "GET", "url": f"{base}/api/v1/community/platforms/clusters?k=4"})
    eps.append({"name": "community.anomalies",   "method": "GET", "url": f"{base}/api/v1/community/platforms/anomalies"})

    # geo (4)
    eps.append({"name": "geo.choropleth",        "method": "GET", "url": f"{base}/api/v1/analytics/country/choropleth"})
    eps.append({"name": "geo.drilldown",         "method": "GET", "url": f"{base}/api/v1/analytics/country/US/drilldown"})
    eps.append({"name": "geo.diffusion",         "method": "GET", "url": f"{base}/api/v1/analytics/country/diffusion?granularity=day"})
    eps.append({"name": "geo.product_compare",   "method": "GET", "url": f"{base}/api/v1/analytics/country/product-compare?product_id=1&countries=US,KR,JP"})

    return eps


def run(
    base: str,
    runs: int,
    product: str,
    products_csv: str,
    out_dir: Path,
    timeout: float,
) -> Path:
    eps = _build_endpoints(base, product=product, products_csv=products_csv)
    print(f"[perf_check] base={base} runs={runs} product={product} endpoints={len(eps)}")

    results: List[Dict[str, Any]] = []
    for ep in eps:
        latencies: List[float] = []
        errors: Dict[int, int] = {}
        first_lat: Optional[float] = None
        second_lat: Optional[float] = None
        body_len = 0

        for i in range(runs):
            if ep["method"] == "POST":
                status, lat_ms, blen = _http_post_json(ep["url"], ep.get("payload") or {}, timeout=timeout)
            else:
                status, lat_ms, blen = _http_get(ep["url"], timeout=timeout)
            if status == 200:
                latencies.append(lat_ms)
                body_len = max(body_len, blen)
                if first_lat is None:
                    first_lat = lat_ms
                elif second_lat is None:
                    second_lat = lat_ms
            else:
                errors[status] = errors.get(status, 0) + 1

        ok = len(latencies)
        row = {
            "endpoint": ep["name"],
            "method": ep["method"],
            "ok": ok,
            "errors": errors,
            "p50": round(_pct(latencies, 50), 1),
            "p95": round(_pct(latencies, 95), 1),
            "p99": round(_pct(latencies, 99), 1),
            "min": round(min(latencies), 1) if latencies else 0.0,
            "max": round(max(latencies), 1) if latencies else 0.0,
            "mean": round(statistics.mean(latencies), 1) if latencies else 0.0,
            "first_ms": round(first_lat, 1) if first_lat is not None else None,
            "second_ms": round(second_lat, 1) if second_lat is not None else None,
            "body_max_kb": round(body_len / 1024.0, 1),
        }
        print(
            f"  {row['endpoint']:32s} ok={ok:3d}/{runs} "
            f"p50={row['p50']:7.1f}ms p95={row['p95']:7.1f}ms p99={row['p99']:7.1f}ms "
            f"first={row['first_ms']} second={row['second_ms']} err={row['errors']}"
        )
        results.append(row)

    # 보고서
    out_dir.mkdir(parents=True, exist_ok=True)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    fp = out_dir / f"perf_{today}.md"
    lines: List[str] = []
    lines.append(f"# Performance Check — {today}")
    lines.append("")
    lines.append(f"- base: `{base}`")
    lines.append(f"- runs per endpoint: **{runs}**")
    lines.append(f"- product: `{product}`, products: `{products_csv}`")
    lines.append(f"- endpoints: **{len(results)}**")
    lines.append("")
    lines.append("## Latency (ms)")
    lines.append("")
    lines.append("| Endpoint | OK | p50 | p95 | p99 | mean | first | second | errors |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---|")
    for r in results:
        err_str = ",".join(f"{k}:{v}" for k, v in r["errors"].items()) or "-"
        lines.append(
            f"| `{r['endpoint']}` | {r['ok']} | {r['p50']} | {r['p95']} | {r['p99']} "
            f"| {r['mean']} | {r['first_ms']} | {r['second_ms']} | {err_str} |"
        )
    lines.append("")

    # SLO 요약 — p95 ≤ 200ms 기준
    over = [r for r in results if r["ok"] > 0 and r["p95"] > 200.0]
    lines.append("## SLO Summary")
    lines.append("")
    lines.append(f"- p95 ≤ 200ms 충족: **{len(results) - len(over)} / {len(results)}**")
    if over:
        lines.append("- p95 초과 endpoint:")
        for r in over:
            lines.append(f"  - `{r['endpoint']}` p95={r['p95']}ms")
    lines.append("")

    # 캐시 효과 (first vs second)
    lines.append("## Cache Effect (first → second)")
    lines.append("")
    lines.append("| Endpoint | first(ms) | second(ms) | speedup |")
    lines.append("|---|---:|---:|---:|")
    for r in results:
        if r["first_ms"] is None or r["second_ms"] is None:
            continue
        f1, f2 = r["first_ms"], r["second_ms"]
        spd = round(f1 / f2, 2) if f2 > 0 else 0.0
        lines.append(f"| `{r['endpoint']}` | {f1} | {f2} | x{spd} |")
    lines.append("")

    fp.write_text("\n".join(lines), encoding="utf-8")
    print(f"[perf_check] report → {fp}")
    return fp


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="SignalForge 22 endpoint perf check")
    ap.add_argument("--base", default="http://127.0.0.1:8000")
    ap.add_argument("--runs", type=int, default=50)
    ap.add_argument("--product", default=None, help="제품 코드 (미지정 시 /products 첫 항목)")
    ap.add_argument("--products-csv", default=None, help="콤마 구분 (미지정 시 product,product)")
    ap.add_argument("--out", default="reports")
    ap.add_argument("--timeout", type=float, default=30.0)
    args = ap.parse_args(argv)

    base = args.base.rstrip("/")
    product = args.product or _discover_product(base, fallback="GS25U")
    products_csv = args.products_csv or f"{product},{product}"
    out_dir = Path(args.out)

    try:
        run(base=base, runs=args.runs, product=product, products_csv=products_csv,
            out_dir=out_dir, timeout=args.timeout)
        return 0
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
