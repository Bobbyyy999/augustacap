"""Settings, read from environment variables and an optional .env file."""
import os
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent


def _load_env():
    p = BASE / ".env"
    if not p.exists():
        return
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_env()


def env(key, default=""):
    return os.environ.get(key, default).strip()


def env_int(key, default):
    try:
        return int(env(key, str(default)))
    except ValueError:
        return default


class Settings:
    def __init__(self):
        self.reload()

    def reload(self):
        self.data_mode = env("DATA_MODE", "live").lower()
        self.host = env("HOST", "127.0.0.1")
        self.port = env_int("PORT", 8000)
        self.https = env("AUGUSTA_HTTPS", "0") == "1"
        self.db_path = env("AUGUSTA_DB", str(BASE / "data" / "augusta.db"))
        self.admin_user = env("ADMIN_USER", "admin")
        self.admin_password = env("ADMIN_PASSWORD")
        self.admin_email = env("ADMIN_EMAIL")
        self.twelvedata_key = env("TWELVEDATA_API_KEY")
        self.twelvedata_stream = env("TWELVEDATA_STREAM", "0") == "1"
        self.massive_key = env("MASSIVE_API_KEY")
        self.finnhub_key = env("FINNHUB_API_KEY")
        self.fred_key = env("FRED_API_KEY")
        self.quote_ttl = max(1, env_int("QUOTE_TTL", 5))
        self.alert_poll = max(3, env_int("ALERT_POLL_SECONDS", 15))
        self.smtp_host = env("SMTP_HOST")
        self.smtp_port = env_int("SMTP_PORT", 587)
        self.smtp_user = env("SMTP_USER")
        self.smtp_password = env("SMTP_PASSWORD")
        self.smtp_from = env("SMTP_FROM") or self.smtp_user
        self.discord_webhook = env("DISCORD_WEBHOOK_URL")
        self.slack_webhook = env("SLACK_WEBHOOK_URL")
        self.session_hours = env_int("SESSION_HOURS", 12)

    @property
    def demo(self):
        return self.data_mode == "demo"


settings = Settings()
