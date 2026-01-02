import json
import os
import boto3
from typing import Any


from src.core.settings.aws_service import get_aws_settings


aws_settings = get_aws_settings()



class BlobHandler():
    bucket_name: str
    s3 : Any

    def __init__(self):
        profile = aws_settings.profile  
        if profile:
            session = boto3.Session(profile_name=profile)
        else:
            session = boto3.Session()  # en Lambda usará el role automáticamente
        self.s3 = session.client("s3")
        self.bucket_name = aws_settings.bucket_name

    def get_tidal_tokens(self, user_name : str) -> dict:
        orig_path = f"tokens/tidal/tidal_token_{user_name}.json"
        try:
            response = self.s3.get_object(Bucket=self.bucket_name, Key=orig_path)
            body = response["Body"].read()
            return json.loads(body)
        except Exception as e:
            raise e
            raise ValueError(f"El nombre de usuario no es el correcto")
        

    def put_tidal_token_dict(self, user_name : str, token_dict: dict):
        try: 
            self.s3.put_object(
                Bucket=self.bucket_name,
                Key=f"tokens/tidal/tidal_token_{user_name}.json",
                Body=json.dumps(token_dict).encode("utf-8"),
                ContentType="application/json"
            )
        except:
            raise Exception(f"Ha ocurrido un error guardando token de {user_name}")

if __name__ == "__main__" :
    blob_handler = BlobHandler()

    june_token = blob_handler.get_tidal_tokens("June")

    blob_handler.put_tidal_token_dict("Prueba", june_token)