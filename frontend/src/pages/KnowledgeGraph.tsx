// 지식 그래프 페이지 — P2-3 T1
//
// /api/v1/kg/graph 응답을 Cytoscape.js + cose-bilkent 레이아웃으로 시각화한다.
// - GlobalFilter (dateRange/products/regions/platforms) 변경 시 자동 재조회
// - 노드 클릭 → NodeDetailPanel (Drawer) 에서 샘플 5건
// - 검색창 → 노드 선택 시 zoom + 패널 열기
//
// 본 페이지는 App.tsx 에서 React.lazy 로 로드되므로,
// cytoscape / cose-bilkent 청크가 자동 분리되어 KG 페이지 진입 시점에만 다운로드된다.
import { useEffect, useMemo, useRef, useState } from 'react';
import { Alert, Card, Col, Empty, Row, Space, Spin, Typography } from 'antd';
import { useQuery } from '@tanstack/react-query';
import cytoscape from 'cytoscape';
// @ts-expect-error — cose-bilkent 타입 없음
import coseBilkent from 'cytoscape-cose-bilkent';

import { useFilterStore } from '../stores/useFilterStore';
import { fetchKGGraph } from '../services/kgApi';
import KGControls from '../components/kg/KGControls';
import KGSearchInput from '../components/kg/KGSearchInput';
import NodeDetailPanel from '../components/kg/NodeDetailPanel';
import {
  CYTO_STYLE,
  COSE_BILKENT_LAYOUT,
  toCytoscapeElements,
} from '../components/kg/cytoStyles';
import {
  DEFAULT_KG_CONTROLS,
  type KGControls as KGControlsState,
  type KGNode,
  type KGNodeType,
} from '../types/kg';

const { Title, Paragraph, Text } = Typography;

// cose-bilkent extension 1회만 등록
let coseRegistered = false;
function ensureCoseRegistered() {
  if (!coseRegistered) {
    cytoscape.use(coseBilkent);
    coseRegistered = true;
  }
}

