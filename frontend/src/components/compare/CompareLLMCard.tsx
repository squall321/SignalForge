import { Card, Empty, Spin, Tag, Typography } from 'antd';
import { useQuery } from '@tanstack/react-query';
import { fetchCompareLLM } from '../../services/insightsApi';

const { Paragraph, Text } = Typography;

interface Props {
  products: string[];    // 2~4 제품 코드
  periodDays: number;
}

// 트랙 D — N개 제품의 LLM 비교 분석 narrative 카드.
// /api/v1/insights/compare-llm 결과를 그대로 보여주고
// tier_label / grounding_score 를 footer 에 표기.
export default function CompareLLMCard({ products, periodDays }: Props) {
  const enabled = products.length >= 2 && products.length <= 4;

  const { data, isLoading, isError } = useQuery({
    queryKey: ['compare-llm', products.slice().sort().join(','), periodDays],
    queryFn: () => fetchCompareLLM({ products, period_days: periodDays }),
    enabled,
    staleTime: 5 * 60_000,
    retry: 0,                   // LLM 호출은 비싸 — 재시도 X
  });

  const score = typeof data?.grounding_score === 'number' ? data.grounding_score : 0;
  const scoreColor = score >= 0.5 ? 'green' : score >= 0.3 ? 'gold' : 'red';

  return (
    <Card
      title={`AI 비교 분석 · ${products.join(' vs ')}`}
      size="small"
      data-testid="compare-llm-card"
      bodyStyle={{ minHeight: 220, padding: 16 }}
    >
      {!enabled ? (
        <Empty description="제품 2~4개를 선택하세요" />
      ) : isLoading ? (
        <Spin tip="LLM 분석 생성 중..." />
      ) : isError ? (
        <Empty description="LLM 호출 실패" />
      ) : !data || !data.narrative ? (
        <Empty description="LLM 키 미설정 또는 분석 불가 — narrative 없음" />
      ) : (
        <>
          <Paragraph
            data-testid="compare-llm-narrative"
            style={{ whiteSpace: 'pre-wrap', marginBottom: 12 }}
          >
            {data.narrative}
          </Paragraph>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
            <Tag color="blue" data-testid="compare-llm-tier">
              tier: {data.tier_label || 'n/a'}
            </Tag>
            <Tag color={scoreColor} data-testid="compare-llm-grounding">
              grounding: {score.toFixed(2)}
            </Tag>
            <Text type="secondary" style={{ fontSize: 11 }}>
              {periodDays}일 윈도우 · {new Date(data.generated_at).toLocaleString()}
            </Text>
          </div>
        </>
      )}
    </Card>
  );
}
