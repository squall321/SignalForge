// SignalForge frontend 정적 서버 — /signalforge/ sub-path 정합 + SPA fallback.
// base=/signalforge/ 로 빌드된 dist 를 서빙한다. 포털이 접두어를 strip 하든(→ /assets)
// 안 하든(→ /signalforge/assets) 모두 정상 동작하도록, 요청 경로에서 /signalforge 접두어를
// 있으면 벗겨낸 뒤 dist 에서 찾고, 파일이 없으면 index.html 로 폴백한다(BrowserRouter).
const http = require('http');
const fs = require('fs');
const path = require('path');

const DIST = process.env.WEB_DIST || '/opt/web/dist';
const PORT = parseInt(process.env.SF_WEB_PORT || '17370', 10);
const HOST = process.env.SF_WEB_HOST || '0.0.0.0';

const MIME = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'application/javascript; charset=utf-8',
  '.mjs': 'application/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.svg': 'image/svg+xml',
  '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
  '.gif': 'image/gif', '.ico': 'image/x-icon', '.webp': 'image/webp',
  '.woff': 'font/woff', '.woff2': 'font/woff2', '.ttf': 'font/ttf',
  '.map': 'application/json; charset=utf-8', '.txt': 'text/plain; charset=utf-8',
};

function send(res, status, body, type) {
  res.writeHead(status, { 'Content-Type': type || 'text/plain; charset=utf-8', 'Content-Length': Buffer.byteLength(body) });
  res.end(body);
}

const server = http.createServer((req, res) => {
  try {
    let p = decodeURIComponent((req.url || '/').split('?')[0]);
    // /signalforge 접두어 제거 (포털이 strip 안 하는 경우 대비)
    p = p.replace(/^\/signalforge(\/|$)/, '/');
    // 디렉토리 traversal 방지
    let rel = path.normalize(p).replace(/^(\.\.[/\\])+/, '').replace(/^\/+/, '');
    let file = path.join(DIST, rel);

    if (!file.startsWith(DIST)) return send(res, 403, 'forbidden');

    fs.stat(file, (err, st) => {
      if (!err && st.isFile()) {
        const ext = path.extname(file).toLowerCase();
        res.writeHead(200, { 'Content-Type': MIME[ext] || 'application/octet-stream', 'Content-Length': st.size });
        fs.createReadStream(file).pipe(res);
      } else {
        // SPA fallback → index.html
        const idx = path.join(DIST, 'index.html');
        fs.readFile(idx, (e2, buf) => {
          if (e2) return send(res, 404, 'not found');
          send(res, 200, buf, MIME['.html']);
        });
      }
    });
  } catch (e) {
    send(res, 500, 'server error');
  }
});

server.listen(PORT, HOST, () => {
  process.stderr.write(`SignalForge frontend serving ${DIST} on ${HOST}:${PORT} (/signalforge sub-path 정합)\n`);
});
