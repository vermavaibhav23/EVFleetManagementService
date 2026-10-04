import os
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )
    app_name: str = "EV Fleet Charging Management"
    api_v1_prefix: str = "/api/v1"
    database_url: str = "postgresql://fleet:fleet@127.0.0.1:5432/fleet"
    mongodb_uri: str = "mongodb://localhost:27017"
    mongodb_db: str = "ev_fleet"
    kafka_bootstrap_servers: str = "localhost:9092"
    kafka_client_id: str = "ev-fleet-api"
    kafka_telemetry_topic: str = "vehicle.telemetry.v3"
    kafka_planning_topic: str = "planning.requests.v3"
    kafka_group_prefix: str = "fleet-v3"
    kafka_partitions: int = 3
    kafka_replication_factor: int = 1
    telemetry_workers: int = 3
    telemetry_max_age_seconds: float = 120
    provider_api_url: str | None = None
    run_workers: bool = True
    mongo_history_days: int = 30

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

            config["ssl_context"] = ssl.create_default_context()
        return config


@lru_cache
def get_settings():
    return Settings()


settings = get_settings()
