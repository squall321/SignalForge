// Data Clean 2 — Track D4.
// 사용자가 실시간으로 voc 품질 (mx_match_active / active) 을 확인.
// worst 사이트 테이블로 다음 정리 대상을 즉시 파악.
import { useMemo } from 'react';
import {
  Alert,
  Card,
  Col,
  Progress,
  Row,
  Space,
  Spin,
  Statistic,
  Table,
  Tag,
  Typography,
} from 'antd';
import { useQuery } from '@tanstack/react-query';
import dayjs from 'dayjs';
import {
  fetchDataQuality,
  parseMatchPct,
  activeRatio,
  worstSorted,
  matchPctTone,
  dataGrowSites,
  highMatchSites,
  type DataQualityWorstSite,
} from '../services/dataQualityApi';

const { Title, Paragraph, Text } = Typography;

const TONE_COLOR: Record<ReturnType<typeof matchPctTone>, string> = {
  danger: '#cf1322',
  warning: '#d48806',
  normal: '#1677ff',
  good: '#389e0d',
};

export default function DataQuality() {
  const { data, isLoading, isError, error } = useQuery({
    queryKey: ['data-quality'],
    queryFn: fetchDataQuality,
    staleTime: 60_000,
    refetchInterval: 60_000,
  });

  const activePct = useMemo(
    () => (data ? activeRatio(data.active, data.total) : 0),
    [data],
  );
  const worst = useMemo(
    () => (data ? worstSorted(data.by_site_worst) : []),
    [data],
  );
  // L7 — Data Grow R5: 신규 collector active voc 카드용
  const dataGrow = useMemo(
    () => (data ? dataGrowSites(data.by_site_worst) : []),
    [data],
  );
  // L7 — 글로벌 IT 매칭 90%+ 우수 사이트 (worst 응답 상위만 검출)
  const highMatch = useMemo(
    () => (data ? highMatchSites(data.by_site_worst, 90) : []),
    [data],
  );

  const columns = [
    {
      title: '코드',
      dataIndex: 'code',
      key: 'code',
      width: 180,
      render: (c: string) => <Text code>{c}</Text>,
    },
    {
      title: '활성 voc',
      dataIndex: 'active',
      key: 'active',
      width: 110,
      align: 'right' as const,
      sorter: (a: DataQualityWorstSite, b: DataQualityWorstSite) =>
        a.active - b.active,
      render: (v: number) => v.toLocaleString(),
    },
    {
      title: 'MX 매치',
      dataIndex: 'mx_match',
      key: 'mx_match',
      width: 110,
      align: 'right' as const,
      sorter: (a: DataQualityWorstSite, b: DataQualityWorstSite) =>
        a.mx_match - b.mx_match,
      render: (v: number) => v.toLocaleString(),
    },
    {
      title: '매치율',
      dataIndex: 'match_pct',
      key: 'match_pct',
      width: 140,
      align: 'right' as const,
      defaultSortOrder: 'ascend' as const,
      sorter: (a: DataQualityWorstSite, b: DataQualityWorstSite) =>
        parseMatchPct(a.match_pct) - parseMatchPct(b.match_pct),
      render: (s: string) => {
        const n = parseMatchPct(s);
        const tone = matchPctTone(n);
        return (
          <Tag color={TONE_COLOR[tone]} style={{ marginRight: 0 }}>
            {n.toFixed(1)} %
          </Tag>
        );
      },
    },
  ];

  return (
    <div data-testid="data-quality-page">
      <Title level={3} style={{ marginTop: 0 }}>
        데이터 품질
      </Title>
      <Paragraph type="secondary">
        총 voc 중 실제 Samsung MX 매칭 비율과, 매치율이 낮은 worst 사이트를
        실시간으로 봅니다. 60초마다 자동 새로고침.
      </Paragraph>

      {isError && (
        <Alert
          type="error"
          showIcon
          message="데이터 품질 로드 실패"
          description={error instanceof Error ? error.message : '알 수 없는 오류'}
          style={{ marginBottom: 16 }}
        />
      )}

      {isLoading && (
        <div style={{ textAlign: 'center', padding: 80 }}>
          <Spin size="large" />
        </div>
      )}

      {data && (
        <>
          {/* 상단 KPI 4 카드 */}
          <Row gutter={[16, 16]} style={{ marginBottom: 16 }}>
            <Col xs={12} md={6}>
              <Card size="small">
                <Statistic
                  title="총 voc"
                  value={data.total}
                  formatter={(v) => Number(v).toLocaleString()}
                />
              </Card>
            </Col>
            <Col xs={12} md={6}>
              <Card size="small">
                <Statistic
                  title="활성"
                  value={data.active}
                  formatter={(v) => Number(v).toLocaleString()}
                  suffix={`(${activePct.toFixed(1)} %)`}
                />
              </Card>
            </Col>
            <Col xs={12} md={6}>
              <Card size="small">
                <Statistic
                  title="archived"
                  value={data.archived}
                  formatter={(v) => Number(v).toLocaleString()}
                  suffix={`(${data.archived_pct.toFixed(1)} %)`}
                />
              </Card>
            </Col>
            <Col xs={12} md={6}>
              <Card size="small">
                <Statistic
                  title="MX 매치율 (활성 기준)"
                  value={data.mx_match_pct}
                  precision={1}
                  suffix="%"
                  valueStyle={{
                    color: TONE_COLOR[matchPctTone(data.mx_match_pct)],
                  }}
                />
              </Card>
            </Col>
          </Row>

          {/* 활성/총 progress */}
          <Card size="small" style={{ marginBottom: 16 }}>
            <Space direction="vertical" size={8} style={{ width: '100%' }}>
              <Text strong>활성 / 총 voc</Text>
              <Progress
                percent={activePct}
                status={
                  activePct < 30
                    ? 'exception'
                    : activePct > 70
                    ? 'success'
                    : 'normal'
                }
                format={(p) => `${(p ?? 0).toFixed(1)} %`}
              />
              <Text type="secondary" style={{ fontSize: 12 }}>
                활성 {data.active.toLocaleString()} / 총{' '}
                {data.total.toLocaleString()} (archived{' '}
                {data.archived.toLocaleString()})
              </Text>
              <Text strong style={{ marginTop: 8 }}>
                MX 매치 / 활성
              </Text>
              <Progress
                percent={data.mx_match_pct}
                status={data.mx_match_pct < 40 ? 'exception' : 'active'}
                strokeColor={TONE_COLOR[matchPctTone(data.mx_match_pct)]}
                format={(p) => `${(p ?? 0).toFixed(1)} %`}
              />
              <Text type="secondary" style={{ fontSize: 12 }}>
                MX 매치 {data.mx_match_active.toLocaleString()} (rich{' '}
                {data.mx_rich_active.toLocaleString()},{' '}
                {data.mx_rich_pct.toFixed(1)} %)
              </Text>
            </Space>
          </Card>

          {/* L7 — Data Grow R5 신규 사이트 카드 */}
          <Card
            size="small"
            title={`Data Grow 신규 사이트 (${dataGrow.length}곳 / 추적 11곳)`}
            style={{ marginBottom: 16 }}
            data-testid="data-grow-card"
          >
            {dataGrow.length === 0 ? (
              <Text type="secondary" style={{ fontSize: 12 }}>
                활성 voc 없음 — 신규 collector 발화 대기 중 또는 active &le; 30
                (worst 응답은 active &gt; 30 만 포함).
              </Text>
            ) : (
              <Row gutter={[8, 8]}>
                {dataGrow.map((s) => {
                  const n = parseMatchPct(s.match_pct);
                  const tone = matchPctTone(n);
                  return (
                    <Col xs={12} sm={8} md={6} key={s.code}>
                      <Card size="small" styles={{ body: { padding: 10 } }}>
                        <Space direction="vertical" size={2}>
                          <Text code style={{ fontSize: 12 }}>
                            {s.code}
                          </Text>
                          <Text strong>{s.active.toLocaleString()}</Text>
                          <Tag color={TONE_COLOR[tone]} style={{ marginRight: 0 }}>
                            {n.toFixed(1)} %
                          </Tag>
                        </Space>
                      </Card>
                    </Col>
                  );
                })}
              </Row>
            )}
          </Card>

          {/* L7 — 글로벌 IT 매칭 90%+ 우수 사이트 */}
          <Card
            size="small"
            title={`글로벌 IT 매칭 양호 사이트 (${highMatch.length}곳, ≥ 90 %)`}
            style={{ marginBottom: 16 }}
            data-testid="high-match-card"
          >
            {highMatch.length === 0 ? (
              <Text type="secondary" style={{ fontSize: 12 }}>
                worst 응답 내 90 %+ 사이트 없음. 전체 best 는 별도 endpoint 가
                필요 (worst 20곳 한정 응답).
              </Text>
            ) : (
              <Space size={[8, 8]} wrap>
                {highMatch.map((s) => (
                  <Tag
                    key={s.code}
                    color={TONE_COLOR.good}
                    style={{ padding: '4px 10px' }}
                  >
                    {s.code} · {s.active.toLocaleString()} ·{' '}
                    {parseMatchPct(s.match_pct).toFixed(1)} %
                  </Tag>
                ))}
              </Space>
            )}
          </Card>

          {/* worst 사이트 테이블 */}
          <Card
            size="small"
            title={`worst 사이트 — 매치율 낮은 순 (${worst.length}곳)`}
          >
            <Table<DataQualityWorstSite>
              rowKey="code"
              size="small"
              columns={columns}
              dataSource={worst}
              pagination={{ pageSize: 25, showSizeChanger: true }}
            />
            <Text type="secondary" style={{ fontSize: 12 }}>
              생성 시각: {dayjs(data.generated_at).format('YYYY-MM-DD HH:mm:ss')}
            </Text>
          </Card>
        </>
      )}
    </div>
  );
}
