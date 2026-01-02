from __future__ import annotations

from typing import Any, Dict, List, Optional, Set, Tuple

from .tidal_library import TidalLibrary
# Reutilizamos los mismos helpers interactivos del migrador.
# Si están en otro módulo, ajusta el import.
from src.utils.utils import prompt, prompt_yn  # o desde donde los tengas


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
    ) -> None:
        self.tidal_a = tidal_a
        self.tidal_b = tidal_b
        self.avoid_duplicates = avoid_duplicates
        self.ask_per_playlist = ask_per_playlist

    # ------------------------
    # Entrypoint interactivo
    # ------------------------
    def run(self) -> None:
        """
        Sincroniza todas las playlists que existan en ambas cuentas TIDAL.

        Ejemplo de uso:
            sync = TidalPlaylistsSynchronizer(lib_a, lib_b)
            sync.run()
        """
        common = self._get_common_playlists()
        if not common:
            print("No se encontraron playlists con el mismo nombre en ambas cuentas.")
            return

        print(f"Playlists comunes encontradas: {len(common)}\n")
        for title in sorted(common):
            print(f"→ Playlist común: '{title}'")
            if self.ask_per_playlist:
                ok = prompt_yn(
                    f"¿Sincronizar la playlist '{title}' entre ambas cuentas?",
                    default_yes=True,
                )
                if not ok:
                    continue

            self.sync_single_playlist(title)

    # ------------------------
    # Sincronizar UNA playlist
    # ------------------------
    def sync_single_playlist(self, playlist_title: str) -> None:
        """
        Sincroniza una playlist concreta (por nombre) entre ambas cuentas.

        Requisitos:
          - La playlist debe existir en ambas cuentas.
          - NO crea playlists nuevas.
        """
        print(f"\n==== Sincronizando playlist: '{playlist_title}' ====")

        pl_a = self.tidal_a.get_playlist_by_title(playlist_title)
        pl_b = self.tidal_b.get_playlist_by_title(playlist_title)

        if pl_a is None or pl_b is None:
            print("  → La playlist no existe en ambas cuentas. No se sincroniza.")
            return

        # 1) Obtener IDs actuales
        ids_a = set(self.tidal_a.list_playlist_track_ids(pl_a))
        ids_b = set(self.tidal_b.list_playlist_track_ids(pl_b))

        print(f"  Pistas en cuenta A: {len(ids_a)}")
        print(f"  Pistas en cuenta B: {len(ids_b)}")

        if not ids_a and not ids_b:
            print("  → Ambas playlists están vacías. Nada que sincronizar.")
            return

        # 2) Calcular unión y diferencias
        union_ids: Set[int] = ids_a | ids_b
        to_add_a = union_ids - ids_a
        to_add_b = union_ids - ids_b

        print(f"  Total canciones distintas (A ∪ B): {len(union_ids)}")
        print(f"  Se añadirán a A: {len(to_add_a)}")
        print(f"  Se añadirán a B: {len(to_add_b)}")

        # 3) Añadir a cada lado lo que le falte
        if to_add_a:
            self.tidal_a.add_tracks_by_ids(
                pl=pl_a,
                track_ids=sorted(to_add_a),
                avoid_duplicates=self.avoid_duplicates,
            )

        if to_add_b:
            self.tidal_b.add_tracks_by_ids(
                pl=pl_b,
                track_ids=sorted(to_add_b),
                avoid_duplicates=self.avoid_duplicates,
            )

        print("  → Sincronización completada.\n")

    # ------------------------
    # Helpers internos
    # ------------------------
    def _get_common_playlists(self) -> List[str]:
        """
        Devuelve la lista de títulos de playlists que existen en ambas cuentas.
        Se compara por nombre exacto (ignorando mayúsculas/minúsculas).
        """
        # Listas de playlists completas
        pls_a = self.tidal_a.list_user_playlists()
        pls_b = self.tidal_b.list_user_playlists()

        # Map norm_title -> título original (por si quieres respetar mayúsculas)
        map_a = self._build_title_map(pls_a)
        map_b = self._build_title_map(pls_b)

        common_norm = set(map_a.keys()) & set(map_b.keys())
        # Devuelve los títulos "bonitos" (los de A, por ejemplo)
        return [map_a[t] for t in common_norm]

    @staticmethod
    def _build_title_map(playlists: List[Dict[str, Any]]) -> Dict[str, str]:
        """
        Construye un diccionario:
            título_normalizado -> título_original
        """
        out: Dict[str, str] = {}
        for p in playlists:
            title = (p.get("title") or "").strip()
            if not title:
                continue
            norm = title.lower()
            # Si hay colisión de nombres normalizados, nos quedamos con el primero
            out.setdefault(norm, title)
        return out


# ----------------------------
# Ejemplo CLI mínimo (opcional)
# ----------------------------
if __name__ == "__main__":
    # Ejemplo orientativo; ajusta a cómo instancias tus clientes TIDAL
    from .tidal_client import TidalUserClient

    print(
        "Este módulo expone la clase TidalPlaylistsSynchronizer.\n"
        "Instánciala con dos TidalLibrary (usuario A y usuario B) y llama sync.run().\n"
    )

    # Usuario A
    client_a = TidalUserClient()
    client_a.authenticate()  # asume algún flujo de login
    tidal_a = TidalLibrary(client_a)

    # Usuario B
    client_b = TidalUserClient()
    client_b.authenticate()
    tidal_b = TidalLibrary(client_b)

    sync = TidalPlaylistsSynchronizer(
        tidal_a=tidal_a,
        tidal_b=tidal_b,
        avoid_duplicates=True,
        ask_per_playlist=True,
    )
    sync.run()
