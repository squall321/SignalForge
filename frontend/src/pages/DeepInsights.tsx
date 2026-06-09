import { Suspense, lazy, useEffect, useRef, useState } from 'react';
import { Col, Divider, Row, Skeleton, Typography } from 'antd';
import { useViewport } from '../utils/useViewport';

// Standard 7 (기존, 차트 보강 완료)
import HourlyPatternCard from '../components/insights/HourlyPatternCard';
import WeekdayPatternCard from '../components/insights/WeekdayPatternCard';
import EmergingKeywordsCard from '../components/insights/EmergingKeywordsCard';
import NewTermsCard from '../components/insights/NewTermsCard';
import SentimentSwingCard from '../components/insights/SentimentSwingCard';
import LifecycleCard from '../components/insights/LifecycleCard';
import InfluenceCard from '../components/insights/InfluenceCard';

// Deep 8 (신규) — lazy 로드로 페이지 청크 축소
const IssueLifecycleCard = lazy(() => import('../components/deep/IssueLifecycleCard'));
const CategoryProductMatrixCard = lazy(
  () => import('../components/deep/CategoryProductMatrixCard'),
);
const SiteDiffusionCard = lazy(() => import('../components/deep/SiteDiffusionCard'));
const CountrySentimentGapCard = lazy(() => import('../components/deep/CountrySentimentGapCard'));
const EngagementSentimentCard = lazy(() => import('../components/deep/EngagementSentimentCard'));
const NewTermSurvivalCard = lazy(() => import('../components/deep/NewTermSurvivalCard'));
const KeywordCooccurrenceCard = lazy(() => import('../components/deep/KeywordCooccurrenceCard'));
const AnomalyContextCard = lazy(() => import('../components/deep/AnomalyContextCard'));
const AnomalyDriverCard = lazy(() => import('../components/deep/AnomalyDriverCard'));

// Deep D-track 5 (P3.7 트랙 D) — 추가 cut
const CategoryMomentumCard = lazy(() => import('../components/deep/CategoryMomentumCard'));
const KeywordNetworkCard = lazy(() => import('../components/deep/KeywordNetworkCard'));
const LifecycleFunnelCard = lazy(() => import('../components/deep/LifecycleFunnelCard'));
const InfluenceRankCard = lazy(() => import('../components/deep/InfluenceRankCard'));
const ProductFunnelCard = lazy(() => import('../components/deep/ProductFunnelCard'));

const { Title, Paragraph } = Typography;

/**
 * Viewport 진입 시에만 children 마운트 (IntersectionObserver).
 * Deep 카드는 무거운 echarts(force-graph/heatmap/sankey)를 포함하므로
 * 스크롤 도달 시까지 fetch/렌더를 지연한다.
 */
function LazyOnView({ children, minHeight = 320 }: { children: React.ReactNode; minHeight?: number }) {
  const ref = useRef<HTMLDivElement | null>(null);
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    if (!ref.current || visible) return;
    const io = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting)) {
          setVisible(true);
          io.disconnect();
        }
      },
      { rootMargin: '120px' },
    );
    io.observe(ref.current);
    return () => io.disconnect();
  }, [visible]);

  return (
    <div ref={ref} style={{ minHeight }}>
      {visible ? (
        <Suspense fallback={<Skeleton active style={{ padding: 16 }} />}>{children}</Suspense>
      ) : (
        <Skeleton active style={{ padding: 16 }} />
      )}
    </div>
  );
}

/**
 * 딥 인사이트 페이지 — 2 섹션 / 15 카드 그리드.
 *
 *  [Standard 7]  hourly | weekday | emerging | new-terms | swing | lifecycle | influence
 *  [Deep 8]      lifecycle | matrix | diffusion | country-gap | engagement | survival | cooccur | anomaly
 */
