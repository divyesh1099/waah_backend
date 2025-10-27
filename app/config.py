# app/config.py
import os
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    APP_ENV: str = "dev"
    APP_SECRET: str
    DB_URL: str
    JWT_ISS: str = "waah"
    JWT_EXP_MIN: int = 12 * 60
    TZ: str = "UTC"

    MEDIA_ROOT: str = "./media"      # directory on disk
    MEDIA_URL_BASE: str = "/media"   # URL path prefix

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

settings = Settings()
os.makedirs(settings.MEDIA_ROOT, exist_ok=True)
