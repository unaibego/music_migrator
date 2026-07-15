from typing import Any, Optional, Dict
from datetime import datetime, timezone
import random

import boto3
from botocore.exceptions import ClientError

from src.core.settings.aws_service import get_aws_settings

aws_settings = get_aws_settings()


class DynamoHandler:
    dynamodb: Any
    table: Any

    def __init__(self):
        profile = aws_settings.profile
        session_kwargs: Dict[str, Any] = {"region_name": aws_settings.region}
        if profile:
            session = boto3.Session(profile_name=profile, **session_kwargs)
        else:
            session = boto3.Session(**session_kwargs)

        self.dynamodb = session.resource("dynamodb")
        self.table = self.dynamodb.Table(aws_settings.dynamodb_table_name)

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

    def _generate_id(self) -> str:
        ts_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        suffix = random.randint(0, 999)
        return str(ts_ms * 1000 + suffix)

    def _find_by_playlist_and_track(
        self, playlist_id: str, track_id: str
    ) -> Optional[Dict[str, Any]]:
        response = self.table.scan(
            FilterExpression="playlistId = :pid AND trackId = :tid",
            ExpressionAttributeValues={
                ":pid": playlist_id,
                ":tid": track_id,
            },
        )
        items = response.get("Items", [])
        return items[0] if items else None

    def record_added_track(
        self,
        playlist_id: str,
        track_id: str,
        title: str,
        inserted_by: Optional[str] = None,
        artist: Optional[str] = None,
    ) -> str:
        """
        Registra una canción añadida al sync.
        Si ya existe (misma playlist + track), actualiza updatedAt.
        """
        now = self._now_iso()
        existing = self._find_by_playlist_and_track(playlist_id, track_id)

        if existing:
            update_expr = "SET updatedAt = :updatedAt, title = :title"
            expr_values: Dict[str, Any] = {
                ":updatedAt": now,
                ":title": title,
            }
            if inserted_by is not None:
                update_expr += ", insertedBy = :insertedBy"
                expr_values[":insertedBy"] = inserted_by
            if artist:
                update_expr += ", artist = :artist"
                expr_values[":artist"] = artist

            self.table.update_item(
                Key={"id": existing["id"]},
                UpdateExpression=update_expr,
                ExpressionAttributeValues=expr_values,
            )
            return existing["id"]

        song_id = self._generate_id()
        item: Dict[str, Any] = {
            "id": song_id,
            "playlistId": playlist_id,
            "trackId": track_id,
            "title": title,
            "insertedAt": now,
            "updatedAt": now,
        }
        if inserted_by is not None:
            item["insertedBy"] = inserted_by
        if artist:
            item["artist"] = artist

        self.table.put_item(
            Item=item,
            ConditionExpression="attribute_not_exists(#id)",
            ExpressionAttributeNames={"#id": "id"},
        )
        return song_id

    def import_track_if_missing(
        self,
        playlist_id: str,
        track_id: str,
        title: str,
        artist: Optional[str] = None,
    ) -> tuple[str, bool]:
        """Importa una canción con insertedBy vacío. Devuelve (id, created)."""
        existing = self._find_by_playlist_and_track(playlist_id, track_id)
        if existing:
            return existing["id"], False

        song_id = self.record_added_track(
            playlist_id=playlist_id,
            track_id=track_id,
            title=title,
            inserted_by=None,
            artist=artist,
        )
        return song_id, True

    def update_inserted_by(self, song_id: str, inserted_by: Optional[str]) -> None:
        now = self._now_iso()
        if inserted_by is None:
            self.table.update_item(
                Key={"id": song_id},
                UpdateExpression="REMOVE insertedBy SET updatedAt = :updatedAt",
                ExpressionAttributeValues={":updatedAt": now},
            )
            return

        self.table.update_item(
            Key={"id": song_id},
            UpdateExpression="SET insertedBy = :insertedBy, updatedAt = :updatedAt",
            ExpressionAttributeValues={
                ":insertedBy": inserted_by,
                ":updatedAt": now,
            },
        )

    def put_song(
        self,
        playlist_id: str,
        track_id: str,
        title: str,
        inserted_by: Optional[str] = None,
        artist: Optional[str] = None,
        inserted_at: Optional[str] = None,
        extra: Optional[Dict[str, Any]] = None,
        prevent_overwrite: bool = True,
    ) -> str:
        now = inserted_at or self._now_iso()
        song_id = self._generate_id()

        item: Dict[str, Any] = {
            "id": song_id,
            "playlistId": playlist_id,
            "trackId": track_id,
            "title": title,
            "insertedAt": now,
            "updatedAt": now,
        }
        if inserted_by is not None:
            item["insertedBy"] = inserted_by

        if artist:
            item["artist"] = artist
        if extra:
            item.update(extra)

        try:
            if prevent_overwrite:
                self.table.put_item(
                    Item=item,
                    ConditionExpression="attribute_not_exists(#id)",
                    ExpressionAttributeNames={"#id": "id"},
                )
            else:
                self.table.put_item(Item=item)

            return song_id

        except ClientError as e:
            if e.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
                song_id = self._generate_id()
                item["id"] = song_id
                self.table.put_item(
                    Item=item,
                    ConditionExpression="attribute_not_exists(#id)",
                    ExpressionAttributeNames={"#id": "id"},
                )
                return song_id

            raise Exception(f"Error insertando canción en DynamoDB: {e}") from e

    def get_song_by_id(self, song_id: str) -> Optional[Dict[str, Any]]:
        try:
            response = self.table.get_item(Key={"id": song_id})
            return response.get("Item")
        except ClientError as e:
            raise Exception(f"Error leyendo canción: {e}") from e

    def list_playlist_songs(self, playlist_id: str) -> list[Dict[str, Any]]:
        items: list[Dict[str, Any]] = []

        try:
            response = self.table.scan(
                FilterExpression="playlistId = :pid",
                ExpressionAttributeValues={":pid": playlist_id},
            )
            items.extend(response.get("Items", []))

            while "LastEvaluatedKey" in response:
                response = self.table.scan(
                    FilterExpression="playlistId = :pid",
                    ExpressionAttributeValues={":pid": playlist_id},
                    ExclusiveStartKey=response["LastEvaluatedKey"],
                )
                items.extend(response.get("Items", []))

            return items
        except ClientError as e:
            raise Exception(f"Error listando canciones de la playlist: {e}") from e

    def list_all_songs(self) -> list[Dict[str, Any]]:
        items: list[Dict[str, Any]] = []

        try:
            response = self.table.scan()
            items.extend(response.get("Items", []))

            while "LastEvaluatedKey" in response:
                response = self.table.scan(
                    ExclusiveStartKey=response["LastEvaluatedKey"],
                )
                items.extend(response.get("Items", []))

            items.sort(key=lambda s: s.get("insertedAt") or "", reverse=True)
            return items
        except ClientError as e:
            raise Exception(f"Error listando canciones: {e}") from e


if __name__ == "__main__":
    dynamo_handler = DynamoHandler()

    song_id = dynamo_handler.put_song(
        playlist_id="123",
        track_id="456",
        title="pepe botella",
        artist="Artista X",
        inserted_by="June",
    )

    print("ID generado:", song_id)

    song = dynamo_handler.get_song_by_id(song_id)
    print("Canción:", song)

    songs = dynamo_handler.list_playlist_songs("123")
    print("Total canciones en playlist:", len(songs))
