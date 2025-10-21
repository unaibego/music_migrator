from pydantic_settings import BaseSettings, SettingsConfigDict


class YoutubeSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="YOUTUBE_")
    url: str = ""
    client_id : str = ""
    client_secret : str = ""
    key: str  = ""


def get_youtube_settings():
    return YoutubeSettings()