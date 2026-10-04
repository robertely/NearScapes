from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="NEARSCAPES_", case_sensitive=False)

    database_url: str = "postgresql+psycopg://nearscapes:nearscapes@postgres:5432/nearscapes"
    redis_url: str = "redis://redis:6379/0"
    queue_mode: str = "dramatiq"
    storage_root: Path = Path("/data")
    model_cache: Path = Path("/models")
    log_level: str = "INFO"
    max_upload_bytes: int = 2 * 1024 * 1024 * 1024
    waveform_points: int = 4000
    pcm_sample_rate: int = 8000

    auto_analyze_uploads: bool = True
    auto_audacity_export: bool = True
    accelerator: str = "cpu"
    inference_url: str = "http://host.docker.internal:8787"
    inference_timeout_seconds: float = 3600.0
    birdnet_confidence_default: float = 0.60

    slate_frequency_hz: float = 1000.0
    slate_frequency_tolerance_hz: float = 30.0
    slate_expected_duration_seconds: float = 2.0
    slate_duration_tolerance_seconds: float = 0.5
    slate_min_tone_to_guard_db: float = 25.0
    slate_min_tone_to_broadband_ratio: float = 0.03
    slate_max_pair_gap_seconds: float = 25.0
    slate_opening_pair_max_start_seconds: float = 60.0
    slate_transcription_post_seconds: float = 30.0
    slate_transcription_sample_rate: int = 16000
    slate_transcription_model: str = "mlx-community/whisper-large-v3-turbo"
    slate_transcription_language: str = "en"

    audacity_command_timeout_seconds: float = 30.0
    audacity_start_timeout_seconds: float = 30.0


@lru_cache
def get_settings() -> Settings:
    return Settings()
