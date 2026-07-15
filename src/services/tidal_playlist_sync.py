from __future__ import annotations

from typing import Any, Dict, List, Optional, Set

from src.db.dynamo_handler import DynamoHandler
from src.core.settings.tidal_sync_service import get_tidal_sync_settings
from .tidal_library import TidalLibrary
from src.utils.utils import prompt_yn


class TidalPlaylistsSynchronizer:
    """
    TidalPlaylistsSynchronizer — Sincroniza playlists entre dos cuentas TIDAL.

    Depende de:
      - Dos instancias autenticadas de TidalLibrary (user A y user B).

    Flujo (modo `run()`):
      1) Lista las playlists de ambos usuarios.
      2) Calcula los nombres de playlists que existen en las dos cuentas.
      3) Para cada playlist común:
           - Pregunta si quieres sincronizarla (si `ask_per_playlist=True`).
           - Obtiene los IDs de pistas de ambas playlists.
           - Calcula la unión (A ∪ B).
           - Añade a A las pistas que solo están en B.
           - Añade a B las pistas que solo están en A.

    Resultado:
      Tras la sincronización, ambas playlists tendrán el mismo conjunto de canciones
      (orden de inserción: se añaden al final las nuevas).

    Parámetros:
      - avoid_duplicates: bool (por defecto True) → delega en TidalLibrary.add_tracks_by_ids
      - ask_per_playlist: bool (por defecto True) → pedir confirmación por playlist
    """

    def __init__(
        self,
        tidal_a: TidalLibrary,
        tidal_b: TidalLibrary,
        *,
        avoid_duplicates: bool = True,
        ask_per_playlist: bool = True,
        dynamo_handler: Optional[DynamoHandler] = None,
        playlist_name: Optional[str] = None,
    ) -> None:
        self.tidal_a = tidal_a
        self.tidal_b = tidal_b
        self.avoid_duplicates = avoid_duplicates
        self.ask_per_playlist = ask_per_playlist
        self.dynamo_handler = dynamo_handler
        sync_settings = get_tidal_sync_settings()
        self.playlist_name = playlist_name or sync_settings.playlist_name or None

    def run(self) -> None:
        if self.playlist_name:
            titles = [self.playlist_name]
            print(f"Sincronizando playlist configurada: '{self.playlist_name}'\n")
        else:
            titles = self._get_common_playlists()
            if not titles:
                print("No se encontraron playlists con el mismo nombre en ambas cuentas.")
                return
            print(f"Playlists comunes encontradas: {len(titles)}\n")

        for title in sorted(titles):
            print(f"→ Playlist común: '{title}'")
            if self.ask_per_playlist:
                ok = prompt_yn(
                    f"¿Sincronizar la playlist '{title}' entre ambas cuentas?",
                    default_yes=True,
                )
                if not ok:
                    continue

            self.sync_single_playlist(title)

    def sync_single_playlist(self, playlist_title: str) -> None:
        print(f"\n==== Sincronizando playlist: '{playlist_title}' ====")

        pl_a = self.tidal_a.get_playlist_by_title(playlist_title)
        pl_b = self.tidal_b.get_playlist_by_title(playlist_title)

        if pl_a is None or pl_b is None:
            print("  → La playlist no existe en ambas cuentas. No se sincroniza.")
            return

        tracks_a = self.tidal_a.list_playlist_tracks_map(pl_a)
        tracks_b = self.tidal_b.list_playlist_tracks_map(pl_b)
        ids_a = set(tracks_a.keys())
        ids_b = set(tracks_b.keys())

        print(f"  Pistas en cuenta A: {len(ids_a)}")
        print(f"  Pistas en cuenta B: {len(ids_b)}")

        if not ids_a and not ids_b:
            print("  → Ambas playlists están vacías. Nada que sincronizar.")
            return

        union_ids: Set[int] = ids_a | ids_b
        to_add_a = union_ids - ids_a
        to_add_b = union_ids - ids_b

        print(f"  Total canciones distintas (A ∪ B): {len(union_ids)}")
        print(f"  Se añadirán a A: {len(to_add_a)}")
        print(f"  Se añadirán a B: {len(to_add_b)}")

        playlist_id = str(getattr(pl_a, "id", None) or getattr(pl_b, "id", ""))
        user_a = self.tidal_a.client.user_name
        user_b = self.tidal_b.client.user_name

        if to_add_a:
            self.tidal_a.add_tracks_by_ids(
                pl=pl_a,
                track_ids=sorted(to_add_a),
                avoid_duplicates=self.avoid_duplicates,
            )
            self._log_added_tracks(
                playlist_id=playlist_id,
                track_ids=to_add_a,
                source_tracks=tracks_b,
                inserted_by=user_b,
            )

        if to_add_b:
            self.tidal_b.add_tracks_by_ids(
                pl=pl_b,
                track_ids=sorted(to_add_b),
                avoid_duplicates=self.avoid_duplicates,
            )
            self._log_added_tracks(
                playlist_id=playlist_id,
                track_ids=to_add_b,
                source_tracks=tracks_a,
                inserted_by=user_a,
            )

        print("  → Sincronización completada.\n")

    def _log_added_tracks(
        self,
        playlist_id: str,
        track_ids: Set[int],
        source_tracks: Dict[int, Dict[str, str]],
        inserted_by: str,
    ) -> None:
        if not self.dynamo_handler or not track_ids:
            return

        for track_id in sorted(track_ids):
            meta = source_tracks.get(track_id, {})
            title = meta.get("title") or f"Track {track_id}"
            artist = meta.get("artist") or None
            try:
                record_id = self.dynamo_handler.record_added_track(
                    playlist_id=playlist_id,
                    track_id=str(track_id),
                    title=title,
                    artist=artist,
                    inserted_by=inserted_by,
                )
                print(f"  → Registrado en DynamoDB: '{title}' (por {inserted_by}) [{record_id}]")
            except Exception as e:
                print(f"  → Error registrando '{title}' en DynamoDB: {e}")

    def _get_common_playlists(self) -> List[str]:
        pls_a = self.tidal_a.list_user_playlists()
        pls_b = self.tidal_b.list_user_playlists()

        map_a = self._build_title_map(pls_a)
        map_b = self._build_title_map(pls_b)

        common_norm = set(map_a.keys()) & set(map_b.keys())
        return [map_a[t] for t in common_norm]

    @staticmethod
    def _build_title_map(playlists: List[Dict[str, Any]]) -> Dict[str, str]:
        out: Dict[str, str] = {}
        for p in playlists:
            title = (p.get("title") or "").strip()
            if not title:
                continue
            norm = title.lower()
            out.setdefault(norm, title)
        return out
