// P5 R6 트랙 A — Dashboard 첫인상 강화.
// /api/v1/dashboard/overview 실 연동 + 라이브 알림 배지(헤더 LiveAlertBadge) + 오늘의 신호 + 빠른 진입.
import { useEffect, useState } from 'react';
import { Col, Row, Typography, Alert } from 'antd';
import {
  CommentOutlined,
  AppstoreOutlined,
  AlertOutlined,
  FrownOutlined,
} from '@ant-design/icons';
import { useQuery } from '@tanstack/react-query';
import { useFilterStore } from '../stores/useFilterStore';
import { fetchOverview } from '../services/dashboardApi';
import KPICard from '../components/dashboard/KPICard';
import BackupStatusCard from '../components/dashboard/BackupStatusCard';
import TodaySignal from '../components/dashboard/TodaySignal';
import QuickActions from '../components/dashboard/QuickActions';
import { computeKPIDeltas } from '../components/dashboard/kpiUtils';
import FavoritesSection from '../components/global/FavoritesSection';
import ExportButton from '../components/global/ExportButton';

const { Title, Paragraph } = Typography;

// 날짜 범위 → period 매핑 (간단화: 8d 이내=7d, 31d 이내=30d, 그 외 90d).
function pickPeriod(dateRange?: [string | null, string | null] | null): '7d' | '30d' | '90d' {
  if (!dateRange || !dateRange[0] || !dateRange[1]) return '7d';
  const diffDays =
    (new Date(dateRange[1]).getTime() - new Date(dateRange[0]).getTime()) /
    (1000 * 60 * 60 * 24);
  if (diffDays <= 8) return '7d';
  if (diffDays <= 31) return '30d';
  return '90d';
}

export default function Dashboard() {
  const { dateRange, products, regions, platforms } = useFilterStore();
  const [now, setNow] = useState(new Date());

  useEffect(() => {
    const t = setInterval(() => setNow(new Date()), 60_000);
    return () => clearInterval(t);
  }, []);

  const period = pickPeriod(dateRange as never);
  const product = products[0];
  const country = regions[0];
  const platform = platforms[0];

  const { data, isLoading, isError } = useQuery({
    queryKey: ['dashboard-overview', period, product, country, platform],
    queryFn: () => fetchOverview({ period, product, country, platform }),
    staleTime: 60_000,
  });

  const overview = data ?? null;
  const kpis = overview?.kpis;
  const trend14d = overview?.trend14d ?? [];
  const deltas = kpis
    ? computeKPIDeltas(trend14d, kpis)
    : { total_voc: null, neg_rate: null, alert_count: null };

  // 내보내기 대상: 선택된 product 우선, 없으면 GS25 (기본 시리즈).
  const exportSeries = product || 'GS25';
  const exportPeriodDays = period === '7d' ? 7 : period === '30d' ? 30 : 90;

  return (
    <div>
      <div
        style={{
          display: 'flex',
          alignItems: 'flex-start',
          justifyContent: 'space-between',
          marginBottom: 4,
        }}
      >
        <Title level={3} style={{ marginTop: 0, marginBottom: 4 }}>
          Overview
        </Title>
        <ExportButton series={exportSeries} periodDays={exportPeriodDays} />
      </div>
      <Paragraph type="secondary" style={{ marginBottom: 16 }}>
        Samsung MX VOC Intelligence — 핵심 지표 ({period}) · 마지막 갱신{' '}
        {now.toLocaleTimeString('ko-KR', { hour: '2-digit', minute: '2-digit' })}
      </Paragraph>

      {isError && (
        <Alert
          type="warning"
          showIcon
          style={{ marginBottom: 16 }}
          message="대시보드 데이터 로드 실패"
          description="백엔드 응답을 확인할 수 없습니다. 잠시 후 자동 재시도됩니다."
        />
      )}

      {/* 상단 KPI 4 카드 */}
      <Row gutter={[12, 12]} style={{ marginBottom: 16 }}>
        <Col xs={24} sm={12} lg={6}>
          <KPICard
            title="총 VOC 건수"
            value={kpis?.total_voc ?? (isLoading ? '—' : 0)}
            suffix="건"
            deltaPct={deltas.total_voc}
            goodWhenUp
            icon={<CommentOutlined style={{ color: '#1677ff' }} />}
            tooltip="전체 VOC 유입량 (선택 기간 기준)"
          />
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <KPICard
            title="부정 비율"
            value={kpis ? Number(kpis.neg_rate.toFixed(1)) : isLoading ? '—' : 0}
            suffix="%"
            deltaPct={deltas.neg_rate}
            deltaAbs={deltas.neg_rate}
            deltaUnit="pp"
            goodWhenUp={false}
            icon={<FrownOutlined style={{ color: '#cf1322' }} />}
            tooltip="감성 점수 -0.1 이하 비율"
          />
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <KPICard
            title="주목 제품"
            value={kpis?.top_product ?? (isLoading ? '—' : 'N/A')}
            deltaPct={null}
            goodWhenUp
            icon={<AppstoreOutlined style={{ color: '#722ed1' }} />}
            tooltip="언급 비중 1위 제품"
          />
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <KPICard
            title="알림 임계 초과 제품"
            value={kpis?.alert_count ?? (isLoading ? '—' : 0)}
            suffix="개"
            deltaPct={null}
            goodWhenUp={false}
            icon={<AlertOutlined style={{ color: '#fa541c' }} />}
            tooltip="부정률 > 임계값(50%) 인 제품 수"
          />
        </Col>
      </Row>

      {/* 오늘의 신호 + 빠른 진입 */}
      <Row gutter={[12, 12]} style={{ marginBottom: 16 }}>
        <Col xs={24} lg={14}>
          <TodaySignal overview={overview} loading={isLoading} />
        </Col>
        <Col xs={24} lg={10}>
          <QuickActions />
        </Col>
      </Row>

      {/* Track E — Drive 백업 상태 (mini KPI). */}
      <Row gutter={[12, 12]} style={{ marginBottom: 16 }}>
        <Col xs={24} lg={12}>
          <BackupStatusCard />
        </Col>
      </Row>

      {/* 트랙 D — 즐겨찾기 (localStorage 영속) */}
      <Row gutter={[12, 12]} style={{ marginBottom: 16 }}>
        <Col xs={24}>
          <FavoritesSection />
        </Col>
      </Row>

      {import.meta.env.DEV && (
        <details style={{ fontSize: 12, color: '#888' }}>
          <summary>디버그: 현재 필터</summary>
          <pre style={{ background: '#fafafa', padding: 8, borderRadius: 6 }}>
            {JSON.stringify({ dateRange, products, regions, platforms, period }, null, 2)}
          </pre>
        </details>
      )}
    </div>
  );
}
