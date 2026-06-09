// Track A — 알림 운영 모니터링 패널.
//   - 활성 룰의 health badge (normal/silent/noisy/violating)
//   - 7일 발화 sparkline (avg 4가지 + 24h 발화 비교)
//   - 자동 권고 카드
//
// /api/v1/_internal/alert-monitor 응답을 단순 표시. data fetch 는 부모(Alerts.tsx).
import { Alert, Card, Col, Empty, Row, Space, Statistic, Table, Tag, Typography } from 'antd';
import type {
  AlertHealth,
  AlertMonitorResponse,
  AlertMonitorRule,
} from '../../services/alertsApi';
import {
  HEALTH_COLOR,
  HEALTH_LABEL,
  classifyRecommendation,
  ruleToSpark,
} from './alertMonitorUtils';

interface Props {
  data: AlertMonitorResponse | null;
  loading?: boolean;
}

/**
 * 단순 SVG 스파크라인 — n 점의 y 값 0~max 정규화.
 * width × height 픽셀 box 안에 polyline 한 줄.  외부 라이브러리 회피.
 */
function MiniSpark({ values }: { values: number[] }) {
  const w = 80;
  const h = 22;
  if (values.length === 0) {
    return <span style={{ color: '#999', fontSize: 12 }}>-</span>;
  }
  const max = Math.max(1, ...values);
  const step = values.length > 1 ? w / (values.length - 1) : w;
  const pts = values
    .map((v, i) => `${(i * step).toFixed(1)},${(h - (v / max) * h).toFixed(1)}`)
    .join(' ');
  return (
    <svg
      width={w}
      height={h}
      role="img"
      aria-label={`발화 ${values.reduce((a, b) => a + b, 0)}건`}
      style={{ display: 'block' }}
    >
      <polyline
        fill="none"
        stroke="#1677ff"
        strokeWidth={1.5}
        points={pts}
      />
    </svg>
  );
}

export default function AlertMonitorPanel({ data, loading }: Props) {
  if (!data && !loading) {
    return (
      <Card title="운영 모니터링" size="small">
        <Empty description="데이터 없음" image={Empty.PRESENTED_IMAGE_SIMPLE} />
      </Card>
    );
  }
  const summary = data?.summary;
  const rules = data?.rules ?? [];
  const recs = data?.recommendations ?? [];

  return (
    <Card
      title={
        <Space>
          <Typography.Text strong>운영 모니터링</Typography.Text>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            ({data?.days ?? 7}일 윈도우)
          </Typography.Text>
        </Space>
      }
      size="small"
      loading={loading}
    >
      <Row gutter={[16, 16]}>
        <Col xs={12} sm={6}>
          <Statistic title="활성 룰" value={summary?.active_rules ?? 0} />
        </Col>
        <Col xs={12} sm={6}>
          <Statistic title="24h 발화" value={summary?.fires_24h ?? 0} />
        </Col>
        <Col xs={12} sm={6}>
          <Statistic title="7d 발화" value={summary?.fires_7d ?? 0} />
        </Col>
        <Col xs={12} sm={6}>
          <Statistic
            title="cooldown 위반 24h"
            value={summary?.cooldown_violations_24h ?? 0}
            valueStyle={
              (summary?.cooldown_violations_24h ?? 0) > 0
                ? { color: '#cf1322' }
                : undefined
            }
          />
        </Col>
      </Row>

      <Typography.Title level={5} style={{ marginTop: 16, marginBottom: 8 }}>
        룰별 health
      </Typography.Title>
      <Table<AlertMonitorRule>
        dataSource={rules}
        rowKey="rule_id"
        size="small"
        pagination={false}
        columns={[
          { title: '이름', dataIndex: 'name', key: 'n', ellipsis: true },
          {
            title: 'health',
            dataIndex: 'health',
            key: 'h',
            width: 110,
            render: (h: AlertHealth) => (
              <Tag color={HEALTH_COLOR[h]} data-testid={`health-${h}`}>
                {HEALTH_LABEL[h]}
              </Tag>
            ),
          },
          { title: '24h', dataIndex: 'fires_24h', key: '24', width: 60, align: 'right' as const },
          { title: '7d', dataIndex: 'fires_7d', key: '7d', width: 60, align: 'right' as const },
          {
            title: '7d 발화 추이',
            key: 'spark',
            width: 100,
            render: (_: unknown, r: AlertMonitorRule) => (
              <MiniSpark values={ruleToSpark(r)} />
            ),
          },
          {
            title: 'cooldown 위반',
            dataIndex: 'cooldown_violations_24h',
            key: 'v',
            width: 90,
            align: 'right' as const,
            render: (n: number) =>
              n > 0 ? <Typography.Text type="danger">{n}</Typography.Text> : n,
          },
        ]}
        scroll={{ x: true }}
      />

      <Typography.Title level={5} style={{ marginTop: 16, marginBottom: 8 }}>
        권고
      </Typography.Title>
      {recs.length === 0 ? (
        <Alert
          type="success"
          showIcon
          message="권고 없음 — 모든 룰이 정상 동작 중"
        />
      ) : (
        <Space direction="vertical" size={6} style={{ width: '100%' }}>
          {recs.map((rec, i) => (
            <Alert
              key={i}
              type={classifyRecommendation(rec)}
              showIcon
              message={rec}
            />
          ))}
        </Space>
      )}
    </Card>
  );
}
