import axios from 'axios';

// API base URL:
//   1) VITE_API_BASE_URL 명시되면 그대로 사용 (예: 절대 URL 또는 커스텀 prefix)
//   2) 아니면 import.meta.env.BASE_URL ('/' 또는 '/signalforge/') 아래의 'api/v1'
//      → standalone: "/api/v1"
//      → 포털 뒤  : "/signalforge/api/v1"
//      포털 nginx 는 /signalforge/ 를 strip 해 backend 의 /api/v1 로 포워딩한다.
const baseUrl = import.meta.env.BASE_URL || '/';
const defaultBase = `${baseUrl}api/v1`.replace(/\/{2,}/g, '/');
const baseURL = import.meta.env.VITE_API_BASE_URL || defaultBase;

export const api = axios.create({
  baseURL,
  timeout: 30_000,
  headers: { 'Content-Type': 'application/json' },
  // 포털 뒤에서는 HttpOnly sf_session 쿠키가 same-origin /signalforge/api/v1 호출에 실려야 한다.
  withCredentials: true,
});

api.interceptors.response.use(
  (res) => res,
  (err) => {
    // 단순 콘솔 로깅 — 추후 message.error 로 교체
    console.error('[api]', err?.response?.status, err?.config?.url, err?.message);
    // 포털 뒤(BASE_URL !== '/')에서 세션 만료/누락(401)이면 포털 root 로 보내 타일에서 재진입.
    // standalone(BASE_URL === '/')은 그대로 둔다.
    if (err?.response?.status === 401 && import.meta.env.BASE_URL !== '/') {
      window.location.href = '/';
    }
    return Promise.reject(err);
  },
);

export default api;
