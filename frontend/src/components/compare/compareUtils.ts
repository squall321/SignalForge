// P5 R7 — Compare 페이지 순수 변환 유틸.
// (1) KPI 행 빌더 (2) 시계열 line series (3) 카테고리 stacked bar series (4) top neg 표.
//
// 모두 순수 함수 — vitest 에서 직접 검증.

import type { CompareData } from '../../services/compareApi';

export interface CompareKpiRow {
  product: string;
  voc24h: number; // 마지막 trend 점의 count
  sent7d: number | null; // -1..1
  negRate: number; // 0..100
  activeSites: number; // overview top_sites 길이 (≤5)
}

export function buildKpiRows(data: CompareData): CompareKpiRow[] {
  return data.productCodes.map((code) => {
    const ov = data.overviews[code];
    const sent = data.sentiment7d.find((s) => s.product_code === code) ?? null;
    // 24h: trend14d 마지막 포인트 count
    const trendArr = ov?.trend14d ?? [];
    const voc24h = trendArr.length ? trendArr[trendArr.length - 1].count : 0;
    const activeSites = ov?.top_sites?.length ?? 0;
    return {
      product: code,
      voc24h,
      sent7d: sent ? sent.avg_score : null,
      // backend 가 이미 % 단위 (예: 10.7) 로 반환하므로 그대로 사용
      negRate: sent ? sent.negative_rate : 0,
      activeSites,
    };
  });
}

// 시계열 line — echarts series 형태로 변환.
// 반환: { xAxis: string[] (공통 날짜), series: { name, data }[] }
export interface CompareTrendChart {
  xAxis: string[];
  series: Array<{ name: string; data: Array<number | null> }>;
}

export function buildTrendChart(data: CompareData): CompareTrendChart {
  // 공통 x 축 — 모든 제품의 date union, sort.
  const dateSet = new Set<string>();
  for (const code of data.productCodes) {
    const tr = data.trends[code];
    tr?.data?.forEach((p) => dateSet.add(p.date));
  }
  const xAxis = Array.from(dateSet).sort();
  const series = data.productCodes.map((code) => {
    const tr = data.trends[code];
    const map = new Map<string, number>();
    tr?.data?.forEach((p) => {
      map.set(p.date, p.positive + p.negative + p.neutral);
    });
    return {
      name: code,
      data: xAxis.map((d) => (map.has(d) ? map.get(d)! : null)),
    };
  });
  return { xAxis, series };
}

// 카테고리 stacked bar — x=제품, stack=카테고리.
// 반환: { categories: string[] (모든 카테고리 union, top 8 by total), series: { name, data }[] }
export interface CompareCategoryChart {
  categories: string[];
  series: Array<{ name: string; data: number[] }>;
}

export function buildCategoryChart(data: CompareData): CompareCategoryChart {
  // 카테고리별 전체 합계로 상위 8개만 노출 (가독성).
  const totals = new Map<string, number>();
  for (const cm of data.categories) {
    for (const c of cm.categories) {
      totals.set(c.category, (totals.get(c.category) ?? 0) + c.count);
    }
  }
  const categories = Array.from(totals.entries())
    .sort((a, b) => b[1] - a[1])
    .slice(0, 8)
    .map(([k]) => k);

  const series = categories.map((cat) => ({
    name: cat,
    data: data.productCodes.map((code) => {
      const cm = data.categories.find((x) => x.product_code === code);
      const item = cm?.categories.find((c) => c.category === cat);
      return item?.count ?? 0;
    }),
  }));
  return { categories, series };
}

// 부정 키워드 표 — 행=rank(1..5), 열=제품.
// 백엔드 top-issues 의 issues[].name_ko 또는 category 를 보여주고 hover 로 count 와 부정률.
export interface CompareIssueRow {
  rank: number;
  cells: Record<string, { label: string; count: number; negRate: number } | null>;
}

export function buildIssueTable(data: CompareData): CompareIssueRow[] {
  const rows: CompareIssueRow[] = [];
  for (let i = 0; i < 5; i++) {
    const cells: CompareIssueRow['cells'] = {};
    for (const code of data.productCodes) {
      const top = data.topIssues[code];
      const it = top?.issues?.[i];
      cells[code] = it
        ? {
            label: it.name_ko ?? it.category,
            count: it.count,
            // backend negative_rate 는 이미 % 단위 (예: 9.4)
            negRate: it.negative_rate,
          }
        : null;
    }
    rows.push({ rank: i + 1, cells });
  }
  return rows;
}
