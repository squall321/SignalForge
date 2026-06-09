import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import path from 'node:path';
// SignalForge Frontend Vite config
// - Dev server on 17370 (5173 점유 케이스 회피)
// - Proxies /api -> http://127.0.0.1:18000 (FastAPI backend)
// - '@' alias -> ./src
//
// 포털 배포 (S2 portal_deploy):
//   기본은 base="/" 로 standalone. HWAX 포털 뒤에서는
//   VITE_BASE_PATH=/signalforge/ 를 export 한 뒤 빌드하면
//   assets/router 모두 그 prefix 아래로 들어간다. preview 에서도
//   동일 prefix 로 정적 서빙된다.
//
// 빌드 청크 전략 (트랙 B 성능 안정화):
//   main bundle 1MB 이하, gzip 300KB 이하 목표.
//   대형 라이브러리는 vendor-* 로 분리하여 페이지 lazy 와 함께
//   route 전환 시 필요한 청크만 받게 한다.
//
//   - vendor-react      : react / react-dom / react-router-dom
//   - vendor-antd       : antd / @ant-design/icons (UI)
//   - vendor-charts     : echarts / echarts-for-react (대시보드/Temporal/Community)
//   - vendor-cytoscape  : cytoscape / cose-bilkent (KG 전용)
//   - vendor-maps       : react-simple-maps / d3-scale (Geo 전용)
//   - vendor-utils      : zustand / axios / @tanstack/react-query / dayjs
//
//   페이지 단위 청크는 React.lazy 에 의해 자동 분리 (manualChunks 와 별개).
var BASE = process.env.VITE_BASE_PATH || '/';
export default defineConfig({
    base: BASE,
    plugins: [react()],
    resolve: {
        alias: {
            '@': path.resolve(__dirname, './src'),
        },
    },
    server: {
        host: '0.0.0.0',
        port: 17370,
        strictPort: true,
        proxy: {
            '/api': {
                target: 'http://127.0.0.1:18000',
                changeOrigin: true,
            },
        },
    },
    preview: {
        host: '0.0.0.0',
        port: 17370,
        strictPort: true,
        proxy: {
            '/api': {
                target: 'http://127.0.0.1:18000',
                changeOrigin: true,
            },
        },
    },
    build: {
        // 청크 사이즈 경고는 800KB 부터 (1MB 목표라 여유)
        chunkSizeWarningLimit: 800,
        sourcemap: false,
        rollupOptions: {
            output: {
                manualChunks: {
                    'vendor-react': ['react', 'react-dom', 'react-router-dom'],
                    'vendor-antd': ['antd', '@ant-design/icons'],
                    'vendor-charts': ['echarts', 'echarts-for-react'],
                    'vendor-cytoscape': ['cytoscape', 'cytoscape-cose-bilkent'],
                    'vendor-maps': ['react-simple-maps', 'd3-scale'],
                    'vendor-utils': ['zustand', 'axios', '@tanstack/react-query', 'dayjs'],
                },
            },
        },
    },
});
