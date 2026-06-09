import { Button, Card, Empty, Space, Spin, Tag, Typography, Alert } from 'antd';
import { BulbOutlined, LinkOutlined, RobotOutlined } from '@ant-design/icons';
import { useMutation } from '@tanstack/react-query';
import ReactMarkdown from 'react-markdown';
import api from '../../services/api';
import type {
  LLMNarrativeRequest,
  LLMNarrativeResponse,
} from '../../types/temporal';

const { Text, Paragraph } = Typography;

interface Props {
  request: LLMNarrativeRequest | null;
  disabled?: boolean;
}

async function fetchNarrative(
  body: LLMNarrativeRequest,
): Promise<LLMNarrativeResponse> {
  const { data } = await api.post<LLMNarrativeResponse>(
    '/analytics/llm-narrative',
    body,
  );
  return data;
}

/**
 * 차트 옆 사이드 패널 — LLM 한국어 narrative 표시
 *   - POST /api/v1/analytics/llm-narrative (ollama qwen2.5:7b)
 *   - react-markdown 으로 본문 렌더
 *   - citations 클릭 → source_url 새 창
 */
export default function LLMNarrativePanel({ request, disabled }: Props) {
  const mutation = useMutation<LLMNarrativeResponse, Error, LLMNarrativeRequest>({
    mutationFn: fetchNarrative,
  });

  const onAnalyze = () => {
    if (!request) return;
    mutation.mutate(request);
  };

  const errMsg =
    mutation.error instanceof Error ? mutation.error.message : '알 수 없는 오류';

  return (
    <Card
      title={
        <Space>
          <RobotOutlined style={{ color: '#722ed1' }} />
          <span>LLM 인사이트</span>
        </Space>
      }
      extra={
        <Button
          type="primary"
          icon={<BulbOutlined />}
          onClick={onAnalyze}
          disabled={disabled || !request || mutation.isPending}
          loading={mutation.isPending}
        >
          LLM 분석
        </Button>
      }
      bodyStyle={{ minHeight: 360 }}
    >
      {mutation.isPending && (
        <div style={{ textAlign: 'center', padding: '64px 0' }}>
          <Spin size="large" />
          <Paragraph type="secondary" style={{ marginTop: 12 }}>
            분석 중... (ollama qwen2.5:7b)
          </Paragraph>
        </div>
      )}

      {mutation.isError && !mutation.isPending && (
        <Alert
          type="error"
          showIcon
          message="LLM 호출 실패"
          description={errMsg}
          style={{ marginBottom: 12 }}
        />
      )}

      {!mutation.isPending && !mutation.data && !mutation.isError && (
        <Empty
          description={
            <Text type="secondary">
              우상단 "LLM 분석" 버튼을 눌러 한국어 narrative 를 생성하세요.
            </Text>
          }
          style={{ padding: '48px 0' }}
        />
      )}

      {mutation.data && (
        <div>
          <div className="md-body">
            <ReactMarkdown>{mutation.data.narrative}</ReactMarkdown>
          </div>

          {mutation.data.citations?.length > 0 && (
            <div style={{ marginTop: 16 }}>
              <Text strong>출처</Text>
              <Space direction="vertical" size={4} style={{ marginTop: 8, width: '100%' }}>
                {mutation.data.citations.map((c, i) => (
                  <a
                    key={`${c.source_url}-${i}`}
                    href={c.source_url}
                    target="_blank"
                    rel="noopener noreferrer"
                    style={{ display: 'block' }}
                  >
                    <LinkOutlined /> {c.title || c.source_url}
                  </a>
                ))}
              </Space>
            </div>
          )}

          {mutation.data.model && (
            <div style={{ marginTop: 16, textAlign: 'right' }}>
              <Tag color="purple">model: {mutation.data.model}</Tag>
              {mutation.data.generated_at && (
                <Text type="secondary" style={{ fontSize: 12 }}>
                  {mutation.data.generated_at}
                </Text>
              )}
            </div>
          )}
        </div>
      )}
    </Card>
  );
}
