"""Environment configuration; never serialize connection credentials to clients."""

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    database_url: str | None = None
    cookie_secure: bool = True
    session_hours: int = 8
    allowed_origins: tuple[str, ...] = (
        "http://127.0.0.1:5173",
        "http://localhost:5173",
        "http://127.0.0.1:8080",
    )

    @classmethod
    def from_environment(cls) -> Settings:
        environment = os.environ.get("APP_ENV", "development")
        secure = os.environ.get("COOKIE_SECURE", "true").lower() != "false"
        origins = tuple(
            item.strip()
            for item in os.environ.get(
                "ALLOWED_ORIGINS",
                "http://127.0.0.1:5173,http://localhost:5173,http://127.0.0.1:8080",
            ).split(",")
            if item.strip()
        )
        if environment == "production" and (
            not secure
            or not origins
            or any(not item.startswith("https://") or "*" in item for item in origins)
        ):
            raise ValueError("Production requires secure cookies and explicit HTTPS origins")
        return cls(
            database_url=os.environ.get("DATABASE_URL") or None,
            cookie_secure=secure,
            allowed_origins=origins,
        )
