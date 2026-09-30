"""Single validated configuration layer. Fails fast at startup on unsafe production settings."""
import os
from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "BUILDGUARD API"
    # dev/test: relaxed. demo: public showcase (seeded users allowed). production: strict checks below.
    env: Literal["dev", "test", "demo", "production"] = "dev"
    enable_docs: bool | None = None            # default: on except production
    log_level: str = "INFO"
    log_format: Literal["auto", "json", "console"] = "auto"   # auto = readable console lines in dev/test, JSON otherwise

    # database
    database_url: str = "sqlite:///./buildguard.db"
    auto_migrate: bool = True                   # run Alembic upgrade head on startup
    db_pool_size: int = 5
    db_max_overflow: int = 5
    db_pool_timeout: int = 30
    db_pool_recycle: int = 1800
    db_statement_timeout_ms: int = 15000        # PostgreSQL only

    # auth
    jwt_secret: str = "dev-secret-change-me"
    jwt_alg: str = "HS256"
    jwt_issuer: str = "buildguard-api"
    jwt_audience: str = "buildguard-clients"
    access_token_minutes: int = 120
    refresh_token_days: int = 14
    password_iterations: int = 600_000          # PBKDF2-HMAC-SHA256 (OWASP 2023 guidance)
    login_max_failures: int = 5
    login_lock_minutes: int = 15
    allow_self_register: bool = True
    admin_email: str = ""                       # this address becomes admin when it registers

    # http
    cors_origins: str = "http://localhost:3000,http://localhost:5173"
    trust_proxy: bool = False                   # true only behind a proxy that appends X-Forwarded-For
    max_json_body_kb: int = 1024
    max_upload_mb: int = 10
    upload_dir: str = "./uploads"
    public_base_url: str = ""

    # rate limits (requests per minute per client IP, per bucket)
    rate_limit_default_per_min: int = 120
    rate_limit_login_per_min: int = 10
    rate_limit_register_per_min: int = 5
    rate_limit_upload_per_min: int = 30
    rate_limit_ai_per_min: int = 20
    rate_limit_public_per_min: int = 60

    # AI layer (all optional: with AI_PROVIDER=none everything runs on rules + verified retrieval)
    ai_provider: Literal["none", "anthropic", "openai", "gemini", "local"] = "none"
    ai_model: str = ""                          # empty = provider default (see providers/__init__.py); verify against your account
    ai_api_key: str = ""
    ai_base_url: str = ""                       # empty = provider default; set for Azure/OpenRouter/Ollama/vLLM etc.
    ai_temperature: float = 0.2
    ai_max_tokens: int = 700
    ai_timeout_ms: int = 30000                  # per HTTP attempt (read timeout)
    ai_connect_timeout_ms: int = 5000
    ai_total_timeout_ms: int = 60000            # hard cap across retries + tool rounds
    ai_max_retries: int = 2
    ai_fallback_provider: Literal["none", "anthropic", "openai", "gemini", "local"] = "none"
    ai_fallback_model: str = ""
    ai_fallback_api_key: str = ""
    ai_fallback_base_url: str = ""
    ai_embedding_provider: Literal["none", "openai", "gemini", "local"] = "none"    # Anthropic has no embeddings API
    ai_embedding_model: str = ""
    ai_embedding_dimensions: int = 0            # 0 = model default
    ai_embedding_api_key: str = ""              # empty = reuse AI_API_KEY
    ai_embedding_base_url: str = ""
    ai_max_requests_per_minute: int = 20        # per authenticated user
    ai_daily_token_budget: int = 150000         # per user per UTC day (input+output); 0 = unlimited
    ai_max_context_tokens: int = 6000           # budget for history + sources + tool results
    ai_max_input_chars: int = 2000
    ai_max_tool_rounds: int = 3
    ai_cache_ttl_s: int = 3600
    ai_price_in_per_1k: float = 0.0             # optional, for estimated_cost only
    ai_price_out_per_1k: float = 0.0
    ai_max_document_kb: int = 200

    redis_url: str = ""                         # empty = in-memory (fine for one process); set for multi-worker / multi-instance

    seed_demo: bool = False
    allow_demo_seed_in_production: bool = False

    model_config = SettingsConfigDict(
        # .env.local (git-ignored, created by `npm run bootstrap`) wins over .env. The test suite (ENV=test) never reads either file, so a developer's
        # real database, Redis and AI key can never leak into automated tests.
        env_file=None if os.environ.get("ENV") == "test" else (".env", ".env.local"), extra="ignore")

    @property
    def db_url(self) -> str:
        """Normalised SQLAlchemy URL. PostgreSQL always uses the explicit psycopg (v3) driver so behaviour never depends on library defaults."""
        u = self.database_url
        for prefix in ("postgres://", "postgresql://"):
            if u.startswith(prefix):
                return "postgresql+psycopg://" + u[len(prefix):]
        return u

    @property
    def cors_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def docs_enabled(self) -> bool:
        return self.enable_docs if self.enable_docs is not None else self.env != "production"

    @property
    def strict(self) -> bool:
        return self.env in ("demo", "production")

    @model_validator(mode="after")
    def _validate(self):
        problems = []
        if self.strict and (len(self.jwt_secret) < 32 or self.jwt_secret.startswith("dev-secret")):
            problems.append("JWT_SECRET must be a random string of at least 32 characters")
        if self.jwt_alg not in ("HS256", "HS384", "HS512"):
            problems.append("JWT_ALG must be HS256/HS384/HS512")
        if self.env == "production":
            if "*" in self.cors_list or not self.cors_list:
                problems.append("CORS_ORIGINS must list explicit origins in production (no '*')")
            if self.seed_demo and not self.allow_demo_seed_in_production:
                problems.append("SEED_DEMO creates well-known demo accounts; refuse in production")
            if self.password_iterations < 300_000:
                problems.append("PASSWORD_ITERATIONS too low for production")
        for prefix, prov, key in (("AI", self.ai_provider, self.ai_api_key), ("AI_FALLBACK", self.ai_fallback_provider, self.ai_fallback_api_key or self.ai_api_key)):
            if prov in ("anthropic", "openai", "gemini") and not key:
                problems.append(f"{prefix}_PROVIDER={prov} requires an API key")
        if self.ai_embedding_provider in ("openai", "gemini") and not (self.ai_embedding_api_key or self.ai_api_key):
            problems.append("AI_EMBEDDING_PROVIDER requires AI_EMBEDDING_API_KEY or AI_API_KEY")
        if not 0.0 <= self.ai_temperature <= 1.5 or self.ai_max_tokens < 1 or self.ai_timeout_ms < 500:
            problems.append("invalid AI_TEMPERATURE / AI_MAX_TOKENS / AI_TIMEOUT_MS")
        if self.db_pool_size < 1 or self.db_max_overflow < 0:
            problems.append("invalid DB pool settings")
        if problems:
            raise ValueError("Invalid configuration: " + "; ".join(problems))
        return self


settings = Settings()
