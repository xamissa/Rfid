"""Observation settings use the existing deployment CONFIGURATION mapping."""
from dataclasses import dataclass, fields
import math


@dataclass(frozen=True)
class ObservationConfig:
    inactivity_seconds: float = 1.0
    max_duration_seconds: float = 10.0
    active_key_limit: int = 10000
    batch_size: int = 100
    batch_flush_seconds: float = 1.0
    max_payload_bytes: int = 256 * 1024
    request_timeout_seconds: int = 10
    retry_initial_seconds: float = 1.0
    retry_max_seconds: float = 300.0
    queue_high_water: int = 100000

    @classmethod
    def load(cls, values):
        defaults = cls()
        result = {}
        for field in fields(cls):
            value = values.get('OBSERVATION_' + field.name.upper(), getattr(defaults, field.name))
            value = type(getattr(defaults, field.name))(value)
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f'Invalid OBSERVATION_{field.name.upper()}')
            result[field.name] = value
        config = cls(**result)
        if not 1 <= config.request_timeout_seconds <= 300:
            raise ValueError('Observation request timeout must be 1-300 seconds.')
        if config.retry_max_seconds < config.retry_initial_seconds:
            raise ValueError('Observation retry maximum is smaller than initial delay.')
        return config
