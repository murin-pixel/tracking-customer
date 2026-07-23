from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        os.environ.setdefault(key, value)


def _path(value: str) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else PROJECT_ROOT / path


@dataclass(frozen=True)
class Settings:
    sheet_id: str
    sheet_gid: str
    sheet_tracking_column: str
    skyfrog_customer_code: str
    skyfrog_username: str
    skyfrog_password: str
    request_timeout_seconds: float
    concurrency: int
    request_delay_seconds: float
    output_dir: Path
    state_db_path: Path
    web_secret_key: str
    google_sheets_webhook_url: str
    google_sheets_webhook_secret: str
    kex_proof_pin: str = ""
    kex_proof_dir: Path = PROJECT_ROOT / "data" / "kex-proofs"
    public_base_url: str = ""
    interexpress_username: str = ""
    interexpress_password: str = ""
    supabase_url: str = ""
    supabase_secret_key: str = ""
    supabase_mapping_table: str = "shopee_order_mapping"

    @property
    def sheet_csv_url(self) -> str:
        return (
            f"https://docs.google.com/spreadsheets/d/{self.sheet_id}/export"
            f"?format=csv&gid={self.sheet_gid}"
        )

    @classmethod
    def from_env(cls, *, require_credentials: bool = True) -> "Settings":
        load_dotenv(PROJECT_ROOT / ".env")
        settings = cls(
            sheet_id=os.environ.get("SHEET_ID", "").strip(),
            sheet_gid=os.environ.get("SHEET_GID", "976262542").strip(),
            sheet_tracking_column=os.environ.get("SHEET_TRACKING_COLUMN", "G").strip(),
            skyfrog_customer_code=os.environ.get("SKYFROG_CUSTOMER_CODE", "").strip(),
            skyfrog_username=os.environ.get("SKYFROG_USERNAME", "").strip(),
            skyfrog_password=os.environ.get("SKYFROG_PASSWORD", ""),
            request_timeout_seconds=float(os.environ.get("REQUEST_TIMEOUT_SECONDS", "30")),
            concurrency=max(1, int(os.environ.get("CONCURRENCY", "2"))),
            request_delay_seconds=max(
                0.0, float(os.environ.get("REQUEST_DELAY_SECONDS", "0.10"))
            ),
            output_dir=_path(os.environ.get("OUTPUT_DIR", "./outputs")),
            state_db_path=_path(
                os.environ.get("STATE_DB_PATH", "./data/status-cache.sqlite")
            ),
            web_secret_key=os.environ.get("WEB_SECRET_KEY", "").strip(),
            google_sheets_webhook_url=os.environ.get(
                "GOOGLE_SHEETS_WEBHOOK_URL", ""
            ).strip(),
            google_sheets_webhook_secret=os.environ.get(
                "GOOGLE_SHEETS_WEBHOOK_SECRET", ""
            ).strip(),
            kex_proof_pin=os.environ.get("KEX_PROOF_PIN", "").strip(),
            kex_proof_dir=_path(os.environ.get("KEX_PROOF_DIR", "./data/kex-proofs")),
            public_base_url=os.environ.get("PUBLIC_BASE_URL", "").strip().rstrip("/"),
            interexpress_username=os.environ.get("INTEREXPRESS_USERNAME", "").strip(),
            interexpress_password=os.environ.get("INTEREXPRESS_PASSWORD", ""),
            supabase_url=os.environ.get("SUPABASE_URL", "").strip().rstrip("/"),
            supabase_secret_key=os.environ.get("SUPABASE_SECRET_KEY", "").strip(),
            supabase_mapping_table=os.environ.get(
                "SUPABASE_MAPPING_TABLE", "shopee_order_mapping"
            ).strip(),
        )
        if require_credentials:
            missing = []
            if not settings.skyfrog_customer_code:
                missing.append("SKYFROG_CUSTOMER_CODE")
            if not settings.skyfrog_username:
                missing.append("SKYFROG_USERNAME")
            if not settings.skyfrog_password:
                missing.append("SKYFROG_PASSWORD")
            if missing:
                raise ValueError("กรุณาตั้งค่า " + ", ".join(missing) + " ในไฟล์ .env")
        return settings
