from __future__ import annotations

from typing import Optional

from src.core.settings.tidal_sync_service import get_tidal_sync_settings
from src.db.dynamo_handler import DynamoHandler
from src.services.tidal_client import TidalUserClient
from src.services.tidal_library import TidalLibrary


class PlaylistTrackDeleter:
    """Elimina una canción de la playlist compartida en ambas cuentas Tidal."""

    def __init__(
        self,
        tidal_a: TidalLibrary,
        tidal_b: TidalLibrary,
        *,
        playlist_name: Optional[str] = None,
        dynamo_handler: Optional[DynamoHandler] = None,
    ) -> None:
        self.tidal_a = tidal_a
        self.tidal_b = tidal_b
        self.dynamo_handler = dynamo_handler or DynamoHandler()
        sync_settings = get_tidal_sync_settings()
        self.playlist_name = playlist_name or sync_settings.playlist_name

    def delete_track(self, song_id: str) -> dict:
        if not self.playlist_name:
            raise RuntimeError("Define TIDAL_SYNC_PLAYLIST_NAME.")

        song = self.dynamo_handler.get_song_by_id(str(song_id))
        if not song:
            raise ValueError(f"Canción {song_id} no encontrada en DynamoDB.")

        track_id = song.get("trackId")
        if track_id is None:
            raise ValueError("La canción no tiene trackId.")

        track_id_int = int(track_id)
        title = song.get("title") or f"Track {track_id}"

        pl_a = self.tidal_a.get_playlist_by_title(self.playlist_name)
        pl_b = self.tidal_b.get_playlist_by_title(self.playlist_name)
        if pl_a is None or pl_b is None:
            raise RuntimeError(
                f"La playlist '{self.playlist_name}' no existe en ambas cuentas."
            )

        user_a = self.tidal_a.client.user_name
        user_b = self.tidal_b.client.user_name

        ok_a = self.tidal_a.remove_tracks_by_ids(pl_a, [track_id_int])
        ok_b = self.tidal_b.remove_tracks_by_ids(pl_b, [track_id_int])

        if not ok_a or not ok_b:
            failed = []
            if not ok_a:
                failed.append(user_a)
            if not ok_b:
                failed.append(user_b)
            raise RuntimeError(
                f"No se pudo eliminar '{title}' en: {', '.join(failed)}"
            )

        self.dynamo_handler.delete_song_by_id(str(song_id))

        return {
            "songId": str(song_id),
            "trackId": str(track_id),
            "title": title,
            "removedFrom": [user_a, user_b],
        }
