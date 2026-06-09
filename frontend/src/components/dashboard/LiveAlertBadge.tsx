// P5 R6 트랙 A — 헤더 우측 라이브 알림 배지.
// WS 연결 + recent 폴백, 클릭 시 /alerts 이동.
import { useEffect, useState } from 'react';
import { Badge, Button } from 'antd';
import { BellOutlined } from '@ant-design/icons';
import { useNavigate } from 'react-router-dom';
import { fetchRecent, openAlertSocket, type AlertEvent } from '../../services/alertsApi';

export default function LiveAlertBadge() {
  const navigate = useNavigate();
  const [count, setCount] = useState(0);

  useEffect(() => {
    let alive = true;

    // 초기 카운트: 최근 24h 발화 수.
    fetchRecent(100)
      .then((events) => {
        if (!alive) return;
        const cutoff = Date.now() - 24 * 60 * 60 * 1000;
        const recent = events.filter((e: AlertEvent) => {
          const t = new Date(e.fired_at).getTime();
          return Number.isFinite(t) && t >= cutoff;
        });
        setCount(recent.length);
      })
      .catch(() => {
        // 백엔드 미가동 — 0 유지.
      });

    // WS: 새 이벤트마다 +1.
    let ws: WebSocket | null = null;
    try {
      ws = openAlertSocket((msg) => {
        if (msg.type === 'alert') setCount((c) => c + 1);
      });
    } catch {
      // WS 미지원 환경 — 무시.
    }

    return () => {
      alive = false;
      ws?.close();
    };
  }, []);

  return (
    <Badge count={count} size="small" overflowCount={99} data-testid="alert-badge">
      <Button
        type="text"
        icon={<BellOutlined style={{ fontSize: 18 }} />}
        onClick={() => navigate('/alerts')}
        aria-label="알림으로 이동"
      />
    </Badge>
  );
}
