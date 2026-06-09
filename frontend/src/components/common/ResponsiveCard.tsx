// 트랙 D — 카드 height/padding 을 viewport 에 따라 자동 조정.
//
// 사용 예:
//   <ResponsiveCard title="..." minHeight={300} mobileHeight={220}>
//     <ReactECharts option={opt} style={{ height: chartHeight }} />
//   </ResponsiveCard>
//
// 차트 height 자체는 카드가 정하지 않고 자식이 결정한다 — body 의 height 만 조정.
// 자식은 useChartHeight() 로 동일한 분기를 받을 수 있다.

import type { ReactNode } from 'react';
import { Card } from 'antd';
import { useViewport } from '../../utils/useViewport';

interface Props {
  title: ReactNode;
  children: ReactNode;
  minHeight?: number;
  mobileHeight?: number;
  extra?: ReactNode;
}

// 카드 body height/padding 결정 — viewport.isMobile 시 mobileHeight 사용.
export function cardBodyStyle(
  isMobile: boolean,
  desktopH: number,
  mobileH: number,
): React.CSSProperties {
  return {
    height: isMobile ? mobileH : desktopH,
    padding: isMobile ? 8 : 12,
  };
}

// echarts height 권장값 — 카드 height 보다 약간 작게.
export function chartHeight(isMobile: boolean, desktopH: number, mobileH: number): number {
  return isMobile ? mobileH - 40 : desktopH - 60;
}

export default function ResponsiveCard({
  title,
  children,
  minHeight = 300,
  mobileHeight = 220,
  extra,
}: Props) {
  const vp = useViewport();
  return (
    <Card
      title={title}
      size="small"
      bodyStyle={cardBodyStyle(vp.isMobile, minHeight, mobileHeight)}
      extra={extra}
    >
      {children}
    </Card>
  );
}
