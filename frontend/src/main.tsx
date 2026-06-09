import React from 'react';
import ReactDOM from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ConfigProvider } from 'antd';
import koKR from 'antd/locale/ko_KR';
import App from './App';
import 'antd/dist/reset.css';
import './index.css';

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 60_000,
      refetchOnWindowFocus: false,
      retry: 1,
    },
  },
});

// 포털 배포 시 BASE_URL = "/signalforge/" (Vite 가 base 옵션으로 주입).
// BrowserRouter 의 basename 은 trailing slash 없이 줘야 하므로 한 번 정리.
const baseUrl = import.meta.env.BASE_URL || '/';
const routerBasename = baseUrl.replace(/\/$/, '') || '/';

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <ConfigProvider locale={koKR} theme={{ token: { colorPrimary: '#1677ff' } }}>
      <QueryClientProvider client={queryClient}>
        <BrowserRouter basename={routerBasename}>
          <App />
        </BrowserRouter>
      </QueryClientProvider>
    </ConfigProvider>
  </React.StrictMode>,
);
