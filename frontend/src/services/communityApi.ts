// T3 커뮤니티 비교 (Platforms) API 클라이언트
// 백엔드 일부 엔드포인트는 P3-2 시점에 미구현일 수 있어 fallback 더미를 제공한다.
// (geoApi.ts 의 패턴을 그대로 따른다.)
import dayjs from 'dayjs';
import api from './api';
import type {
  AnomalyResponse,
  ClusterResponse,
  DispersionResponse,
  EarlySignalResponse,
  PlatformHealthResponse,
  ProductMatrixResponse,
} from '../types/community';

// ─────────────────────────────── fallback seeds ───────────────────────────────
const SEED_PLATFORMS = [
  'reddit', 'gsmarena', 'xda', 'youtube', 'twitter',
  'amazon', 'tiktok', 'bestbuy', 'naver_blog', 'dcinside',
  'theverge', 'androidpolice',
];

const SEED_PRODUCTS = ['GS25U', 'GS25', 'GZF6', 'GZF7', 'GTabS10'];

function rand(seed: number) {
  // 결정론적 의사난수 (xorshift 단순화)
  let x = seed | 0 || 1;
  return () => {
    x ^= x << 13;
    x ^= x >>> 17;
    x ^= x << 5;
    return ((x >>> 0) % 10000) / 10000;
  };
}

function fallbackHealth(): PlatformHealthResponse {
  const r = rand(101);
  return {
    generated_at: dayjs().toISOString(),
    platforms: SEED_PLATFORMS.map((code, i) => {
      const posts_7d = Math.floor(80 + r() * 1800);
      const posts_24h = Math.floor(posts_7d * (0.05 + r() * 0.18));
      const sent = +((r() - 0.5) * 0.9).toFixed(2);
      const status = posts_7d === 0 ? 'dead' : posts_24h === 0 ? 'idle' : 'active';
      return {
        platform_id: i + 1,
        code,
        region: ['US', 'KR', 'JP', 'GLOBAL'][i % 4],
        base_url: `https://${code}.example.com`,
        posts_24h,
        posts_7d,
        sent_avg_7d: sent,
        avg_body_len_7d: Math.floor(120 + r() * 600),
        last_collected: dayjs().subtract(Math.floor(r() * 30), 'hour').toISOString(),
        status,
      };
    }),
  };
}

function fallbackProductMatrix(): ProductMatrixResponse {
  const r = rand(202);
  const cells = [];
  for (const p of SEED_PLATFORMS) {
    for (const prod of SEED_PRODUCTS) {
      cells.push({
        platform_code: p,
        product_code: prod,
        count: Math.floor(r() * 420),
        sent_avg: +((r() - 0.5) * 0.8).toFixed(2),
      });
    }
  }
  return { cells, platforms: SEED_PLATFORMS, products: SEED_PRODUCTS };
}

function fallbackDispersion(): DispersionResponse {
  const r = rand(303);
  return {
    entries: SEED_PLATFORMS.map((code) => {
      const median = +((r() - 0.5) * 0.6).toFixed(2);
      const spread = 0.15 + r() * 0.25;
      const min = +(median - spread - r() * 0.2).toFixed(2);
      const max = +(median + spread + r() * 0.2).toFixed(2);
      const q1 = +(median - spread * 0.5).toFixed(2);
      const q3 = +(median + spread * 0.5).toFixed(2);
      return {
        platform_code: code,
        min,
        q1,
        median,
        q3,
        max,
        outliers: r() < 0.5 ? [+(max + 0.1).toFixed(2), +(min - 0.1).toFixed(2)] : [],
        n: Math.floor(80 + r() * 600),
      };
    }),
  };
}

function fallbackEarlySignal(signal: string): EarlySignalResponse {
  const r = rand(404 + signal.length);
  const base = dayjs().subtract(48, 'hour');
  const ordered = [...SEED_PLATFORMS].sort(() => r() - 0.5);
  const rows = ordered.map((code, i) => {
    const lag = +(i * (0.5 + r() * 2.5)).toFixed(1);
    return {
      platform_code: code,
      signal,
      first_seen: base.add(lag, 'hour').toISOString(),
      lag_hours: lag,
      count_24h: Math.floor(20 + r() * 380),
    };
  });
  return { signal, rows };
}

