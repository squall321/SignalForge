// SignalForge 공통 echarts 옵션 빌더.
//
// 목적
//  - 21 카드의 echarts 옵션 일관성 (색·툴팁·범례·grid·dataZoom·toolbox).
//  - 색맹 친화(Wong / Okabe-Ito) 팔레트로 빨↔녹 직접 대비를 회피.
//  - 한국어 단위 포매터 (건/% /pp / 점) 통일.
//  - 모바일/데스크탑 분기 처리는 buildBaseOption 의 mobile 옵션으로.
//
// 사용
//   const base = makeBaseOption({ mobile, withDataZoom: hours.length >= 30, withToolbox: true });
//   const opt = { ...base, xAxis: ..., yAxis: ..., series: ... };
//   // 또는: const opt = mergeOption(base, { series: ..., yAxis: ... });
//
// 주의
//  - 카드별 series/xAxis/yAxis 는 그대로 두고, base 가 제공하는 색·툴팁·grid 만 위임한다.
//  - 기존 옵션이 base 의 키를 덮어쓰려면 ...base 보다 뒤에 두면 된다.

import type { EChartsOption } from 'echarts';

// 색맹 친화 8색 팔레트 (Okabe-Ito 기반, 약간 가독성 보정).
// 시맨틱 키 → 의미.
//
//  - primary  : 카운트/주축 시리즈 (파랑)
//  - accent   : 보조 시리즈 (주황)
//  - positive : 긍정 (청록) — 녹색 대신 사용해 색맹에서 빨↔녹 충돌 회피
//  - negative : 부정 (자홍/주황)
//  - warning  : 경고 (노랑)
//  - info     : 정보 (하늘)
//  - neutral  : 회색
//  - secondary: 보라
export const palette = {
  primary: '#0072B2',
  accent: '#E69F00',
  positive: '#009E73',
  negative: '#D55E00',
  warning: '#F0E442',
  info: '#56B4E9',
  neutral: '#999999',
  secondary: '#CC79A7',
} as const;

// 시리즈 색상 회전 — 다중 시리즈가 있는 차트의 color[] 로 그대로 사용.
export const seriesColors: string[] = [
  palette.primary,
  palette.accent,
  palette.positive,
  palette.negative,
  palette.info,
  palette.secondary,
  palette.warning,
  palette.neutral,
];

// severity 매핑 — 카드/태그에서 일관 사용.
export const severityColor = (sev: 'critical' | 'warning' | 'info'): string => {
  if (sev === 'critical') return palette.negative;
  if (sev === 'warning') return palette.warning;
  return palette.info;
};

// 감성 매핑 (-1..1 → 색) — 양수 positive, 음수 negative, 0 부근 neutral.
export const sentimentColor = (score: number): string => {
  if (score >= 0.2) return palette.positive;
  if (score <= -0.2) return palette.negative;
  return palette.neutral;
};

// 한국어 단위 포매터 — 차트 tooltip / yAxis label 에서 일관 사용.
export const formatCount = (v: number): string =>
  `${Math.round(v).toLocaleString()}건`;

export const formatPct = (v: number, digits = 1): string =>
  `${v.toFixed(digits)}%`;

export const formatPp = (v: number, digits = 2): string => {
  const sign = v >= 0 ? '+' : '';
  return `${sign}${v.toFixed(digits)}pp`;
};

export const formatSent = (v: number, digits = 3): string =>
  `${v.toFixed(digits)}점`;

// 한국어 unit 사전 — 시리즈 이름별 자동 단위.
const KOREAN_UNIT_MAP: Record<string, (v: number) => string> = {
  count: formatCount,
  baseline: formatCount,
  value: formatCount,
  spike: formatCount,
  '건수': formatCount,
  neg_rate: (v) => formatPct(v, 1),
  'neg_rate(%)': (v) => formatPct(v, 1),
  share_pct: (v) => formatPct(v, 1),
  sent_avg: formatSent,
  sent: formatSent,
  score: (v) => v.toFixed(1),
};

// 시리즈 이름으로 한국어 단위를 적용한 기본 tooltip formatter.
//  - trigger:'axis' 일 때 다중 시리즈를 한 줄씩 렌더링.
//  - 시리즈 이름이 사전에 있으면 한국어 단위, 없으면 toLocaleString().
type TooltipParam = {
  axisValue?: string | number;
  seriesName?: string;
  value?: number | [string | number, number] | unknown;
  marker?: string;
};

