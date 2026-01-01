from pydantic_settings import BaseSettings, SettingsConfigDict


class AWSSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AWS_")
    
    bucket_name: str = ""
    client_id : str = ""
    client_secret : str = ""
    key: str  = ""


def get_aws_settings():
    return AWSSettings()