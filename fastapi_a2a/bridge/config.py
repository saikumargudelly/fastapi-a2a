"""
FastAPI Bridge — Configuration model for fastapi_a2a_config entity.
Supports environment variable overrides and full fastapi_a2a_config spec fields.
"""
from __future__ import annotations

from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class RegistryConfig:
    """Programmatic registry configuration (Priority 2 in discovery chain)."""

    def __init__(
        self,
        url: str | None = None,
        heartbeat_interval_seconds: int = 60,
        region: str | None = None,
        discovery_mode: Literal["explicit", "auto", "disabled"] = "auto",
    ):
        self.url = url
        self.heartbeat_interval_seconds = heartbeat_interval_seconds
        self.region = region
        self.discovery_mode = discovery_mode


class FastApiA2AConfig(BaseSettings):
    """
    Runtime configuration for the fastapi-a2a library.
    Values can be overridden via environment variables prefixed with A2A_.
    """
    model_config = SettingsConfigDict(env_prefix="A2A_", env_file=".env", extra="ignore")

    # Database — required at runtime; supply via A2A_DATABASE_URL env var or
    # by passing database_url= directly to FastApiA2A(...).
    database_url: str | None = Field(
        default=None,
        description="Async PostgreSQL connection URL. Set A2A_DATABASE_URL env var or pass database_url= to FastApiA2A.",
    )

    # Registry discovery (§17.8.1)
    registry_url: str | None = Field(None, description="Explicit registry URL (Priority 2)")
    discovery_mode: Literal["explicit", "auto", "disabled"] = "auto"
    dns_srv_timeout_ms: int = 2000
    well_known_timeout_ms: int = 3000
    fallback_to_public_registry: bool = True
    default_public_registry: str = "https://registry.fastapi-a2a.dev"

    # Card signing
    require_signed_cards: bool = False
    jwks_cache_max_age_seconds: int = 3600
    jwks_push_rotation_events: bool = True
    jwks_cooperative_crawl_respect: bool = True
    jwks_cdn_cache_bust_on_rotation: bool = True

    # Scan / sanitizer (§17.4, §18.1, §19.1)
    scan_mode: Literal["synchronous", "async"] = "async"
    scan_review_threshold: float = 0.5
    scan_auto_reject_threshold: float = 0.95
    sanitizer_enabled: bool = True
    sanitizer_report_threshold: float = 0.30
    sanitizer_auto_reject_threshold: float = 0.95
    sanitizer_max_field_length: int = 2048
    sanitizer_rules_version: str = "fastapi-a2a-sanitizer/0.6.0"
    sanitizer_surfaces: list[str] = Field(
        default=["card_serve", "crawler_ingest", "federation_sync", "llm_prompt", "extended_card", "card_history", "prompt_assembly", "audit_summary"]
    )

    # Policy cache (§18.8)
    policy_cache_enabled: bool = True
    policy_cache_max_entries: int = 10000
    policy_cache_ttl_allow_seconds: int = 300
    policy_cache_ttl_deny_seconds: int = 60
    policy_cache_invalidation_poll_ms: int = 100
    use_redis_pubsub: bool = False
    redis_url: str | None = None
    policy_eval_log_sample_rate: float = 0.01

    # Dual-write (§19.3)
    dual_write_enabled: bool = True
    dual_write_queue_type: Literal["kinesis", "sqs", "pubsub", "db_only"] = "db_only"
    dual_write_queue_url: str | None = None
    dual_write_target_regions: list[str] = Field(default=[])
    dual_write_sla_minutes: int = 15
    dual_write_retention_years: int = 7

    # Embedding migration (§18.6)
    embedding_migration_auto_cutover: bool = False
    embedding_migration_concurrency: int = 10

    # Compliance jobs (§18.5)
    compliance_job_cron: str = "0 2 * * *"
    compliance_retraction_on_violation: bool = True
    compliance_incident_auto_page: bool = True

    # Crawler / legal (§17.10)
    legal_contact_email: str | None = None
    crawler_policy_url: str | None = None
    takedown_audit_retention_years: int = 7
    removal_page_enabled: bool = True
    removal_verification_required: bool = True

    # SDK DX
    sdk_compatibility_matrix_url: str | None = None

    @field_validator("scan_auto_reject_threshold")
    @classmethod
    def auto_reject_must_exceed_review(cls, v: float, info) -> float:
        review = info.data.get("scan_review_threshold", 0.5)
        if v <= review:
            raise ValueError("scan_auto_reject_threshold must be > scan_review_threshold")
        return v
