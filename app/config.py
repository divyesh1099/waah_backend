# app/config.py
import os
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    APP_ENV: str = "dev"
    APP_SECRET: str
    DB_URL: str
    JWT_ISS: str = "waah"
    JWT_EXP_MIN: int = 12 * 60
    TZ: str = "UTC"

    MEDIA_ROOT: str = "./media"        # can be overridden via env
    MEDIA_URL_BASE: str = "/media"      # leading slash is normalized below

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

settings = Settings()

# Normalize + ensure directory exists
_media_root_path = Path(settings.MEDIA_ROOT).resolve()
os.makedirs(_media_root_path, exist_ok=True)

# Keep attributes as originally defined; helpers for normalized values
def MEDIA_ROOT_PATH() -> Path:
    return _media_root_path

def MEDIA_URL_PREFIX() -> str:
    return "/" + settings.MEDIA_URL_BASE.strip("/")
