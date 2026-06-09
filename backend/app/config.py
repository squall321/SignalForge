import os

from pydantic_settings import BaseSettings
from pydantic import field_validator, model_validator
from typing import List


class Settings(BaseSettings):
    # 앱
    APP_ENV: str = "development"
    DEBUG: bool = True
    LOG_LEVEL: str = "INFO"

    # 데이터베이스
    DATABASE_URL: str

    # Redis
    REDIS_URL: str = "redis://localhost:6379/0"

    # Anthropic Claude
    ANTHROPIC_API_KEY: str = ""

    # 번역
    DEEPL_API_KEY: str = ""
    LIBRE_TRANSLATE_URL: str = ""

    # Reddit
    REDDIT_CLIENT_ID: str = ""
    REDDIT_CLIENT_SECRET: str = ""
    REDDIT_USER_AGENT: str = "SignalForge/1.0"

    # Twitter
    TWITTER_USERNAME: str = ""
    TWITTER_PASSWORD: str = ""

    # 보안
    API_KEY: str = "change-me"
    CORS_ORIGINS: str = "http://localhost:3000,http://localhost:5173"

    # P4 트랙 A — 실시간 알림
    SLACK_WEBHOOK_URL: str = ""              # 비어 있으면 SlackChannel 은 dry-run
    SLACK_CHANNEL: str = ""                  # 선택. Slack incoming webhook 의 채널 override
    WS_PING_INTERVAL_SEC: int = 30           # alerts WebSocket ping 주기
    ALERT_COOLDOWN_DEFAULT_SEC: int = 900    # rule.cooldown_sec 의 기본값 (DB seed 와 일치)

    @field_validator("CORS_ORIGINS", mode="before")
    @classmethod
    def parse_cors(cls, v: str) -> str:
        return v

    @model_validator(mode="after")
    def _fallback_slack_webhook(self) -> "Settings":
        """SLACK_WEBHOOK_URL 이 비어 있으면 ALERT_WEBHOOK_URL 을 폴백으로 사용.

        운영 정책: 사용자는 root .env 의 ALERT_WEBHOOK_URL 한 줄만 입력하면
        crawler 의 dispatcher (ALERT_WEBHOOK_URL 직접 읽음) 와 backend 의
        SlackChannel (이 settings 사용) 양쪽이 동시에 활성화된다.

        이미 SLACK_WEBHOOK_URL 이 명시되면 그 값 우선 — 두 시스템 분리 운영 가능.
        """
        if not self.SLACK_WEBHOOK_URL:
            fallback = (os.getenv("ALERT_WEBHOOK_URL", "") or "").strip()
            if fallback:
                # field 가 frozen 이 아니라 직접 대입 가능 (BaseSettings 기본 동작).
                object.__setattr__(self, "SLACK_WEBHOOK_URL", fallback)
        return self

    @property
    def cors_origins_list(self) -> List[str]:
        return [origin.strip() for origin in self.CORS_ORIGINS.split(",")]

    class Config:
        env_file = ".env"
        case_sensitive = True
        extra = "ignore"


settings = Settings()