function fallbackClusters(): ClusterResponse {
  const r = rand(505);
  const k = 4;
  return {
    k,
    points: SEED_PLATFORMS.map((code, i) => {
      const cluster = i % k;
      // 클러스터 중심 주위에 산포
      const cx = [-1, 1, 1, -1][cluster];
      const cy = [-1, -1, 1, 1][cluster];
      return {
        platform_code: code,
        x: +(cx + (r() - 0.5) * 0.6).toFixed(3),
        y: +(cy + (r() - 0.5) * 0.6).toFixed(3),
        cluster,
        posts_7d: Math.floor(100 + r() * 1500),
        sent_avg_7d: +((r() - 0.5) * 0.7).toFixed(2),
      };
    }),
  };
}

function fallbackAnomalies(): AnomalyResponse {
  const r = rand(606);
  const kinds: Array<'volume_spike' | 'volume_drop' | 'sent_swing' | 'silence'> = [
    'volume_spike', 'volume_drop', 'sent_swing', 'silence',
  ];
  const items = Array.from({ length: 8 }, (_, i) => {
    const code = SEED_PLATFORMS[i % SEED_PLATFORMS.length];
    const kind = kinds[i % kinds.length];
    const score = +(0.6 + r() * 0.4).toFixed(2);
    return {
      platform_code: code,
      kind,
      score,
      detected_at: dayjs().subtract(Math.floor(r() * 24), 'hour').toISOString(),
      description:
        kind === 'volume_spike' ? '24h 게시량이 베이스라인 대비 +3.2σ' :
        kind === 'volume_drop'  ? '24h 게시량이 베이스라인 대비 -2.7σ' :
        kind === 'sent_swing'   ? '평균 감성이 12h 이내 0.6 이상 급변' :
                                  '6h 이상 신규 게시 없음',
    };
  });
  return { items };
}

// ─────────────────────────────── public API ───────────────────────────────
export async function fetchPlatformHealth(): Promise<PlatformHealthResponse> {
  try {
    const { data } = await api.get<PlatformHealthResponse>('/platforms/health');
    if (!data?.platforms?.length) return fallbackHealth();
    return data;
  } catch {
    return fallbackHealth();
  }
}

export async function fetchProductMatrix(): Promise<ProductMatrixResponse> {
  try {
    const { data } = await api.get<ProductMatrixResponse>('/platforms/product-matrix');
    if (!data?.cells?.length) return fallbackProductMatrix();
    return data;
  } catch {
    return fallbackProductMatrix();
  }
}

export async function fetchDispersion(): Promise<DispersionResponse> {
  try {
    const { data } = await api.get<DispersionResponse>('/platforms/dispersion');
    if (!data?.entries?.length) return fallbackDispersion();
    return data;
  } catch {
    return fallbackDispersion();
  }
}

export async function fetchEarlySignal(signal: string): Promise<EarlySignalResponse> {
  try {
    const { data } = await api.get<EarlySignalResponse>('/platforms/early-signal', {
      params: { signal },
    });
    if (!data?.rows?.length) return fallbackEarlySignal(signal);
    return data;
  } catch {
    return fallbackEarlySignal(signal);
  }
}

export async function fetchClusters(): Promise<ClusterResponse> {
  try {
    const { data } = await api.get<ClusterResponse>('/platforms/clusters');
    if (!data?.points?.length) return fallbackClusters();
    return data;
  } catch {
    return fallbackClusters();
  }
}

export async function fetchAnomalies(): Promise<AnomalyResponse> {
  try {
    const { data } = await api.get<AnomalyResponse>('/platforms/anomalies');
    if (!data?.items?.length) return fallbackAnomalies();
    return data;
  } catch {
    return fallbackAnomalies();
  }
}
