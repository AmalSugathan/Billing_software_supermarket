"""Environment configuration; never serialize connection credentials to clients."""

import os
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Settings:
    database_url: str | None = None
    cookie_secure: bool = True
    session_hours: int = 8
    ocr_encryption_key: str | None = None
    ocr_service_url: str | None = None
    ocr_timeout_seconds: int = 180
    ocr_provider: str = "gemini"
    gemini_api_key: str | None = field(default=None, repr=False)
    gemini_model: str = "gemini-3.5-flash"
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
        ocr_timeout = int(os.environ.get("OCR_TIMEOUT_SECONDS", "180"))
        if not 30 <= ocr_timeout <= 7200:
            raise ValueError("OCR timeout must be between 30 and 7200 seconds")
        provider = os.environ.get("OCR_PROVIDER", "gemini")
        if provider not in {"gemini", "paddle"}:
            raise ValueError("OCR_PROVIDER must be gemini or paddle")
        return cls(
            ocr_provider=provider,
            gemini_api_key=os.environ.get("GEMINI_API_KEY") or None,
            gemini_model=os.environ.get("GEMINI_MODEL", "gemini-3.5-flash"),
            database_url=os.environ.get("DATABASE_URL") or None,
            ocr_encryption_key=os.environ.get("OCR_ENCRYPTION_KEY") or None,
            ocr_service_url=os.environ.get("OCR_SERVICE_URL") or None,
            ocr_timeout_seconds=ocr_timeout,
            cookie_secure=secure,
            allowed_origins=origins,
        )