export default function DeepInsights() {
  // 트랙 D — 모바일: gutter 축소(8), 섹션 제목 16px / 데스크탑: gutter 16, 제목 default.
  const vp = useViewport();
  const gutter: [number, number] = vp.isMobile ? [8, 8] : [16, 16];
  const sectionTitleStyle = vp.isMobile ? { fontSize: 16 } : undefined;
  return (
    <div>
      <Title level={3} style={{ marginTop: 0, ...(vp.isMobile ? { fontSize: 18 } : {}) }}>
        딥 인사이트
      </Title>
      <Paragraph type="secondary" style={vp.isMobile ? { fontSize: 12 } : undefined}>
        Standard 7개 · Deep 8개 · Combined 1개 · D-track 5개 합 21개 카드. Standard 는 즉시 로드, Deep/D
        는 스크롤 도달 시 lazy fetch.
      </Paragraph>

      <Title level={5} style={{ marginTop: 8, marginBottom: 8, ...sectionTitleStyle }}>
        Standard
      </Title>
      <Row gutter={gutter}>
        <Col xs={24} lg={12}>
          <HourlyPatternCard />
        </Col>
        <Col xs={24} lg={12}>
          <WeekdayPatternCard />
        </Col>
        <Col xs={24} lg={12}>
          <EmergingKeywordsCard />
        </Col>
        <Col xs={24} lg={12}>
          <NewTermsCard />
        </Col>
        <Col xs={24} lg={12}>
          <SentimentSwingCard />
        </Col>
        <Col xs={24} lg={12}>
          <LifecycleCard />
        </Col>
        <Col xs={24}>
          <InfluenceCard />
        </Col>
      </Row>

      <Divider style={{ margin: vp.isMobile ? '16px 0 8px' : '24px 0 16px' }} />
      <Title level={5} style={{ marginTop: 0, marginBottom: 8, ...sectionTitleStyle }}>
        Deep
      </Title>
      <Row gutter={gutter}>
        <Col xs={24}>
          <LazyOnView>
            <AnomalyDriverCard />
          </LazyOnView>
        </Col>
        <Col xs={24} sm={24} md={12} lg={12} xl={8} xxl={6}>
          <LazyOnView>
            <IssueLifecycleCard />
          </LazyOnView>
        </Col>
        <Col xs={24} sm={24} md={12} lg={12} xl={8} xxl={6}>
          <LazyOnView>
            <CategoryProductMatrixCard />
          </LazyOnView>
        </Col>
        <Col xs={24} sm={24} md={12} lg={12} xl={8} xxl={6}>
          <LazyOnView>
            <SiteDiffusionCard />
          </LazyOnView>
        </Col>
        <Col xs={24} sm={24} md={12} lg={12} xl={8} xxl={6}>
          <LazyOnView>
            <CountrySentimentGapCard />
          </LazyOnView>
        </Col>
        <Col xs={24} sm={24} md={12} lg={12} xl={8} xxl={6}>
          <LazyOnView>
            <EngagementSentimentCard />
          </LazyOnView>
        </Col>
        <Col xs={24} sm={24} md={12} lg={12} xl={8} xxl={6}>
          <LazyOnView>
            <NewTermSurvivalCard />
          </LazyOnView>
        </Col>
        <Col xs={24} sm={24} md={12} lg={12} xl={8} xxl={6}>
          <LazyOnView>
            <KeywordCooccurrenceCard />
          </LazyOnView>
        </Col>
        <Col xs={24} sm={24} md={12} lg={12} xl={8} xxl={6}>
          <LazyOnView>
            <AnomalyContextCard />
          </LazyOnView>
        </Col>
      </Row>

      <Divider style={{ margin: vp.isMobile ? '16px 0 8px' : '24px 0 16px' }} />
      <Title level={5} style={{ marginTop: 0, marginBottom: 8, ...sectionTitleStyle }}>
        D-track (추가 cut)
      </Title>
      <Row gutter={gutter}>
        <Col xs={24} sm={24} md={12} lg={12} xxl={8}>
          <LazyOnView>
            <CategoryMomentumCard />
          </LazyOnView>
        </Col>
        <Col xs={24} sm={24} md={12} lg={12} xxl={8}>
          <LazyOnView>
            <KeywordNetworkCard />
          </LazyOnView>
        </Col>
        <Col xs={24} sm={24} md={12} lg={12} xxl={8}>
          <LazyOnView>
            <LifecycleFunnelCard />
          </LazyOnView>
        </Col>
        <Col xs={24} sm={24} md={12} lg={12} xxl={12}>
          <LazyOnView>
            <InfluenceRankCard />
          </LazyOnView>
        </Col>
        <Col xs={24} sm={24} md={12} lg={12} xxl={12}>
          <LazyOnView>
            <ProductFunnelCard />
          </LazyOnView>
        </Col>
      </Row>
    </div>
  );
}
