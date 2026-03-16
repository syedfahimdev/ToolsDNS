"""
config.py — Configuration management for ToolsDNS.

Loads settings from environment variables and .env file.
All configuration is centralized here so every other module
can import from this single source of truth.

ToolsDNS uses ~/.tooldns as its home directory for:
    - .env (configuration)
    - tooldns.db (database)
    - tooldns.log (log file)

The home directory is created by `tooldns install`.

Environment Variables:
    TOOLDNS_HOME: Path to ToolsDNS home directory (default: ~/.tooldns)
    TOOLDNS_API_KEY: API key for authenticating requests
    TOOLDNS_HOST: Server host (default: 0.0.0.0)
    TOOLDNS_PORT: Server port (default: 8787)
    TOOLDNS_EMBEDDING_MODEL: Sentence-transformer model name (default: all-MiniLM-L6-v2)
    TOOLDNS_DB_PATH: Path to SQLite database (default: ~/.tooldns/tooldns.db)
    TOOLDNS_REFRESH_INTERVAL: Auto-refresh interval in minutes (default: 15, 0 = disabled)
    TOOLDNS_LOG_LEVEL: Logging level (default: INFO)
    TOOLDNS_APP_NAME: Branding name (default: ToolsDNS)
    TOOLDNS_GITHUB_URL: GitHub repo URL shown in UI/footer
    TOOLDNS_CONTACT_EMAIL: Contact email shown on pricing page
"""

import os
import logging
from pathlib import Path
from pydantic import ConfigDict
from pydantic_settings import BaseSettings
from dotenv import load_dotenv

# Determine the ToolsDNS home directory
TOOLDNS_HOME = Path(os.environ.get(
    "TOOLDNS_HOME",
    os.path.expanduser("~/.tooldns")
))

# Load .env from home directory first, then project dir as fallback
if (TOOLDNS_HOME / ".env").exists():
    load_dotenv(TOOLDNS_HOME / ".env")
else:
    load_dotenv(Path(__file__).parent.parent / ".env")


class Settings(BaseSettings):
    """
    Application settings loaded from environment variables.

    Uses pydantic-settings for type validation and defaults.
    All settings can be overridden via environment variables
    prefixed with TOOLDNS_.
    """

    home: str = str(TOOLDNS_HOME)
    api_key: str = "td_dev_key"
    host: str = "0.0.0.0"
    port: int = 8787
    embedding_model: str = "all-MiniLM-L6-v2"
    db_path: str = str(TOOLDNS_HOME / "tooldns.db")
    refresh_interval: int = 15
    log_level: str = "INFO"
    model: str = ""  # LLM model name for cost calc (e.g. claude-sonnet-4-6)
    webhook_url: str = ""  # POST health alerts here (Slack/Discord/custom)
    webhook_secret: str = ""  # Added as X-ToolsDNS-Secret header if set
    public_url: str = ""  # Public HTTPS URL (e.g. https://api.toolsdns.com) — used in connect snippets
    # Branding — override these in ~/.tooldns/.env to white-label
    app_name: str = "ToolsDNS"
    app_tagline: str = "DNS for AI Tools"
    github_url: str = "https://github.com/syedfahimdev/ToolsDNS"
    contact_email: str = "hello@toolsdns.com"

    model_config = ConfigDict(env_prefix="TOOLDNS_")


# Singleton settings instance — import this from anywhere
settings = Settings()


def setup_logging() -> logging.Logger:
    """
    Configure and return the application logger.

    Sets up a logger named 'tooldns' with the configured log level.
    Outputs to both console and a log file in the home directory.

    Returns:
        logging.Logger: Configured logger instance.
    """
    logger = logging.getLogger("tooldns")
    logger.setLevel(getattr(logging, settings.log_level.upper(), logging.INFO))

    if not logger.handlers:
        # Console handler
        console = logging.StreamHandler()
        console.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%H:%M:%S"
        ))
        logger.addHandler(console)

        # File handler (only if home dir exists)
        log_path = Path(settings.home) / "tooldns.log"
        if log_path.parent.exists():
            file_handler = logging.FileHandler(str(log_path))
            file_handler.setFormatter(logging.Formatter(
                "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
            ))
            logger.addHandler(file_handler)

    return logger


logger = setup_logging()
