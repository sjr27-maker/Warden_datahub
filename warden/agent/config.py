"""Environment-driven settings, validated once at import."""

import os

from pydantic import BaseModel, Field, field_validator


class Settings(BaseModel):
    datahub_gms_url: str = Field(default="http://localhost:8080")
    datahub_gms_token: str = Field(default="")
    mutation_tools_enabled: bool = Field(default=True)

    llm_backend: str = Field(default="ollama")
    anthropic_api_key: str = Field(default="")
    gemini_api_key: str = Field(default="")

    coverage_threshold: float = Field(default=0.6, ge=0.0, le=1.0)

    @field_validator("datahub_gms_url")
    @classmethod
    def _no_trailing_slash(cls, v: str) -> str:
        return v.rstrip("/")

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            datahub_gms_url=os.environ.get("DATAHUB_GMS_URL", "http://localhost:8080"),
            datahub_gms_token=os.environ.get("DATAHUB_GMS_TOKEN", ""),
            mutation_tools_enabled=os.environ.get("TOOLS_IS_MUTATION_ENABLED", "true").lower()
            == "true",
            llm_backend=os.environ.get("WARDEN_LLM_BACKEND", "ollama"),
            anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY", ""),
            gemini_api_key=os.environ.get("GEMINI_API_KEY", ""),
            coverage_threshold=float(os.environ.get("WARDEN_COVERAGE_THRESHOLD", "0.6")),
        )


settings = Settings.from_env()
