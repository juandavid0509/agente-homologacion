import os
from pathlib import Path

from dotenv import load_dotenv

# Cargar el .env del backend sin depender del directorio de ejecución.
BASE_DIR = Path(__file__).resolve().parents[2]
load_dotenv(BASE_DIR / ".env")


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


class Settings:
    """Configuración central del backend (leída desde .env)."""

    # --- Base de datos ---
    DB_USER: str = os.getenv("DB_USER", "root")
    DB_PASSWORD: str = os.getenv("DB_PASSWORD", "")
    DB_HOST: str = os.getenv("DB_HOST", "localhost")
    DB_PORT: str = os.getenv("DB_PORT", "3306")
    DB_NAME: str = os.getenv("DB_NAME", "homologacion_db")

    # --- LLM ---
    LLM_MODE: str = os.getenv("LLM_MODE", "ollama")
    OLLAMA_HOST: str = os.getenv("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
    OLLAMA_MODEL: str = os.getenv("OLLAMA_MODEL", "llama3.2:3b")
    OLLAMA_NUM_CTX: int = _int("OLLAMA_NUM_CTX", 8192)
    OLLAMA_NUM_PREDICT: int = _int("OLLAMA_NUM_PREDICT", 512)
    OLLAMA_TEMPERATURE: float = _float("OLLAMA_TEMPERATURE", 0.3)
    OLLAMA_TOP_P: float = _float("OLLAMA_TOP_P", 0.9)
    OLLAMA_REPEAT_PENALTY: float = _float("OLLAMA_REPEAT_PENALTY", 1.1)
    OLLAMA_KEEP_ALIVE: str = os.getenv("OLLAMA_KEEP_ALIVE", "10m")
    OLLAMA_TIMEOUT: float = _float("OLLAMA_TIMEOUT", 120.0)
    OLLAMA_CONNECT_TIMEOUT: float = _float("OLLAMA_CONNECT_TIMEOUT", 5.0)
    OLLAMA_RETRIES: int = _int("OLLAMA_RETRIES", 2)

    # --- Memoria conversacional ---
    HISTORY_MAX_MESSAGES: int = _int("HISTORY_MAX_MESSAGES", 20)
    HISTORY_MAX_CHARS: int = _int("HISTORY_MAX_CHARS", 12000)
    SUMMARY_TRIGGER_MESSAGES: int = _int("SUMMARY_TRIGGER_MESSAGES", 24)
    SUMMARY_KEEP_RECENT: int = _int("SUMMARY_KEEP_RECENT", 10)


settings = Settings()
