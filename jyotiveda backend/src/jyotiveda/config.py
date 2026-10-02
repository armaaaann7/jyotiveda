"""Twelve-factor configuration. Every setting can be overridden with a JYOTIVEDA_* environment variable."""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Environment(StrEnum):
    DEV = "dev"
    TEST = "test"
    STAGING = "staging"
    PROD = "prod"


class ServiceRole(StrEnum):
    """One container image, many roles. Each role mounts a subset of routers / workers."""

    ALL = "all"  # modular monolith: every router + control loop (dev, demo, edge-of-network pilots)
    GATEWAY = "gateway"  # BFF + WebSocket fan-out
    TWIN = "twin"
    FORECAST = "forecast"
    GRID_INTEL = "grid-intel"
    RELIABILITY = "reliability"
    FLEXIBILITY = "flexibility"
    OPTIMIZATION = "optimization"
    DISPATCH = "dispatch"
    COPILOT = "copilot"
    CONTROL_LOOP = "control-loop"  # background worker that runs the 15-min cycle


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="JYOTIVEDA_", env_file=".env", extra="ignore")

    env: Environment = Environment.DEV
    role: ServiceRole = ServiceRole.ALL
    service_name: str = "jyotiveda"
    log_level: str = "INFO"
    log_json: bool = True

    # --- data plane -------------------------------------------------------------------------
    database_url: str = "sqlite+aiosqlite:///./jyotiveda.db"
    redis_url: str | None = None
    event_bus: str = Field("memory", description="memory | kafka")
    kafka_bootstrap: str = "localhost:9092"
    kafka_client_id: str = "jyotiveda"
    mqtt_host: str = "localhost"
    mqtt_port: int = 1883

    # --- control loop ------------------------------------------------------------------------
    cell_id: str = "cell-a"
    fleet_shard: list[str] = Field(
        default_factory=list, description="transformer ids owned by this cell (empty = whole reference fleet)"
    )
    slot_minutes: int = 15
    horizon_slots: int = 96  # 24 h look-ahead for MPC
    forecast_horizon_slots: int = 288  # 72 h
    control_loop_enabled: bool = False
    control_loop_interval_s: int = 900
    auto_approve_max_kw: float = 60.0  # larger dispatches wait for a DISCOM operator

    # --- AI models ---------------------------------------------------------------------------
    forecast_backend: str = Field("auto", description="auto | chronos2 | gbm | seasonal")
    chronos_model_id: str = "amazon/chronos-2"
    chronos_device: str = "cpu"
    gnn_checkpoint: str | None = None
    rl_policy_onnx: str | None = None
    mpc_solver: str = "CLARABEL"
    mpc_scenarios: int = 7
    mpc_cvar_alpha: float = 0.9

    # --- LLM copilot -------------------------------------------------------------------------
    anthropic_api_key: SecretStr | None = None
    llm_model: str = "claude-opus-5-5"
    llm_fast_model: str = "claude-haiku-4-5-20251001"
    llm_max_tokens: int = 2048

    # --- security ----------------------------------------------------------------------------
    auth_mode: str = Field("dev", description="dev | oidc")
    oidc_issuer: str | None = None
    oidc_audience: str = "jyotiveda-api"
    oidc_jwks_url: str | None = None
    dev_jwt_secret: SecretStr = SecretStr("dev-only-change-me-dev-only-change-me")
    command_signing_key_path: str | None = None  # Ed25519 PEM; generated ephemeral in dev
    cors_origins: list[str] = ["http://localhost:3000", "http://127.0.0.1:3000"]

    # --- observability -----------------------------------------------------------------------
    otlp_endpoint: str | None = None
    metrics_enabled: bool = True

    @property
    def is_prod(self) -> bool:
        return self.env in (Environment.PROD, Environment.STAGING)


@lru_cache
def get_settings() -> Settings:
    return Settings()
