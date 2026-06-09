// 국가 분석 (T4) API 클라이언트
// 백엔드 일부 엔드포인트는 P3-5 시점에 미구현 상태일 수 있어 fallback 더미를 제공한다.
import api from './api';
import type {
  ChoroplethMode,
  CountryDrilldownResponse,
  CountryHeatmapResponse,
  CountryMetric,
  DiffusionResponse,
  ProductCompareResponse,
} from '../types/geo';
import dayjs from 'dayjs';

// ---------- helpers ----------
function dateRangeParams(filters: { start?: string; end?: string }) {
  return {
    start: filters.start || undefined,
    end: filters.end || undefined,
  };
}

// ---------- fallback (백엔드 미구현 시 UI 검증용) ----------
const SEED_COUNTRIES: Array<[string, string]> = [
  ['USA', 'United States'],
  ['KOR', 'Korea'],
  ['JPN', 'Japan'],
  ['CHN', 'China'],
  ['IND', 'India'],
  ['DEU', 'Germany'],
  ['GBR', 'United Kingdom'],
  ['FRA', 'France'],
  ['BRA', 'Brazil'],
  ['IDN', 'Indonesia'],
  ['VNM', 'Vietnam'],
  ['MEX', 'Mexico'],
  ['AUS', 'Australia'],
  ['CAN', 'Canada'],
  ['ESP', 'Spain'],
  ['ITA', 'Italy'],
  ['TUR', 'Turkey'],
  ['RUS', 'Russia'],
  ['ZAF', 'South Africa'],
  ['EGY', 'Egypt'],
];

function fallbackHeatmap(): CountryHeatmapResponse {
  // 단순 결정론적 의사난수 — 매번 같은 결과가 나오도록 seeded
  const countries: CountryMetric[] = SEED_COUNTRIES.map(([code, name], idx) => {
    const count = 200 + ((idx * 47) % 800);
    const sent = +(((idx * 13) % 17) / 17 - 0.5).toFixed(2);
    const sent_z = +(((idx * 7) % 11) / 5.5 - 1).toFixed(2);
    return { country_code: code, country_name: name, count, sent_avg: sent, sent_z };
  });
  return {
    countries,
    start: dayjs().subtract(30, 'day').format('YYYY-MM-DD'),
    end: dayjs().format('YYYY-MM-DD'),
  };
}

function fallbackDrilldown(code: string): CountryDrilldownResponse {
  const name = SEED_COUNTRIES.find(([c]) => c === code)?.[1] || code;
  return {
    country_code: code,
    country_name: name,
    total_count: 1234,
    sent_avg: 0.12,
    top_sites: [
      { site_code: 'amazon', site_name: 'Amazon', count: 420, sent_avg: 0.21 },
      { site_code: 'reddit', site_name: 'Reddit', count: 310, sent_avg: -0.08 },
      { site_code: 'gsmarena', site_name: 'GSMArena', count: 180, sent_avg: 0.35 },
      { site_code: 'youtube', site_name: 'YouTube', count: 160, sent_avg: 0.04 },
    ],
    top_products: [
      { product_code: 'GS25U', product_name: 'Galaxy S25 Ultra', count: 580, sent_avg: 0.28 },
      { product_code: 'GS25', product_name: 'Galaxy S25', count: 410, sent_avg: 0.18 },
      { product_code: 'GZF6', product_name: 'Galaxy Z Fold 6', count: 244, sent_avg: -0.05 },
    ],
    top_categories: [
      { category: '카메라', count: 380, sent_avg: 0.31 },
      { category: '배터리', count: 290, sent_avg: -0.12 },
      { category: '디스플레이', count: 230, sent_avg: 0.18 },
      { category: '가격', count: 180, sent_avg: -0.22 },
    ],
  };
}

function fallbackDiffusion(metric: 'count' | 'sent_z'): DiffusionResponse {
  const frames = Array.from({ length: 30 }, (_, i) => {
    const date = dayjs().subtract(29 - i, 'day').format('YYYY-MM-DD');
    const values: Record<string, number> = {};
    SEED_COUNTRIES.forEach(([code], idx) => {
      // 시간에 따라 점차 확산되는 패턴 (idx 늦은 국가일수록 늦게 상승)
      const onset = idx * 1.2;
      const x = Math.max(0, i - onset);
      const base = metric === 'count' ? 50 : -0.5;
      const peak = metric === 'count' ? 600 : 1.5;
      const v = base + (peak - base) * (1 - Math.exp(-x / 6));
      values[code] = +v.toFixed(2);
    });
    return { date, values };
  });
  return { metric, frames };
}

function fallbackProductCompare(productCode: string): ProductCompareResponse {
  const items = SEED_COUNTRIES.slice(0, 10).map(([code, name], idx) => {
    const sent = +(((idx * 19) % 23) / 23 - 0.4).toFixed(2);
    const ci = 0.08 + ((idx * 3) % 5) / 40;
    return {
      country_code: code,
      country_name: name,
      product_code: productCode,
      sent_avg: sent,
      sent_ci_low: +(sent - ci).toFixed(2),
      sent_ci_high: +(sent + ci).toFixed(2),
      count: 80 + ((idx * 23) % 220),
    };
  });
  return { product_code: productCode, items };
}

// ---------- public API ----------
export async function fetchCountryHeatmap(filters: {
  start?: string;
  end?: string;
  products?: string[];
}): Promise<CountryHeatmapResponse> {
  try {
    const { data } = await api.get<CountryHeatmapResponse>('/analytics/country-heatmap', {
      params: {
        ...dateRangeParams(filters),
        products: filters.products?.length ? filters.products.join(',') : undefined,
      },
    });
    if (!data?.countries || data.countries.length === 0) return fallbackHeatmap();
    return data;
  } catch {
    return fallbackHeatmap();
  }
}

export async function fetchCountryDrilldown(
  code: string,
  filters: { start?: string; end?: string; products?: string[] },
): Promise<CountryDrilldownResponse> {
  try {
    const { data } = await api.get<CountryDrilldownResponse>(
      `/analytics/country/${code}/drilldown`,
      {
        params: {
          ...dateRangeParams(filters),
          products: filters.products?.length ? filters.products.join(',') : undefined,
        },
      },
    );
    return data;
  } catch {
    return fallbackDrilldown(code);
  }
}

export async function fetchDiffusion(
  metric: ChoroplethMode,
  filters: { start?: string; end?: string; products?: string[] },
): Promise<DiffusionResponse> {
  try {
    const { data } = await api.get<DiffusionResponse>('/analytics/country-diffusion', {
      params: {
        metric,
        ...dateRangeParams(filters),
        products: filters.products?.length ? filters.products.join(',') : undefined,
      },
    });
    if (!data?.frames || data.frames.length === 0) return fallbackDiffusion(metric);
    return data;
  } catch {
    return fallbackDiffusion(metric);
  }
}

export async function fetchProductCompare(
  productCode: string,
  filters: { start?: string; end?: string },
): Promise<ProductCompareResponse> {
  try {
    const { data } = await api.get<ProductCompareResponse>(
      `/analytics/product/${productCode}/country-compare`,
      { params: dateRangeParams(filters) },
    );
    if (!data?.items || data.items.length === 0) return fallbackProductCompare(productCode);
    return data;
  } catch {
    return fallbackProductCompare(productCode);
  }
}
