from pydantic_settings import BaseSettings, SettingsConfigDict


class AWSSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AWS_")
    
    bucket_name: str = ""
    profile : str | None = None



def get_aws_settings():
    return AWSSettings()