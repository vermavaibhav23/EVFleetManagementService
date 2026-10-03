from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )
    app_name: str = "EV Fleet Charging Management"
    api_v1_prefix: str = "/api/v1"
    mongodb_uri: str = "mongodb://localhost:27017"
    mongodb_db: str = "ev_fleet"
    kafka_enabled: bool = False
    kafka_bootstrap_servers: str = "localhost:9092"
    kafka_client_id: str = "ev-fleet-api"
    kafka_telemetry_topic: str = "vehicle.telemetry.v2"
    kafka_consumer_group: str = "ev-fleet-v2"
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
        return config


@lru_cache
def get_settings():
    return Settings()


settings = get_settings()
