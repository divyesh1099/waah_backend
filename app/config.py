from pydantic_settings import BaseSettings, SettingsConfigDict
class Settings(BaseSettings):
    APP_ENV: str = "dev"
    APP_SECRET: str
    DB_URL: str
    JWT_ISS: str = "waah"
    JWT_EXP_MIN: int = 12*60
    TZ: str = "UTC"
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
settings = Settings()
