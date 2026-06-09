import { describe, it, expect } from 'vitest';
import {
  makeBaseOption,
  palette,
  seriesColors,
  defaultAxisTooltipFormatter,
  formatCount,
  formatPct,
  formatPp,
  formatSent,
  sentimentColor,
  severityColor,
} from '../utils/chartTheme';

// 트랙 C — chartTheme 공통 옵션 빌더 단위 테스트.
//
// 검증 포인트
//  1) 색맹 친화 팔레트가 옵션의 color[] 로 노출되는지 (8색 확인).
//  2) 한국어 포매터가 시리즈 이름에 맞춰 단위를 붙이는지.
//  3) dataZoom 자동 표시(임계치 초과 시) 및 mobile 분기 grid 축소.

describe('palette / seriesColors — 색맹 친화 팔레트', () => {
  it('8색 시리즈 컬러 노출', () => {
    expect(seriesColors).toHaveLength(8);
  });

  it('palette.positive 는 녹색이 아닌 청록 #009E73 (색맹에서 빨↔녹 충돌 회피)', () => {
    // 일반 antd 녹색 #52c41a 가 아니어야 한다.
    expect(palette.positive).not.toBe('#52c41a');
    expect(palette.positive.toUpperCase()).toBe('#009E73');
  });

  it('palette.negative 는 주황 계열 #D55E00 (선명한 빨강 대신)', () => {
    expect(palette.negative.toUpperCase()).toBe('#D55E00');
  });

  it('severityColor 매핑 — critical→negative, warning→warning, info→info', () => {
    expect(severityColor('critical')).toBe(palette.negative);
    expect(severityColor('warning')).toBe(palette.warning);
    expect(severityColor('info')).toBe(palette.info);
  });

  it('sentimentColor 매핑 — +0.5 / -0.5 / 0', () => {
    expect(sentimentColor(0.5)).toBe(palette.positive);
    expect(sentimentColor(-0.5)).toBe(palette.negative);
    expect(sentimentColor(0)).toBe(palette.neutral);
  });
});

describe('한국어 단위 포매터', () => {
  it('formatCount → 천단위 콤마 + "건"', () => {
    expect(formatCount(12345)).toBe('12,345건');
  });

  it('formatPct → 소수 1자리 + "%"', () => {
    expect(formatPct(12.345)).toBe('12.3%');
  });

  it('formatPp → 부호 + 소수 2자리 + "pp"', () => {
    expect(formatPp(0.123)).toBe('+0.12pp');
    expect(formatPp(-0.456)).toBe('-0.46pp');
  });

  it('formatSent → 소수 3자리 + "점"', () => {
    expect(formatSent(0.412)).toBe('0.412점');
  });

  it('defaultAxisTooltipFormatter — count 시리즈 한국어 단위 "건" 포함', () => {
    const html = defaultAxisTooltipFormatter([
      {
        axisValue: '12시',
        seriesName: 'count',
        value: 1234,
        marker: '<span></span>',
      },
    ]);
    expect(html).toContain('12시');
    expect(html).toContain('1,234건');
  });

  it('defaultAxisTooltipFormatter — neg_rate 시리즈는 "%"', () => {
    const html = defaultAxisTooltipFormatter([
      {
        axisValue: 'Mon',
        seriesName: 'neg_rate',
        value: 12.345,
        marker: '',
      },
    ]);
    expect(html).toContain('12.3%');
  });
});

describe('makeBaseOption', () => {
  it('데스크탑 기본 — color 팔레트 적용 + grid 표준 + legend bottom', () => {
    const base = makeBaseOption();
    expect(base.color).toEqual(seriesColors);
    const grid = base.grid as { top?: number; bottom?: number; left?: number; right?: number };
    expect(grid.top).toBe(20);
    expect(grid.right).toBe(20);
    expect(grid.bottom).toBe(40);
    expect(grid.left).toBe(50);
    const legend = base.legend as { bottom?: number; show?: boolean };
    expect(legend.bottom).toBe(0);
    expect(legend.show).not.toBe(false);
    expect(base.dataZoom).toBeUndefined();
    expect(base.toolbox).toBeUndefined();
  });

  it('모바일 분기 — grid 축소 + legend hide', () => {
    const base = makeBaseOption({ mobile: true });
    const grid = base.grid as { top?: number; bottom?: number };
    expect(grid.top).toBeLessThan(30);
    expect(grid.bottom).toBeLessThanOrEqual(36);
    const legend = base.legend as { show?: boolean };
    expect(legend.show).toBe(false);
  });

  it('withDataZoom auto — dataPoints≥threshold 일 때만 dataZoom 자동 표시', () => {
    // 30 미만 → 비활성
    const baseOff = makeBaseOption({ withDataZoom: { dataPoints: 14, threshold: 30 } });
    expect(baseOff.dataZoom).toBeUndefined();
    // 임계치 초과 → 활성 + inside+slider 2종
    const baseOn = makeBaseOption({ withDataZoom: { dataPoints: 60, threshold: 30 } });
    expect(Array.isArray(baseOn.dataZoom)).toBe(true);
    const dz = baseOn.dataZoom as Array<{ type: string }>;
    expect(dz.map((d) => d.type)).toContain('inside');
    expect(dz.map((d) => d.type)).toContain('slider');
  });

  it('withToolbox — saveAsImage 활성화 + pixelRatio 2', () => {
    const base = makeBaseOption({ withToolbox: true });
    const tb = base.toolbox as {
      feature?: { saveAsImage?: { pixelRatio?: number } };
    };
    expect(tb?.feature?.saveAsImage?.pixelRatio).toBe(2);
  });

  it('tooltipMode off — tooltip 미설정', () => {
    const base = makeBaseOption({ tooltipMode: 'off' });
    expect(base.tooltip).toBeUndefined();
  });
});
