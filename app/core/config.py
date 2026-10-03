from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    app_name: str = "EV Fleet Charging Management"
    api_v1_prefix: str = "/api/v1"

    mongodb_uri: str = "mongodb://localhost:27017"
    mongodb_db: str = "ev_fleet"

    redis_url: str = "redis://localhost:6379/0"

    kafka_bootstrap_servers: str = "localhost:9092"
    kafka_client_id: str = "ev-fleet-api"
    kafka_telemetry_topic: str = "vehicle.telemetry.v1"
    kafka_alerts_topic: str = "vehicle.alerts.v1"
    kafka_consumer_group: str = "ev-fleet-api"
    kafka_security_protocol: str = "PLAINTEXT"
    kafka_sasl_mechanism: str | None = None
    kafka_username: str | None = None
    kafka_password: str | None = None

    reserve_range_km: float = Field(default=15, ge=0, le=200)
    charge_soon_margin_km: float = Field(default=15, ge=0, le=200)
    charging_deadline_buffer_minutes: int = Field(default=20, ge=0, le=240)
    scheduler_slot_minutes: int = Field(default=1, ge=1, le=60)
    charging_efficiency: float = Field(default=0.92, gt=0, le=1)

    @property
    def kafka_config(self) -> dict[str, str]:
        config = {
            "bootstrap_servers": self.kafka_bootstrap_servers,
            "client_id": self.kafka_client_id,
            "security_protocol": self.kafka_security_protocol,
        }

        if self.kafka_username and self.kafka_password:
            config["sasl_plain_username"] = self.kafka_username
            config["sasl_plain_password"] = self.kafka_password

        if self.kafka_sasl_mechanism:
            config["sasl_mechanism"] = self.kafka_sasl_mechanism

        return config


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
