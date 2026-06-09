// P4.2 R6 트랙 D — 대시보드 즐겨찾기 섹션.
// favoritesStore 의 id 리스트를 cardRegistry 와 join 해 카드 형태로 노출.
import { Card, Empty, List, Tag, Typography, Button } from 'antd';
import { StarFilled } from '@ant-design/icons';
import { useNavigate } from 'react-router-dom';
import { useFavoritesStore } from '../../stores/favoritesStore';
import { CARD_REGISTRY, lookupCardMeta } from './cardRegistry';

const { Text } = Typography;

export default function FavoritesSection() {
  const ids = useFavoritesStore((s) => Array.from(s.ids));
  const toggle = useFavoritesStore((s) => s.toggle);
  const navigate = useNavigate();

  const items = ids
    .map((id) => lookupCardMeta(id))
    .filter((m): m is NonNullable<typeof m> => Boolean(m));

  return (
    <Card
      title={
        <span>
          <StarFilled style={{ color: '#faad14', marginRight: 8 }} />
          즐겨찾기 ({items.length})
        </span>
      }
      size="small"
      data-testid="favorites-section"
    >
      {items.length === 0 ? (
        <Empty
          description={
            <Text type="secondary">
              카드의 ⭐ 별 아이콘을 눌러 즐겨찾기에 추가하세요. (총 {CARD_REGISTRY.length}개 카드)
            </Text>
          }
          image={Empty.PRESENTED_IMAGE_SIMPLE}
        />
      ) : (
        <List
          size="small"
          dataSource={items}
          renderItem={(meta) => (
            <List.Item
              data-testid={`favorite-item-${meta.id}`}
              actions={[
                <Button
                  key="goto"
                  type="link"
                  size="small"
                  onClick={() => navigate(meta.path)}
                >
                  바로가기
                </Button>,
                <Button
                  key="remove"
                  type="link"
                  size="small"
                  danger
                  onClick={() => toggle(meta.id)}
                >
                  해제
                </Button>,
              ]}
            >
              <List.Item.Meta
                title={meta.label}
                description={<Tag color="blue">{meta.area}</Tag>}
              />
            </List.Item>
          )}
        />
      )}
    </Card>
  );
}
