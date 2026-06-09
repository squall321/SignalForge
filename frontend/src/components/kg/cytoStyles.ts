// Cytoscape 스타일/레이아웃 헬퍼 — P2-3 T1
//
// - 노드 색: type 별 (product=blue, category=green, platform=orange, country=purple)
// - 노드 크기: log10(count+1) * 8 + 12
// - 노드 테두리: sent_avg → 양=초록 / 음=빨강 / null=회색
// - 엣지 두께: log10(weight+1) * 1.5 + 0.5
import type { KGNode, KGEdge } from '../../types/kg';
import { NODE_TYPE_COLOR } from '../../types/kg';

export function nodeSize(count: number): number {
  const c = Math.max(count, 0);
  return Math.round(Math.log10(c + 1) * 8 + 12);
}

export function edgeWidth(weight: number): number {
  const w = Math.max(weight, 0);
  return +(Math.log10(w + 1) * 1.5 + 0.5).toFixed(2);
}

export function sentimentBorderColor(sent: number | null | undefined): string {
  if (sent == null) return '#bfbfbf';
  if (sent >= 0.1) return '#52c41a';   // positive → green
  if (sent <= -0.1) return '#f5222d';  // negative → red
  return '#bfbfbf';                    // neutral → grey
}

export function toCytoscapeElements(
  nodes: KGNode[],
  edges: KGEdge[],
): cytoscape.ElementDefinition[] {
  const nodeEls: cytoscape.ElementDefinition[] = nodes.map((n) => ({
    group: 'nodes',
    data: {
      id: n.id,
      label: n.label,
      type: n.type,
      count: n.count,
      sent_avg: n.sent_avg ?? 0,
      _size: nodeSize(n.count),
      _color: NODE_TYPE_COLOR[n.type],
      _border: sentimentBorderColor(n.sent_avg),
    },
  }));
  const edgeEls: cytoscape.ElementDefinition[] = edges.map((e, i) => ({
    group: 'edges',
    data: {
      id: `e${i}:${e.source}->${e.target}`,
      source: e.source,
      target: e.target,
      weight: e.weight,
      sent_avg: e.sent_avg ?? 0,
      edge_type: e.edge_type,
      _width: edgeWidth(e.weight),
    },
  }));
  return [...nodeEls, ...edgeEls];
}

export const CYTO_STYLE: cytoscape.StylesheetStyle[] = [
  {
    selector: 'node',
    style: {
      'background-color': 'data(_color)',
      'border-color': 'data(_border)',
      'border-width': 3,
      'width': 'data(_size)',
      'height': 'data(_size)',
      'label': 'data(label)',
      'font-size': 10,
      'color': '#1f1f1f',
      'text-valign': 'bottom',
      'text-halign': 'center',
      'text-margin-y': 4,
      'text-outline-color': '#fff',
      'text-outline-width': 2,
      'min-zoomed-font-size': 7,
    },
  },
  {
    selector: 'node:selected',
    style: {
      'border-color': '#1677ff',
      'border-width': 5,
    },
  },
  {
    selector: 'edge',
    style: {
      'width': 'data(_width)',
      'line-color': '#d9d9d9',
      'curve-style': 'bezier',
      'opacity': 0.7,
    },
  },
  {
    selector: 'edge[edge_type = "product_category"]',
    style: { 'line-color': '#a0d911' },
  },
  {
    selector: 'edge[edge_type = "product_platform"]',
    style: { 'line-color': '#ffa940' },
  },
  {
    selector: 'edge[edge_type = "product_country"]',
    style: { 'line-color': '#b37feb' },
  },
];

export const COSE_BILKENT_LAYOUT = {
  name: 'cose-bilkent',
  animate: false,
  randomize: true,
  nodeRepulsion: 8000,
  idealEdgeLength: 90,
  edgeElasticity: 0.45,
  gravity: 0.25,
  numIter: 2500,
  tile: true,
  padding: 20,
  nodeDimensionsIncludeLabels: true,
} as unknown as Record<string, unknown>;
