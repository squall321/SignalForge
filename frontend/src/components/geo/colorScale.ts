// 색상 스케일 헬퍼 — d3-scale 기반 (테스트 가능하도록 분리)
import { scaleSequential, scaleDiverging } from 'd3-scale';
import type { CountryMetric, ChoroplethMode } from '../../types/geo';

const MISSING = '#eef0f4';

// HSL 보간 대신 단순 RGB 보간으로 의존성 최소화
function lerpHex(a: string, b: string, t: number): string {
  const ar = parseInt(a.slice(1, 3), 16);
  const ag = parseInt(a.slice(3, 5), 16);
  const ab = parseInt(a.slice(5, 7), 16);
  const br = parseInt(b.slice(1, 3), 16);
  const bg = parseInt(b.slice(3, 5), 16);
  const bb = parseInt(b.slice(5, 7), 16);
  const r = Math.round(ar + (br - ar) * t);
  const g = Math.round(ag + (bg - ag) * t);
  const bl = Math.round(ab + (bb - ab) * t);
  return `#${[r, g, bl].map((x) => x.toString(16).padStart(2, '0')).join('')}`;
}

// count: 단일 톤 (white -> blue)
function interpBlues(t: number): string {
  return lerpHex('#e6f0ff', '#0b4dad', Math.max(0, Math.min(1, t)));
}

// sent_z: 발산 (red -> white -> green) — 양수=긍정
function interpRdYlGn(t: number): string {
  if (t < 0.5) return lerpHex('#d73027', '#ffffbf', t * 2);
  return lerpHex('#ffffbf', '#1a9850', (t - 0.5) * 2);
}

export interface ColorScale {
  color: (v: number | undefined) => string;
  domain: [number, number];
  mode: ChoroplethMode;
  missing: string;
}

export function buildColorScale(
  countries: CountryMetric[],
  mode: ChoroplethMode,
): ColorScale {
  const values = countries
    .map((c) => (mode === 'count' ? c.count : c.sent_z ?? 0))
    .filter((v) => Number.isFinite(v));

  if (mode === 'count') {
    const max = values.length ? Math.max(...values) : 1;
    const scale = scaleSequential<string>().domain([0, max || 1]).interpolator(interpBlues);
    return {
      color: (v) => (v == null || !Number.isFinite(v) ? MISSING : scale(v)),
      domain: [0, max || 1],
      mode,
      missing: MISSING,
    };
  }
  // sent_z: -2 ~ +2 클램프
  const absMax = Math.min(2, Math.max(1, values.length ? Math.max(...values.map((v) => Math.abs(v))) : 1));
  const scale = scaleDiverging<string>().domain([-absMax, 0, absMax]).interpolator(interpRdYlGn);
  return {
    color: (v) => (v == null || !Number.isFinite(v) ? MISSING : scale(v)),
    domain: [-absMax, absMax],
    mode,
    missing: MISSING,
  };
}

// 국가코드 → 메트릭 인덱스 (지도 컴포넌트 사용)
export function indexByCountry(
  countries: CountryMetric[],
): Record<string, CountryMetric> {
  const out: Record<string, CountryMetric> = {};
  countries.forEach((c) => {
    out[c.country_code] = c;
  });
  return out;
}
