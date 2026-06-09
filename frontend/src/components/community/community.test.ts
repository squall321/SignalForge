import { describe, it, expect } from 'vitest';
import { buildMatrixOption } from './PlatformMatrix';
import { buildBoxplotOption } from './DispersionBoxplot';
import { buildClusterOption } from './ClusterScatter';
import type {
  ClusterResponse,
  DispersionResponse,
  ProductMatrixResponse,
} from '../../types/community';

describe('PlatformMatrix.buildMatrixOption', () => {
  const matrix: ProductMatrixResponse = {
    platforms: ['reddit', 'amazon'],
    products: ['GS25U', 'GS25'],
    cells: [
      { platform_code: 'reddit', product_code: 'GS25U', count: 100, sent_avg: 0.3 },
      { platform_code: 'reddit', product_code: 'GS25', count: 50, sent_avg: -0.1 },
      { platform_code: 'amazon', product_code: 'GS25U', count: 80, sent_avg: 0.2 },
      // amazon × GS25 셀 누락 — 0 으로 보정되어야 함
    ],
  };

  it('count 모드는 0~vmax 도메인 + sequential 팔레트', () => {
    const opt = buildMatrixOption(matrix, 'count');
    const vm: any = opt.visualMap;
    expect(vm.min).toBe(0);
    expect(vm.max).toBe(100);
    expect((opt.series as any[])[0].type).toBe('heatmap');
    // 2×2 = 4 셀, 누락 셀 포함
    expect((opt.series as any[])[0].data.length).toBe(4);
  });

  it('sent_avg 모드는 -absMax ~ +absMax diverging', () => {
    const opt = buildMatrixOption(matrix, 'sent_avg');
    const vm: any = opt.visualMap;
    expect(vm.min).toBeLessThan(0);
    expect(vm.max).toBeGreaterThan(0);
    expect(vm.min).toBe(-vm.max);
  });

  it('xAxis 카테고리는 platforms 순서를 따른다', () => {
    const opt = buildMatrixOption(matrix, 'count');
    expect((opt.xAxis as any).data).toEqual(['reddit', 'amazon']);
    expect((opt.yAxis as any).data).toEqual(['GS25U', 'GS25']);
  });
});

describe('DispersionBoxplot.buildBoxplotOption', () => {
  const resp: DispersionResponse = {
    entries: [
      {
        platform_code: 'narrow',
        min: -0.1, q1: -0.05, median: 0.0, q3: 0.05, max: 0.1,
        outliers: [0.5],
        n: 100,
      },
      {
        platform_code: 'wide',
        min: -0.8, q1: -0.4, median: 0.0, q3: 0.4, max: 0.8,
        outliers: [],
        n: 200,
      },
    ],
  };

  it('분산 폭이 큰 항목이 먼저 오도록 정렬한다', () => {
    const opt = buildBoxplotOption(resp);
    const xs = (opt.xAxis as any).data as string[];
    expect(xs[0]).toBe('wide');
    expect(xs[1]).toBe('narrow');
  });

  it('boxplot + scatter 두 series 를 생성한다', () => {
    const opt = buildBoxplotOption(resp);
    const series = opt.series as any[];
    expect(series).toHaveLength(2);
    expect(series[0].type).toBe('boxplot');
    expect(series[1].type).toBe('scatter');
    // outliers 1개 → scatter data 1개
    expect(series[1].data).toHaveLength(1);
  });
});

describe('ClusterScatter.buildClusterOption', () => {
  const resp: ClusterResponse = {
    k: 2,
    points: [
      { platform_code: 'A', x: 1, y: 1, cluster: 0, posts_7d: 100, sent_avg_7d: 0.2 },
      { platform_code: 'B', x: 1.2, y: 0.9, cluster: 0, posts_7d: 200, sent_avg_7d: 0.1 },
      { platform_code: 'C', x: -1, y: -1, cluster: 1, posts_7d: 50, sent_avg_7d: -0.3 },
    ],
  };

  it('클러스터별로 series 를 분리한다', () => {
    const opt = buildClusterOption(resp);
    const series = opt.series as any[];
    expect(series).toHaveLength(2);
    expect(series[0].name).toBe('Cluster 0');
    expect(series[1].name).toBe('Cluster 1');
    expect(series[0].data).toHaveLength(2);
    expect(series[1].data).toHaveLength(1);
  });

  it('포인트 데이터에 code/sent 가 포함된다', () => {
    const opt = buildClusterOption(resp);
    const first = (opt.series as any[])[0].data[0] as any[];
    // [x, y, size, code, sent]
    expect(first[3]).toBe('A');
    expect(first[4]).toBe(0.2);
  });
});
