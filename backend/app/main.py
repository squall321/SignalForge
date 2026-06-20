from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from contextlib import asynccontextmanager

from app.config import settings
from app.core.session import verify_session
from app.database import engine
from app.models import Product, Platform, VocRecord, VocCategory, CrawlJob  # noqa: F401 — import 순서 보장
from app.api import products, analytics, crawl_jobs, websocket, dashboard, kg, temporal, geo, community, insights, _internal, deep, alerts, shared, charts, portal_sso


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 시작 시: DB 연결 확인
    async with engine.connect() as conn:
        await conn.execute(__import__("sqlalchemy").text("SELECT 1"))
    yield
    # 종료 시: 엔진 정리
    await engine.dispose()


# @lat: app — FastAPI 앱 진입점. [[architecture#FastAPI Backend]] 참조.
app = FastAPI(
    title="SignalForge API",
    description="Samsung MobileExperience VOC Intelligence Platform",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

# CORS 미들웨어
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 라우터 등록
app.include_router(products.router, prefix="/api/v1")
app.include_router(analytics.router, prefix="/api/v1")
app.include_router(crawl_jobs.router, prefix="/api/v1")
app.include_router(dashboard.router, prefix="/api/v1")
app.include_router(kg.router, prefix="/api/v1")
app.include_router(temporal.router, prefix="/api/v1")
app.include_router(geo.router, prefix="/api/v1")
app.include_router(community.router, prefix="/api/v1")
app.include_router(insights.router, prefix="/api/v1")
app.include_router(deep.router, prefix="/api/v1")
app.include_router(alerts.router, prefix="/api/v1")
app.include_router(_internal.router, prefix="/api/v1")
app.include_router(shared.router, prefix="/api/v1")
app.include_router(charts.router, prefix="/api/v1")
app.include_router(portal_sso.router, prefix="/api/v1")
app.include_router(websocket.router)


# ── HWAX Portal SSO gate ──────────────────────────────────
# PORTAL_JWKS_URL 이 설정된 경우에만 /api/v1/* 요청에 유효한 sf_session 쿠키를 요구한다.
# 비어 있으면 완전한 pass-through (standalone 배포는 그대로). CORS 뒤에 등록해 CORS 가 감싸도록 한다.
# @app.middleware('http') 는 BaseHTTPMiddleware 라 websocket scope 를 보지 않으므로 WS 는 게이트되지 않는다.
_GATE_ALLOW = ("/api/v1/auth/portal-callback",)


@app.middleware("http")
async def portal_sso_gate(request: Request, call_next):
    if (
        settings.PORTAL_JWKS_URL
        and request.method != "OPTIONS"
        and request.url.path.startswith("/api/v1")
        and request.url.path not in _GATE_ALLOW
    ):
        raw = request.cookies.get("sf_session")
        if not raw or verify_session(raw) is None:
            return JSONResponse(status_code=401, content={"detail": "portal session required"})
    return await call_next(request)


# ── 헬스체크 ──────────────────────────────────────────────

@app.get("/health", tags=["system"])
async def health_check():
    return {"status": "ok", "service": "SignalForge API", "version": "1.0.0"}


@app.get("/api/v1/platforms", tags=["platforms"])
async def list_platforms():
    """크롤링 소스 플랫폼 목록 (간략)"""
    from app.database import AsyncSessionLocal
    from sqlalchemy import select
    from app.models import Platform

    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Platform).where(Platform.is_active.is_(True)))
        platforms = result.scalars().all()
        return [
            {"id": p.id, "code": p.code, "name": p.name, "region": p.region}
            for p in platforms
        ]
