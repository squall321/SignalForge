// P4.2 R6 트랙 D — 카드 별 즐겨찾기 토글 버튼.
// AntD Button(icon-only) + StarFilled/StarOutlined. Card extra 슬롯에 주입한다.
import { Button, Tooltip } from 'antd';
import { StarFilled, StarOutlined } from '@ant-design/icons';
import { useFavoritesStore } from '../../stores/favoritesStore';

interface Props {
  cardId: string;
  size?: 'small' | 'middle' | 'large';
}

export default function FavoriteButton({ cardId, size = 'small' }: Props) {
  const active = useFavoritesStore((s) => s.ids.has(cardId));
  const toggle = useFavoritesStore((s) => s.toggle);
  return (
    <Tooltip title={active ? '즐겨찾기 해제' : '즐겨찾기 추가'}>
      <Button
        type="text"
        size={size}
        aria-label={active ? 'remove-favorite' : 'add-favorite'}
        data-testid={`favorite-${cardId}`}
        icon={
          active ? (
            <StarFilled style={{ color: '#faad14' }} />
          ) : (
            <StarOutlined style={{ color: '#bfbfbf' }} />
          )
        }
        onClick={() => toggle(cardId)}
      />
    </Tooltip>
  );
}
