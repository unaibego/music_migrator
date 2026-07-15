from pydantic_settings import BaseSettings, SettingsConfigDict


class AWSSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AWS_", env_file=".env", extra="ignore")

    bucket_name: str = ""
    profile: str | None = None
    region: str = "eu-north-1"
    dynamodb_table_name: str = "PlaylistSongs"


def get_aws_settings():
    return AWSSettings()
