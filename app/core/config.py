import os
from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )
    app_name: str = "EV Fleet Charging Scheduler"
    api_v1_prefix: str = "/api/v1"
    database_url: str = "postgresql://fleet:fleet@127.0.0.1:5432/fleet"
    kafka_bootstrap_servers: str = "localhost:9092"
    kafka_client_id: str = "ev-fleet-api"
    kafka_telemetry_topic: str = "vehicle.telemetry.v4"
    kafka_group_prefix: str = "fleet-interview-v4"
    kafka_partitions: int = 2
    kafka_replication_factor: int = 1
    telemetry_max_age_seconds: float = 120
    provider_api_url: str | None = None
    run_workers: bool = True
    telemetry_history_days: int = 3
    demo_idle_seconds: int = 300
    kafka_auto_create_topics: bool = False
    kafka_ssl_ca_pem: str | None = None
    kafka_ssl_cafile: str | None = None
    kafka_ssl_certfile: str | None = None
    kafka_ssl_keyfile: str | None = None
    database_ssl_cafile: str | None = None
    database_ssl_ca_pem: str | None = None
    gemini_api_key: SecretStr | None = None
    gemini_model: str = Field(
        default="gemini-2.5-flash-lite", pattern=r"^[a-zA-Z0-9._-]+$"
    )
    ai_requests_per_minute: int = Field(default=3, ge=1, le=60)
    ai_requests_per_day: int = Field(default=20, ge=1, le=1000)

    @property
    def provider_url(self):
        return (
            self.provider_api_url
            or f"http://127.0.0.1:{os.environ.get('PORT', '8000')}"
        )

    kafka_security_protocol: str = "PLAINTEXT"
    kafka_sasl_mechanism: str | None = None
    kafka_username: str | None = None
    kafka_password: str | None = None

    @property
    def kafka_config(self):
        config = dict(
            bootstrap_servers=self.kafka_bootstrap_servers,
            client_id=self.kafka_client_id,
            security_protocol=self.kafka_security_protocol,
        )
        if self.kafka_username and self.kafka_password:
            config.update(
                sasl_plain_username=self.kafka_username,
                sasl_plain_password=self.kafka_password,
            )
        if self.kafka_sasl_mechanism:
            config["sasl_mechanism"] = self.kafka_sasl_mechanism
        if self.kafka_security_protocol in ("SSL", "SASL_SSL"):
            import ssl

            context = ssl.create_default_context(
                cafile=self.kafka_ssl_cafile, cadata=self.kafka_ssl_ca_pem
            )
            if self.kafka_ssl_certfile and self.kafka_ssl_keyfile:
                context.load_cert_chain(self.kafka_ssl_certfile, self.kafka_ssl_keyfile)
            config["ssl_context"] = context
        return config


@lru_cache
def get_settings():
    return Settings()


settings = get_settings()
