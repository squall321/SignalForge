import { useMemo } from 'react';
import {
  Drawer,
  Statistic,
  Row,
  Col,
  Tag,
  Empty,
  Skeleton,
  Typography,
  List,
  Space,
  Divider,
} from 'antd';
import { useQuery } from '@tanstack/react-query';
import { fetchKeywordDetail } from '../../services/deepApi';
import type {
  KeywordDetailResponse,
  KeywordDetailSample,
} from '../../types/deep';

const { Text, Link: AntLink } = Typography;

interface Props {
  keyword: string | null;
  lang?: string | null;
  open: boolean;
  onClose: () => void;
  /** 연결 키워드 클릭 시 호출 (Drawer 재오픈). */
  onSelectKeyword?: (kw: string, lang?: string | null) => void;
}

/**
 * UX R2 트랙 A — KeywordNetworkCard node 클릭 → 상세 Drawer.
 *
 *  - 상단: stats Statistic 4개 (total / sentiment_avg / top product / top platform)
 *  - 중단: 5 샘플 (negative 우선, 최신순)
 *  - 하단: 연결 키워드 Tag cloud + 카테고리 분포 mini-bar
 */
export default function KeywordDetailDrawer({
  keyword,
  lang,
  open,
  onClose,
  onSelectKeyword,
}: Props) {
  const { data, isLoading } = useQuery<KeywordDetailResponse>({
    queryKey: ['deep', 'keyword-detail', keyword, lang ?? ''],
    queryFn: () =>
      fetchKeywordDetail({
        keyword: keyword as string,
        lang: lang ?? undefined,
        period_days: 7,
        limit: 5,
      }),
    enabled: open && !!keyword,
    staleTime: 5 * 60_000,
  });

  const empty =
    !isLoading &&
    data !== undefined &&
    data.stats.total_count === 0 &&
    data.samples.length === 0 &&
    data.related_keywords.length === 0;

  const maxCat = useMemo(() => {
    if (!data?.categories?.length) return 0;
    return Math.max(...data.categories.map((c) => c.count));
  }, [data]);

  const topProductLabel = data?.stats.top_products?.[0]
    ? data.stats.top_products[0].name_ko || data.stats.top_products[0].code
    : '-';
  const topPlatformLabel = data?.stats.top_platforms?.[0]
    ? data.stats.top_platforms[0].name || data.stats.top_platforms[0].code
    : '-';

  const isMobile =
    typeof window !== 'undefined' && window.innerWidth < 768;

  return (
    <Drawer
      title={keyword ? `키워드 상세 · ${keyword}` : '키워드 상세'}
      placement="right"
      open={open}
      onClose={onClose}
      width={isMobile ? '100%' : 480}
      destroyOnClose
    >
      {isLoading || !data ? (
        <Skeleton active paragraph={{ rows: 6 }} />
      ) : empty ? (
        <Empty description="해당 키워드 데이터 없음" />
      ) : (
        <div data-testid="keyword-detail-content">
          <Row gutter={[8, 8]} style={{ marginBottom: 12 }}>
            <Col span={12}>
              <Statistic
                title="VoC 총 (7일)"
                value={data.stats.total_count}
                precision={0}
              />
            </Col>
            <Col span={12}>
              <Statistic
                title="감성 평균"
                value={data.stats.sentiment_avg}
                precision={2}
                valueStyle={{
                  color:
                    data.stats.sentiment_avg < 0 ? '#cf1322' : '#1677ff',
                }}
              />
            </Col>
            <Col span={12}>
              <Statistic title="Top 제품" value={topProductLabel} />
            </Col>
            <Col span={12}>
              <Statistic title="Top 플랫폼" value={topPlatformLabel} />
            </Col>
          </Row>

          <Divider style={{ margin: '8px 0' }} />

          <Text strong style={{ fontSize: 12 }}>
            샘플 VoC ({data.samples.length})
          </Text>
          {data.samples.length === 0 ? (
            <Empty
              image={Empty.PRESENTED_IMAGE_SIMPLE}
              description="샘플 없음"
            />
          ) : (
            <List<KeywordDetailSample>
              size="small"
              dataSource={data.samples}
              renderItem={(it) => {
                const sev = it.sentiment_label;
                const color =
                  sev === 'negative'
                    ? 'red'
                    : sev === 'positive'
                      ? 'blue'
                      : 'default';
                return (
                  <List.Item style={{ display: 'block', padding: '6px 0' }}>
                    <Space size={4} wrap>
                      <Tag color={color} style={{ fontSize: 10 }}>
                        {sev ?? 'neutral'}
                      </Tag>
                      {it.product && (
                        <Tag color="geekblue" style={{ fontSize: 10 }}>
                          {it.product}
                        </Tag>
                      )}
                      {it.platform && (
                        <Tag color="purple" style={{ fontSize: 10 }}>
                          {it.platform}
                        </Tag>
                      )}
                    </Space>
                    <div style={{ marginTop: 4, fontSize: 12, lineHeight: 1.4 }}>
                      {it.url ? (
                        <AntLink
                          href={it.url}
                          target="_blank"
                          rel="noopener noreferrer"
                        >
                          {it.content_preview}
                        </AntLink>
                      ) : (
                        <Text>{it.content_preview}</Text>
                      )}
                    </div>
                  </List.Item>
                );
              }}
            />
          )}

          <Divider style={{ margin: '8px 0' }} />

          <Text strong style={{ fontSize: 12 }}>
            연결 키워드 (top {data.related_keywords.length})
          </Text>
          <div style={{ marginTop: 6 }} data-testid="related-tag-cloud">
            {data.related_keywords.length === 0 ? (
              <Text type="secondary" style={{ fontSize: 11 }}>
                연결 키워드 없음
              </Text>
            ) : (
              data.related_keywords.map((r) => (
                <Tag
                  key={r.keyword}
                  color="blue"
                  style={{ cursor: 'pointer', marginBottom: 4 }}
                  onClick={() =>
                    onSelectKeyword
                      ? onSelectKeyword(r.keyword, r.lang)
                      : undefined
                  }
                >
                  {r.keyword} · {r.cooccur_count}
                </Tag>
              ))
            )}
          </div>

          {data.categories?.length > 0 && (
            <>
              <Divider style={{ margin: '12px 0 8px' }} />
              <Text strong style={{ fontSize: 12 }}>
                카테고리 분포
              </Text>
              <div style={{ marginTop: 6 }}>
                {data.categories.map((c) => {
                  const pct = maxCat > 0 ? (c.count / maxCat) * 100 : 0;
                  return (
                    <div
                      key={c.category}
                      style={{ marginBottom: 4, fontSize: 11 }}
                    >
                      <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                        <span>{c.category}</span>
                        <span style={{ color: '#888' }}>{c.count}</span>
                      </div>
                      <div
                        style={{
                          height: 6,
                          background: '#f0f0f0',
                          borderRadius: 3,
                        }}
                      >
                        <div
                          style={{
                            height: '100%',
                            width: `${pct}%`,
                            background: '#1677ff',
                            borderRadius: 3,
                          }}
                        />
                      </div>
                    </div>
                  );
                })}
              </div>
            </>
          )}
        </div>
      )}
    </Drawer>
  );
}
