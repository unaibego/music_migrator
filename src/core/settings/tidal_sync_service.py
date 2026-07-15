from pydantic_settings import BaseSettings, SettingsConfigDict


class TidalSyncSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="TIDAL_SYNC_", env_file=".env", extra="ignore")

    playlist_name: str = ""


def get_tidal_sync_settings():
    return TidalSyncSettings()
