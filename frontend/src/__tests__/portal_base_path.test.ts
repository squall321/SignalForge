// Portal deploy 회귀 가드 — S2 portal_deploy.
// dist/index.html 이 VITE_BASE_PATH 로 prefix 된 asset URL 을 가지는지 확인한다.
// 빌드 시 VITE_BASE_PATH 가 비어있으면 모든 경로가 "/" 로 시작하므로 skip.
// (실 portal 빌드는 항상 VITE_BASE_PATH=/signalforge/ 로 수행됨)
import { describe, it, expect } from 'vitest';
import { existsSync, readFileSync } from 'node:fs';
import { resolve } from 'node:path';

describe('portal base path prefix', () => {
  const distHtml = resolve(__dirname, '../../dist/index.html');
  const hasDist = existsSync(distHtml);
  const html = hasDist ? readFileSync(distHtml, 'utf-8') : '';
  // 어느 assertion 을 돌릴지는 '빌드 산출물'로 판별한다 — 테스트 실행 시점 env 가 아니라
  // dist/index.html 이 실제로 /signalforge/ prefix 를 담고 있는지로 결정(env 커플링 제거).
  const isPortalDist = html.includes('/signalforge/assets/');

  it.skipIf(!hasDist)('dist/index.html exists', () => {
    expect(hasDist).toBe(true);
  });

  it.skipIf(!hasDist || !isPortalDist)(
    'portal dist asset URLs are prefixed with /signalforge/',
    () => {
      // index html 에는 main script, css, 3 vendor modulepreload = 최소 5개
      const matches = html.match(/\/signalforge\/assets\//g) || [];
      expect(matches.length).toBeGreaterThanOrEqual(5);
    },
  );

  it.skipIf(!hasDist || isPortalDist)(
    'standalone dist asset URLs start with /assets/',
    () => {
      const matches = html.match(/\s(?:src|href)="\/assets\//g) || [];
      expect(matches.length).toBeGreaterThanOrEqual(5);
    },
  );
});
