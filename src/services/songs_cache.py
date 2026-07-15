from __future__ import annotations

import json
from decimal import Decimal
from typing import Any, Dict, List

import boto3

from src.core.settings.aws_service import get_aws_settings

aws_settings = get_aws_settings()
SONGS_CACHE_KEY = "songs.json"


def _json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        if value % 1 == 0:
            return int(value)
        return float(value)
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


class SongsCacheExporter:
    def __init__(self) -> None:
        profile = aws_settings.profile
        session_kwargs: Dict[str, Any] = {"region_name": aws_settings.region}
        if profile:
            session = boto3.Session(profile_name=profile, **session_kwargs)
        else:
            session = boto3.Session(**session_kwargs)
        self.s3 = session.client("s3")
        self.front_bucket_name = aws_settings.front_bucket_name

    def export_songs(self, songs: List[Dict[str, Any]]) -> None:
        if not self.front_bucket_name:
            print("AWS_FRONT_BUCKET_NAME no configurado; se omite export de songs.json")
            return

        payload = {
            "ok": True,
            "count": len(songs),
            "songs": songs,
            "updatedAt": _now_iso(),
        }
        body = json.dumps(payload, default=_json_default, ensure_ascii=False).encode("utf-8")

        self.s3.put_object(
            Bucket=self.front_bucket_name,
            Key=SONGS_CACHE_KEY,
            Body=body,
            ContentType="application/json; charset=utf-8",
            CacheControl="no-cache, no-store, must-revalidate",
        )
        print(f"Exportadas {len(songs)} canciones a s3://{self.front_bucket_name}/{SONGS_CACHE_KEY}")


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()