export default function KnowledgeGraph() {
  const { dateRange, products, regions, platforms } = useFilterStore();
  const [controls, setControls] = useState<KGControlsState>(DEFAULT_KG_CONTROLS);
  const [selectedNode, setSelectedNode] = useState<{
    id: string;
    label: string;
    type: KGNodeType;
  } | null>(null);
  const [panelOpen, setPanelOpen] = useState(false);

  const containerRef = useRef<HTMLDivElement | null>(null);
  const cyRef = useRef<cytoscape.Core | null>(null);

  const { data, isLoading, isError, error } = useQuery({
    queryKey: [
      'kg',
      'graph',
      dateRange.start,
      dateRange.end,
      products.join(','),
      regions.join(','),
      platforms.join(','),
      controls.topN,
      controls.minWeight,
      controls.edgeTypes.join(','),
    ],
    queryFn: () =>
      fetchKGGraph({
        start: dateRange.start,
        end: dateRange.end,
        top_n: controls.topN,
        min_weight: controls.minWeight,
        edge_types: controls.edgeTypes,
        products,
        platforms,
        regions,
      }),
    staleTime: 60_000,
    retry: 0,
  });

  // 노드 라벨/타입 lookup (id → meta)
  const nodeMeta = useMemo(() => {
    const m = new Map<string, KGNode>();
    (data?.nodes ?? []).forEach((n) => m.set(n.id, n));
    return m;
  }, [data]);

  // Cytoscape 인스턴스 생성 / 갱신
  useEffect(() => {
    if (!containerRef.current) return;
    if (!data) return;

    ensureCoseRegistered();

    // 기존 인스턴스 정리
    if (cyRef.current) {
      cyRef.current.destroy();
      cyRef.current = null;
    }

    const elements = toCytoscapeElements(data.nodes, data.edges);
    const cy = cytoscape({
      container: containerRef.current,
      elements,
      style: CYTO_STYLE,
      layout: COSE_BILKENT_LAYOUT as unknown as cytoscape.LayoutOptions,
      wheelSensitivity: 0.2,
      minZoom: 0.2,
      maxZoom: 3,
    });

    cy.on('tap', 'node', (evt) => {
      const node = evt.target;
      const id = node.id();
      const meta = nodeMeta.get(id);
      if (!meta) return;
      setSelectedNode({ id, label: meta.label, type: meta.type });
      setPanelOpen(true);
    });

    cyRef.current = cy;

    return () => {
      cy.destroy();
      cyRef.current = null;
    };
  }, [data, nodeMeta]);

  // 검색에서 노드 선택 → 줌 + 패널 열기
  const handleSearchSelect = (nodeId: string) => {
    const meta = nodeMeta.get(nodeId);
    if (meta) {
      setSelectedNode({ id: nodeId, label: meta.label, type: meta.type });
      setPanelOpen(true);
    } else {
      // 그래프 외부 노드라도 패널은 시도 (백엔드가 샘플을 줄 수 있음)
      setSelectedNode({ id: nodeId, label: nodeId, type: 'product' });
      setPanelOpen(true);
    }
    const cy = cyRef.current;
    if (cy) {
      const el = cy.getElementById(nodeId);
      if (el && el.length > 0) {
        cy.animate({ center: { eles: el }, zoom: 1.5 }, { duration: 400 });
        el.select();
      }
    }
  };

  const nodeCount = data?.nodes.length ?? 0;
  const edgeCount = data?.edges.length ?? 0;

  return (
    <div>
      <Title level={3} style={{ marginTop: 0 }}>
        지식 그래프
      </Title>
      <Paragraph type="secondary">
        제품·카테고리·플랫폼·국가 관계망 (Cytoscape.js + cose-bilkent 레이아웃)
      </Paragraph>

      <Row gutter={[16, 16]}>
        <Col xs={24} md={6}>
          <Space direction="vertical" style={{ width: '100%' }} size={12}>
            <KGControls value={controls} onChange={setControls} />
            <Card size="small" title="범례">
              <Space direction="vertical" size={4}>
                <Text>
                  <span style={legendDot('#1677ff')} /> Product
                </Text>
                <Text>
                  <span style={legendDot('#52c41a')} /> Category
                </Text>
                <Text>
                  <span style={legendDot('#fa8c16')} /> Platform
                </Text>
                <Text>
                  <span style={legendDot('#722ed1')} /> Country
                </Text>
                <Text type="secondary" style={{ fontSize: 12, marginTop: 6 }}>
                  테두리: 초록=긍정 / 빨강=부정 / 회색=중립
                </Text>
                <Text type="secondary" style={{ fontSize: 12 }}>
                  크기·두께: log scale (count·weight)
                </Text>
              </Space>
            </Card>
            <Card size="small" title="요약">
              <Text>노드: {nodeCount}</Text>
              <br />
              <Text>엣지: {edgeCount}</Text>
            </Card>
          </Space>
        </Col>

        <Col xs={24} md={18}>
          <Card
            size="small"
            title={
              <Space>
                <Text>그래프</Text>
                <KGSearchInput onSelectNode={handleSearchSelect} />
              </Space>
            }
            bodyStyle={{ padding: 0, position: 'relative' }}
          >
            {isError && (
              <Alert
                type="warning"
                showIcon
                style={{ margin: 12 }}
                message="그래프 API 호출 실패"
                description={
                  <span>
                    <code>/api/v1/kg/graph</code> 응답을 받지 못했습니다. 백엔드 구현
                    상태를 확인하세요. ({(error as Error)?.message})
                  </span>
                }
              />
            )}
            <div style={{ position: 'relative', height: 640 }}>
              {isLoading && (
                <div style={loadingOverlay}>
                  <Spin tip="그래프 로딩 중..." />
                </div>
              )}
              {!isLoading && !isError && nodeCount === 0 && (
                <div style={loadingOverlay}>
                  <Empty description="표시할 노드가 없습니다 (필터를 조정해보세요)" />
                </div>
              )}
              <div
                ref={containerRef}
                style={{
                  width: '100%',
                  height: '100%',
                  background: '#fafafa',
                }}
              />
            </div>
          </Card>
        </Col>
      </Row>

      <NodeDetailPanel
        open={panelOpen}
        nodeId={selectedNode?.id ?? null}
        nodeLabel={selectedNode?.label}
        nodeType={selectedNode?.type}
        onClose={() => setPanelOpen(false)}
      />
    </div>
  );
}

const loadingOverlay: React.CSSProperties = {
  position: 'absolute',
  inset: 0,
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
  background: 'rgba(255,255,255,0.6)',
  zIndex: 2,
};

function legendDot(color: string): React.CSSProperties {
  return {
    display: 'inline-block',
    width: 10,
    height: 10,
    borderRadius: '50%',
    background: color,
    marginRight: 6,
    verticalAlign: 'middle',
  };
}