export function defaultAxisTooltipFormatter(
  params: TooltipParam | TooltipParam[],
): string {
  const arr = Array.isArray(params) ? params : [params];
  if (!arr.length) return '';
  const header = arr[0].axisValue ?? '';
  const lines = arr.map((p) => {
    const raw = Array.isArray(p.value) ? p.value[1] : p.value;
    const num = typeof raw === 'number' ? raw : Number(raw ?? 0);
    const fmt = KOREAN_UNIT_MAP[p.seriesName ?? ''];
    const text = fmt
      ? fmt(num)
      : Number.isFinite(num)
        ? num.toLocaleString()
        : String(raw ?? '');
    return `${p.marker ?? ''} ${p.seriesName ?? ''}: <b>${text}</b>`;
  });
  return [`${header}`, ...lines].join('<br/>');
}

// makeBaseOption 옵션.
export interface BaseOptionInput {
  // viewport 모바일 분기 — grid 축소·dataZoom 강제·legend hide.
  mobile?: boolean;
  // 시계열에서 데이터 포인트가 많을 때 dataZoom 자동 표시.
  withDataZoom?: boolean | { dataPoints?: number; threshold?: number };
  // toolbox.saveAsImage (PNG export) 활성화 — 기본 false (카드별 opt-in).
  withToolbox?: boolean;
  // tooltip 한국어 포매터 적용 (trigger:'axis' 전제).
  // 'auto' (기본): trigger:'axis' 일 때만, 'force' / 'off' 가능.
  tooltipMode?: 'auto' | 'force' | 'off';
}

// 기본 옵션 빌더 — 카드 옵션에 ...spread 로 적용.
//
// 반환 옵션
//  - color           : 색맹 친화 팔레트
//  - tooltip         : trigger:'axis' + 한국어 포매터
//  - legend          : bottom 정렬, scroll, fontSize 11
//  - grid            : { top: 20, right: 20, bottom: 40, left: 50 }, mobile 분기 축소
//  - dataZoom        : withDataZoom 또는 자동 임계치 초과 시 inside+slider
//  - toolbox         : saveAsImage(pixelRatio 2)
export function makeBaseOption(input: BaseOptionInput = {}): EChartsOption {
  const mobile = !!input.mobile;
  const tooltipMode = input.tooltipMode ?? 'auto';

  const grid = mobile
    ? { top: 24, right: 16, bottom: 36, left: 40, containLabel: true }
    : { top: 20, right: 20, bottom: 40, left: 50, containLabel: true };

  const legend = mobile
    ? { show: false as const }
    : {
        bottom: 0,
        type: 'scroll' as const,
        textStyle: { fontSize: 11 },
      };

  // echarts TooltipOption.formatter 의 callback 타입은 TopLevelFormatterParams 인데
  // 실 사용 필드는 우리 TooltipParam 과 동등. unknown 경유 캐스팅으로 타입 좁힘 회피.
  const tooltip =
    tooltipMode === 'off'
      ? undefined
      : ({
          trigger: 'axis',
          axisPointer: { type: 'cross' },
          formatter: defaultAxisTooltipFormatter,
          textStyle: { fontSize: 12 },
        } as unknown as EChartsOption['tooltip']);

  // dataZoom 자동 표시 임계치 — 옵션 객체일 때 dataPoints 와 threshold 비교.
  let withZoom = false;
  if (typeof input.withDataZoom === 'boolean') {
    withZoom = input.withDataZoom;
  } else if (input.withDataZoom) {
    const pts = input.withDataZoom.dataPoints ?? 0;
    const th = input.withDataZoom.threshold ?? 30;
    withZoom = pts >= th;
  }

  const dataZoom = withZoom
    ? [
        { type: 'inside' as const },
        { type: 'slider' as const, height: 16, bottom: mobile ? 4 : 8 },
      ]
    : undefined;

  const toolbox = input.withToolbox
    ? {
        right: 8,
        top: 0,
        feature: {
          saveAsImage: {
            pixelRatio: 2,
            title: 'PNG 저장',
            name: 'signalforge-chart',
          },
        },
      }
    : undefined;

  const opt: EChartsOption = {
    color: seriesColors,
    grid,
    legend,
  };
  if (tooltip) opt.tooltip = tooltip;
  if (dataZoom) opt.dataZoom = dataZoom;
  if (toolbox) opt.toolbox = toolbox;

  // axis 기본값 — 카드가 xAxis/yAxis 를 지정할 때 fontSize 만 위임할 수 있도록 별도 helper.
  return opt;
}

// 카드에서 그대로 axisLabel 에 spread 하기 좋은 helper.
//   xAxis: { type: 'category', data: x, axisLabel: { ...axisLabelStyle(mobile) } }
export const axisLabelStyle = (mobile = false) => ({
  fontSize: mobile ? 10 : 11,
});

// 얕은 merge — base 우선 적용 후 override 가 같은 key 를 덮어쓴다.
// (echarts series/xAxis/yAxis 는 배열/객체 모두 가능해 deep merge 는 피한다.)
export function mergeOption(
  base: EChartsOption,
  override: EChartsOption,
): EChartsOption {
  return { ...base, ...override };
}
