"""
Importa todas las canciones de la playlist configurada en Tidal a DynamoDB.

Las canciones nuevas se guardan sin insertedBy (campo vacío / null).
Las que ya existen en la BBDD no se modifican.

Uso:
    python -m src.import_playlist_to_db
"""

from src.core.settings.tidal_sync_service import get_tidal_sync_settings
from src.db.dynamo_handler import DynamoHandler
from src.services.songs_cache import SongsCacheExporter
from src.services.tidal_client import TidalUserClient
from src.services.tidal_library import TidalLibrary


def import_playlist_to_db(user_name: str = "Unai") -> None:
    sync_settings = get_tidal_sync_settings()
    playlist_name = sync_settings.playlist_name
    if not playlist_name:
        raise RuntimeError(
            "Define TIDAL_SYNC_PLAYLIST_NAME en .env con el nombre exacto de la playlist."
        )

    tidal = TidalUserClient(user_name=user_name)
    tidal.authenticate()
    lib = TidalLibrary(tidal)

    playlist = lib.get_playlist_by_title(playlist_name)
    if playlist is None:
        raise RuntimeError(f"La playlist '{playlist_name}' no existe en la cuenta {user_name}.")

    playlist_id = str(getattr(playlist, "id", ""))
    tracks = lib.list_playlist_tracks_map(playlist)

    dynamo = DynamoHandler()
    created = 0
    skipped = 0

    print(f"Importando playlist '{playlist_name}' (id={playlist_id})")
    print(f"Canciones encontradas en Tidal: {len(tracks)}\n")

    for track_id, meta in sorted(tracks.items()):
        title = meta.get("title") or f"Track {track_id}"
        artist = meta.get("artist") or None
        _, is_new = dynamo.import_track_if_missing(
            playlist_id=playlist_id,
            track_id=str(track_id),
            title=title,
            artist=artist,
        )
        if is_new:
            created += 1
            print(f"  + {title} — {artist or 'Artista desconocido'}")
        else:
            skipped += 1

    songs = dynamo.list_all_songs()
    SongsCacheExporter().export_songs(songs)

    print(f"\nImportación completada.")
    print(f"  Nuevas en DynamoDB: {created}")
    print(f"  Ya existían: {skipped}")
    print(f"  Total en BBDD: {len(songs)}")


if __name__ == "__main__":
    import_playlist_to_db()
