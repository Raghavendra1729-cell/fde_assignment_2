from dataclasses import dataclass
from pathlib import Path
import os


def _as_bool(value: str, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


@dataclass(frozen=True)
class PipelineConfig:
    pack_root: Path
    project_root: Path
    dispatch_api_url: str
    max_retries: int
    retry_base_seconds: float
    max_data_age_days: int
    page_size: int
    start_mock_api: bool
    log_level: str

    @classmethod
    def from_env(cls, pack_root: Path, project_root: Path):
        return cls(
            pack_root=pack_root,
            project_root=project_root,
            dispatch_api_url=os.getenv(
                "DISPATCH_API_URL",
                "http://127.0.0.1:8000",
            ),
            max_retries=int(os.getenv("MAX_RETRIES", "3")),
            retry_base_seconds=float(os.getenv("RETRY_BASE_SECONDS", "1")),
            max_data_age_days=int(os.getenv("MAX_DATA_AGE_DAYS", "60")),
            page_size=int(os.getenv("PAGE_SIZE", "200")),
            start_mock_api=_as_bool(os.getenv("START_MOCK_API"), True),
            log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
        )
