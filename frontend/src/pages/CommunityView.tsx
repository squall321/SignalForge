import { Tabs, Typography } from 'antd';
import {
  ApartmentOutlined,
  BoxPlotOutlined,
  ClusterOutlined,
  HeatMapOutlined,
  ThunderboltOutlined,
  WarningOutlined,
} from '@ant-design/icons';
import HealthTable from '../components/community/HealthTable';
import PlatformMatrix from '../components/community/PlatformMatrix';
import DispersionBoxplot from '../components/community/DispersionBoxplot';
import EarlySignalTimeline from '../components/community/EarlySignalTimeline';
import ClusterScatter from '../components/community/ClusterScatter';
import AnomalyList from '../components/community/AnomalyList';

const { Title, Paragraph } = Typography;

/**
 * T3 커뮤니티 비교 페이지 — 60+ 사이트(플랫폼) 간 활동/감성/확산 패턴 비교.
 *
 * 6개 탭:
 *   1) 사이트 상태       — platform_health MV 표
 *   2) 제품 매트릭스      — 플랫폼 × 제품 heatmap
 *   3) 분산               — 플랫폼별 감성 boxplot
 *   4) Early Signal      — 신호별 등장 시점 lag 타임라인
 *   5) 클러스터           — 임베딩 좌표 scatter + 클러스터 색
 *   6) 이상치             — kind/score 리스트
 */
export default function CommunityView() {
  return (
    <div>
      <Title level={3} style={{ marginTop: 0 }}>
        커뮤니티 비교
      </Title>
      <Paragraph type="secondary">
        60+ 사이트의 활동/감성/확산 패턴을 한눈에 비교하고, 클러스터·이상치로 운영 인사이트를 도출합니다.
      </Paragraph>

      <Tabs
        defaultActiveKey="health"
        items={[
          {
            key: 'health',
            label: (
              <span>
                <ApartmentOutlined /> 사이트 상태
              </span>
            ),
            children: <HealthTable />,
          },
          {
            key: 'matrix',
            label: (
              <span>
                <HeatMapOutlined /> 제품 매트릭스
              </span>
            ),
            children: <PlatformMatrix />,
          },
          {
            key: 'dispersion',
            label: (
              <span>
                <BoxPlotOutlined /> 분산
              </span>
            ),
            children: <DispersionBoxplot />,
          },
          {
            key: 'early',
            label: (
              <span>
                <ThunderboltOutlined /> Early Signal
              </span>
            ),
            children: <EarlySignalTimeline />,
          },
          {
            key: 'cluster',
            label: (
              <span>
                <ClusterOutlined /> 클러스터
              </span>
            ),
            children: <ClusterScatter />,
          },
          {
            key: 'anomaly',
            label: (
              <span>
                <WarningOutlined /> 이상치
              </span>
            ),
            children: <AnomalyList />,
          },
        ]}
      />
    </div>
  );
}
