import { useCallback, useState } from 'react';
import { Alert, Card, Col, Row, Segmented, Space, Spin, Typography } from 'antd';
import { useQuery } from '@tanstack/react-query';
import { fetchCountryHeatmap } from '../services/geoApi';
import { useFilterStore } from '../stores/useFilterStore';
import WorldChoropleth from '../components/geo/WorldChoropleth';
import CountryDrilldownPanel from '../components/geo/CountryDrilldownPanel';
import DiffusionPlayer from '../components/geo/DiffusionPlayer';
import ProductCompareBar from '../components/geo/ProductCompareBar';
import type { ChoroplethMode } from '../types/geo';

const { Title, Paragraph } = Typography;

export default function GeoView() {
  const { dateRange, products } = useFilterStore();
  const [mode, setMode] = useState<ChoroplethMode>('count');
  const [selectedCode, setSelectedCode] = useState<string | null>(null);
  const [frameOverride, setFrameOverride] = useState<Record<string, number> | undefined>(
    undefined,
  );

  const { data, isLoading, isError, error } = useQuery({
    queryKey: ['country-heatmap', dateRange.start, dateRange.end, products.join(',')],
    queryFn: () =>
      fetchCountryHeatmap({
        start: dateRange.start,
        end: dateRange.end,
        products,
      }),
    staleTime: 60_000,
  });

  const onFrame = useCallback((values: Record<string, number>) => {
    setFrameOverride(values);
  }, []);

  return (
    <div>
      <Title level={3} style={{ marginTop: 0 }}>
        국가 분석
      </Title>
      <Paragraph type="secondary">
        세계지도에서 국가별 VoC 신호 강도를 한눈에 보고, 클릭으로 드릴다운, 시간 슬라이더로 확산 패턴을 재생합니다.
      </Paragraph>

      <Space style={{ marginBottom: 12 }}>
        <Typography.Text strong>색상 기준:</Typography.Text>
        <Segmented
          value={mode}
          options={[
            { label: '건수', value: 'count' },
            { label: '감성 z', value: 'sent_z' },
          ]}
          onChange={(v) => {
            setMode(v as ChoroplethMode);
            setFrameOverride(undefined); // 모드 변경 시 frame override 해제
          }}
        />
      </Space>

      <Row gutter={[16, 16]}>
        <Col xs={24} xl={16}>
          <Card title="세계지도 (Choropleth)" bodyStyle={{ padding: 12 }}>
            {isLoading && (
              <div style={{ textAlign: 'center', padding: 120 }}>
                <Spin size="large" />
              </div>
            )}
            {isError && (
              <Alert
                type="error"
                showIcon
                message="국가 데이터 조회 실패"
                description={error instanceof Error ? error.message : '알 수 없는 오류'}
              />
            )}
            {data && !isLoading && (
              <WorldChoropleth
                countries={data.countries}
                mode={mode}
                selectedCode={selectedCode ?? undefined}
                onSelect={(code) => setSelectedCode(code)}
                valueOverride={frameOverride}
                height={520}
              />
            )}
          </Card>
        </Col>
        <Col xs={24} xl={8}>
          <Space direction="vertical" style={{ width: '100%' }} size={16}>
            <CountryDrilldownPanel code={selectedCode} />
            <DiffusionPlayer
              metric={mode}
              onMetricChange={(m) => {
                setMode(m);
                setFrameOverride(undefined);
              }}
              onFrameChange={onFrame}
            />
          </Space>
        </Col>
      </Row>

      <Row gutter={[16, 16]} style={{ marginTop: 16 }}>
        <Col span={24}>
          <ProductCompareBar />
        </Col>
      </Row>
    </div>
  );
}
