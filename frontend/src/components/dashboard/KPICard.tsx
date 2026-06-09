// P5 R6 트랙 A — KPI 카드 컴포넌트.
// 임원이 5초 안에 "오늘 무슨 일" 파악하도록 값 + Δ + 화살표 + 색을 한 컷으로.
import { Card, Statistic, Tooltip } from 'antd';
import { ArrowDownOutlined, ArrowUpOutlined, MinusOutlined } from '@ant-design/icons';
import {
  deltaColor,
  deltaDirection,
  type DeltaDirection,
} from './kpiUtils';

export interface KPICardProps {
  title: string;
  value: number | string;
  suffix?: string;
  precision?: number;
  /** 변화량(%) — null 이면 화살표 숨김. */
  deltaPct: number | null;
  /** 절대 변화량 — 표시용. neg_rate 같은 pp 지표에서 사용. */
  deltaAbs?: number | null;
  deltaUnit?: '%' | 'pp' | '건';
  /** 값이 클수록 좋은 지표인가? (neg_rate, alert_count 는 false). */
  goodWhenUp: boolean;
  /** 좌측 아이콘. */
  icon?: React.ReactNode;
  /** 부가 설명 (Tooltip). */
  tooltip?: string;
}

const ARROW_ICON: Record<DeltaDirection, React.ReactNode> = {
  up: <ArrowUpOutlined />,
  down: <ArrowDownOutlined />,
  flat: <MinusOutlined />,
};

export default function KPICard({
  title,
  value,
  suffix,
  precision,
  deltaPct,
  deltaAbs,
  deltaUnit = '%',
  goodWhenUp,
  icon,
  tooltip,
}: KPICardProps) {
  const dir = deltaDirection(deltaPct);
  const color = deltaColor(dir, goodWhenUp);
  const showDelta = deltaPct !== null;
  const deltaText = (() => {
    if (!showDelta) return '데이터 부족';
    if (deltaUnit === 'pp' && deltaAbs !== null && deltaAbs !== undefined) {
      const sign = deltaAbs > 0 ? '+' : '';
      return `${sign}${deltaAbs.toFixed(1)} pp`;
    }
    const sign = (deltaPct as number) > 0 ? '+' : '';
    return `${sign}${(deltaPct as number).toFixed(1)}%`;
  })();

  const titleNode = (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
      {icon}
      <span style={{ fontSize: 13, color: '#595959' }}>{title}</span>
    </span>
  );

  return (
    <Card size="small" bodyStyle={{ padding: '14px 18px' }}>
      <Statistic
        title={tooltip ? <Tooltip title={tooltip}>{titleNode}</Tooltip> : titleNode}
        value={value}
        precision={precision}
        suffix={suffix}
        valueStyle={{ fontSize: 24, fontWeight: 600 }}
      />
      <div
        data-testid="kpi-delta"
        style={{
          marginTop: 6,
          display: 'inline-flex',
          alignItems: 'center',
          gap: 4,
          color,
          fontSize: 12,
        }}
      >
        {showDelta && ARROW_ICON[dir]}
        <span>{deltaText}</span>
        <span style={{ color: '#8c8c8c', marginLeft: 4 }}>vs 직전 7일</span>
      </div>
    </Card>
  );
}
