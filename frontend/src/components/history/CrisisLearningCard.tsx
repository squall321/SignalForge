// R10 트랙 B3 — Crisis Learning Card.
// 위기 사례 5종 (Note 7 / Fold 1 / S22 GoS / Z Flip 힌지 / S20 5G 가격)을
// "사건 학습 카드" 형식으로 보여 준다.
//   - timeline mini-line
//   - top keyword tag cloud
//   - 영향 평가 (total / neg_rate)
//   - 학습 인사이트 (정적 문구)
import { Alert, Card, Col, Empty, Row, Space, Spin, Statistic, Tag, Typography } from 'antd';
import { useQuery } from '@tanstack/react-query';
import ReactECharts from 'echarts-for-react';
import { crisisLineSeries, fetchCrisisCases } from '../../services/historyApi';
import { makeBaseOption, palette } from '../../utils/chartTheme';

const { Paragraph, Text } = Typography;

// 사례별 학습 인사이트 — 디자인 단계에서 정적 문구로 고정.
const LESSONS: Record<string, string> = {
  GN7:   '리튬이온 셀 설계·QA 회귀의 비용을 보여준 사례. 대규모 리콜 트리거.',
  GZF1:  '폴더블 디스플레이 초기 리뷰가 출시 일정에 미치는 영향. PR 전 매체 검증 강화.',
  GS22U: '하드웨어 마케팅 약속과 SW 제한의 괴리 — 신뢰 누적의 중요성.',
  GZFL3: '폴더블 양산 3세대에서도 힌지·주름 이슈가 잔존. 부품 표준화 가속 필요.',
  GS20:  '5G 프리미엄 가격 인상이 부정 SOV 를 가속, 가격 정책 커뮤니케이션 부족.',
};

export default function CrisisLearningCard() {
  const { data, isLoading, isError } = useQuery({
    queryKey: ['history-crisis'],
    queryFn: fetchCrisisCases,
    staleTime: 10 * 60_000,
  });

  return (
    <Card
      data-testid="crisis-learning-card"
      size="small"
      title="위기 사례 학습 카드 (5종)"
    >
      {isError && <Alert type="error" message="crisis 로드 실패" />}
      {isLoading && <Spin />}
      {data && data.cases.length === 0 && <Empty description="사례 없음" />}
      {data && (
        <Row gutter={[12, 12]}>
          {data.cases.map((c) => {
            const ser = crisisLineSeries(c);
            const base = makeBaseOption({ withDataZoom: false, withToolbox: false });
            const opt = {
              ...base,
              grid: { top: 16, right: 12, bottom: 22, left: 36 },
              xAxis: { type: 'category', data: ser.x, axisLabel: { fontSize: 8, rotate: 45 } },
              yAxis: { type: 'value' },
              series: [
                {
                  type: 'line',
                  smooth: true,
                  data: ser.y,
                  itemStyle: { color: palette.negative },
                  areaStyle: { opacity: 0.18 },
                },
              ],
            };
            return (
              <Col xs={24} md={12} xl={8} key={c.code}>
                <Card
                  size="small"
                  type="inner"
                  title={c.title}
                  data-testid={`crisis-learning-${c.code}`}
                  extra={<Tag color="red">{c.code}</Tag>}
                >
                  <Paragraph type="secondary" style={{ fontSize: 12, marginBottom: 6 }}>
                    {c.description}
                  </Paragraph>
                  <Space size={10} style={{ marginBottom: 4 }}>
                    <Statistic title="총 voc" value={c.total_voc} valueStyle={{ fontSize: 16 }} />
                    <Statistic
                      title="neg_rate"
                      value={(c.neg_rate * 100).toFixed(1)}
                      suffix="%"
                      valueStyle={{ fontSize: 16 }}
                    />
                  </Space>
                  {ser.x.length > 0 ? (
                    <ReactECharts option={opt} style={{ height: 120 }} notMerge lazyUpdate />
                  ) : (
                    <Empty description="기간 내 voc 없음" image={Empty.PRESENTED_IMAGE_SIMPLE} />
                  )}
                  <div style={{ marginTop: 4 }}>
                    <Text type="secondary" style={{ fontSize: 11 }}>Top 키워드: </Text>
                    <Space size={4} wrap>
                      {c.top_keywords.slice(0, 5).map((k) => (
                        <Tag key={k.keyword} color="volcano">
                          {k.keyword} ({k.count})
                        </Tag>
                      ))}
                    </Space>
                  </div>
                  <Paragraph
                    type="secondary"
                    style={{ fontSize: 11, marginTop: 6, marginBottom: 0 }}
                    data-testid={`crisis-lesson-${c.code}`}
                  >
                    학습: {LESSONS[c.code] ?? '사례 분석 데이터 부족.'}
                  </Paragraph>
                </Card>
              </Col>
            );
          })}
        </Row>
      )}
    </Card>
  );
}
