// cytoStyles 헬퍼 단위 테스트 — P2-3 T1
//
// nodeSize/edgeWidth/sentimentBorderColor 가 요구된 변환 규칙
// (log scale + 감성 부호 매핑) 을 만족하는지 검증한다.
import { describe, it, expect } from 'vitest';
import {
  nodeSize,
  edgeWidth,
  sentimentBorderColor,
  toCytoscapeElements,
} from './cytoStyles';
import type { KGNode, KGEdge } from '../../types/kg';

describe('cytoStyles helpers', () => {
  it('nodeSize: count=0 → 최소값 12, count 증가 시 단조 증가', () => {
    expect(nodeSize(0)).toBe(12);
    expect(nodeSize(1)).toBeGreaterThan(nodeSize(0));
    expect(nodeSize(100)).toBeGreaterThan(nodeSize(10));
    expect(nodeSize(10_000)).toBeGreaterThan(nodeSize(1_000));
  });

  it('edgeWidth: weight=0 → 0.5, weight 증가 시 단조 증가', () => {
    expect(edgeWidth(0)).toBeCloseTo(0.5, 2);
    expect(edgeWidth(10)).toBeGreaterThan(edgeWidth(1));
    expect(edgeWidth(100)).toBeGreaterThan(edgeWidth(10));
  });

  it('sentimentBorderColor: 양수=초록 / 음수=빨강 / null=회색 / 중립=회색', () => {
    expect(sentimentBorderColor(0.5)).toBe('#52c41a');
    expect(sentimentBorderColor(0.1)).toBe('#52c41a');
    expect(sentimentBorderColor(-0.5)).toBe('#f5222d');
    expect(sentimentBorderColor(-0.1)).toBe('#f5222d');
    expect(sentimentBorderColor(0)).toBe('#bfbfbf');
    expect(sentimentBorderColor(0.05)).toBe('#bfbfbf');
    expect(sentimentBorderColor(null)).toBe('#bfbfbf');
  });

  it('toCytoscapeElements: 노드/엣지 변환 + data 메타 세팅', () => {
    const nodes: KGNode[] = [
      { id: 'product:GS25U', label: 'GS25U', type: 'product', count: 1234, sent_avg: 0.4 },
      { id: 'category:battery', label: 'battery', type: 'category', count: 88, sent_avg: -0.3 },
    ];
    const edges: KGEdge[] = [
      {
        source: 'product:GS25U',
        target: 'category:battery',
        weight: 50,
        sent_avg: 0.1,
        edge_type: 'product_category',
      },
    ];
    const els = toCytoscapeElements(nodes, edges);
    expect(els).toHaveLength(3);

    const productNode = els.find((e) => e.data.id === 'product:GS25U')!;
    expect(productNode.group).toBe('nodes');
    expect(productNode.data._color).toBe('#1677ff');
    expect(productNode.data._border).toBe('#52c41a'); // sent_avg=0.4 → green
    expect(productNode.data._size).toBe(nodeSize(1234));

    const categoryNode = els.find((e) => e.data.id === 'category:battery')!;
    expect(categoryNode.data._color).toBe('#52c41a');
    expect(categoryNode.data._border).toBe('#f5222d'); // sent_avg=-0.3 → red

    const edge = els.find((e) => e.group === 'edges')!;
    expect(edge.data.source).toBe('product:GS25U');
    expect(edge.data.target).toBe('category:battery');
    expect(edge.data._width).toBe(edgeWidth(50));
    expect(edge.data.edge_type).toBe('product_category');
  });
});
