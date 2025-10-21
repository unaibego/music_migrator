from pydantic_settings import BaseSettings, SettingsConfigDict


class SpotifySettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SPOTIFY_")
    url: str = ""
    client_id : str = ""
    key: str  = ""


def get_spotify_settings():
    return SpotifySettings()