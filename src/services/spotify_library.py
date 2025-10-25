from __future__ import annotations

from typing import Dict, List, Optional, Iterator, Any, Literal
import requests

# Importa tu cliente ya funcional
# from spotify_client import SpotifyUserClient   # si lo tienes en otro módulo
# En tu caso:
from src.core.settings.spotify_service import get_spotify_settings
from collections import deque
spotify_settings = get_spotify_settings()
# from <ruta_donde_este_tu_clase> import SpotifyUserClient


class SpotifyLibrary:
    """
    Envuelve un SpotifyUserClient para operaciones de biblioteca:
    - get_my_playlists(): lista las playlists del usuario autenticado.
    - get_playlist_tracks(playlist_id): lista pistas (con artistas) de una playlist.
    """

    def __init__(self, client: "SpotifyUserClient") -> None:
        self.client = client

    # ------------------------
    # Playlists del usuario
    # ------------------------
    def get_my_playlists(
        self,
        max_total: Optional[int] = None,
        page_size: int = 50,
    ) -> List[Dict[str, Any]]:
        """
        Devuelve las playlists del usuario autenticado.
        - max_total: limita el nº total a devolver (None => traer todas)
        - page_size: tamaño de página (máx. 50 en /me/playlists)
        Retorna lista de dicts: {id, name, owner_id, owner_display_name, tracks_total}
        """
        params = {
            "limit": min(max(page_size, 1), 50),
            # afinamos fields para minimizar payload
            "fields": "items(id,name,owner(id,display_name),tracks(total)),next,total"
        }
        results: List[Dict[str, Any]] = []

        for page in self._paginate("/me/playlists", params=params):
            items = page.get("items", []) or []
            for p in items:
                results.append({
                    "id": p.get("id"),
                    "name": p.get("name"),
                    "owner_id": (p.get("owner") or {}).get("id"),
                    "owner_display_name": (p.get("owner") or {}).get("display_name"),
                    "tracks_total": (p.get("tracks") or {}).get("total"),
                })
                if max_total is not None and len(results) >= max_total:
                    return results[:max_total]
        return results
    
    def get_playlist_images(self, playlist_id: str) -> List[Dict[str, Any]]:
        """
        Devuelve el array de ImageObject de Spotify:
        [{url, width, height}, ...] (orden habitual: mayor→menor).
        """
        endpoint = f"/playlists/{playlist_id}/images"
        # api_request maneja auth; este endpoint devuelve JSON
        images = self.client.api_request("GET", endpoint)
        # Algunas libs devuelven dict; normalizamos a lista
        if isinstance(images, dict):
            images = images.get("images") or images.get("items") or []
        return images or []

    def get_best_playlist_image_url(
        self,
        playlist_id: str,
        *,
        strategy: Literal["largest", "smallest"] = "largest",
    ) -> Optional[str]:
        """
        Selecciona la mejor URL según estrategia.
        """
        imgs = self.get_playlist_images(playlist_id)
        if not imgs:
            return None
        def _w(o): 
            # Algunos objetos pueden no traer width/height
            return int(o.get("width") or 0)
        imgs_sorted = sorted(imgs, key=_w, reverse=(strategy == "largest"))
        return imgs_sorted[0].get("url")

    def download_bytes_from_url(self, url: str, timeout: int = 20) -> Optional[bytes]:
        """
        Descarga bytes de imagen desde la CDN de Spotify (URLs temporales).
        """
        try:
            r = requests.get(url, timeout=timeout)
            r.raise_for_status()
            return r.content
        except Exception:
            return None
        


    
    def get_my_saved_tracks(
        self,
        max_total: Optional[int] = None,
        page_size: int = 50,
    ) -> List[Dict[str, Any]]:
        """
        Devuelve las canciones guardadas del usuario (Liked Songs) desde /me/tracks.
        Estructura compatible con get_playlist_tracks():
            {
              id, name, duration_ms, is_local, added_at,
              album: {id, name},
              artists: [{id, name}, ...]
            }
        """
        endpoint = "/me/tracks"
        params = {
            "limit": min(max(page_size, 1), 50),
            "fields": (
                "items(added_at,track(id,name,duration_ms,is_local,"
                "album(id,name),artists(id,name))),next,total"
            )
        }
        tracks: deque = deque(maxlen=max_total)
        for page in self._paginate(endpoint, params=params):
            for item in page.get("items", []) or []:
                t = item.get("track")
                if not t:
                    continue
                artists = t.get("artists") or []
                tracks.append({
                    "id": t.get("id"),
                    "name": t.get("name"),
                    "duration_ms": t.get("duration_ms"),
                    "is_local": t.get("is_local"),
                    "added_at": item.get("added_at"),
                    "album": {
                        "id": (t.get("album") or {}).get("id"),
                        "name": (t.get("album") or {}).get("name"),
                    },
                    "artists": [{"id": a.get("id"), "name": a.get("name")} for a in artists],
                })
                if max_total is not None and len(tracks) >= max_total:
                    return tracks[:max_total]
        return list(reversed(tracks))

    # ------------------------
    # Pistas de una playlist
    # ------------------------
    def get_playlist_tracks(
        self,
        playlist_id: str,
        max_total: Optional[int] = None,
        page_size: int = 100,
    ) -> List[Dict[str, Any]]:
        """
        Devuelve las pistas de una playlist, con artistas.
        - max_total: límite total (None => traer todas)
        - page_size: tamaño de página (máx. 100 en /playlists/{id}/tracks)

        Retorna lista de dicts:
            {
              id, name, duration_ms, is_local, added_at,
              album: {id, name},
              artists: [{id, name}, ...]
            }
        """
        endpoint = f"/playlists/{playlist_id}/tracks"
        params = {
            "limit": min(max(page_size, 1), 100),
            # afinamos fields: elementos necesarios + paginación
            "fields": (
                "items(added_at,track(id,name,duration_ms,is_local,"
                "album(id,name),artists(id,name))),next,total"
            )
        }

        tracks: List[Dict[str, Any]] = []

        for page in self._paginate(endpoint, params=params):
            for item in page.get("items", []) or []:
                t = item.get("track")
                if not t:
                    continue  # puede haber items 'vacíos' o eliminados
                artists = t.get("artists") or []
                tracks.append({
                    "id": t.get("id"),
                    "name": t.get("name"),
                    "duration_ms": t.get("duration_ms"),
                    "is_local": t.get("is_local"),
                    "added_at": item.get("added_at"),
                    "album": {
                        "id": (t.get("album") or {}).get("id"),
                        "name": (t.get("album") or {}).get("name"),
                    },
                    "artists": [{"id": a.get("id"), "name": a.get("name")} for a in artists],
                })
                if max_total is not None and len(tracks) >= max_total:
                    return tracks[:max_total]
        return tracks

    # ------------------------
    # Helper de paginación
    # ------------------------
    def _paginate(
        self,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None,
    ) -> Iterator[Dict[str, Any]]:
        """
        Itera sobre páginas de endpoints de Spotify que devuelven 'next'.
        Usa client.api_request() para GET sucesivos.
        """
        url: Optional[str] = None
        current_params = dict(params or {})

        while True:
            if url is None:
                # primera llamada por endpoint relativo
                page = self.client.api_request("GET", endpoint, params=current_params)
            else:
                # siguientes llamadas usan URL absoluta 'next'
                page = self.client.api_request("GET", url)

            yield page

            url = page.get("next")
            if not url:
                break


# ------------------------
# Ejemplo de uso directo
# ------------------------
if __name__ == "__main__":
    # Instancia tu cliente autenticado
    from .spotify_getter import SpotifyUserClient  # ajusta import a tu ruta real

    client = SpotifyUserClient(
        client_id=spotify_settings.client_id,
        redirect_uri="http://127.0.0.1:8080/callback",  # o el que tengas en el Dashboard
        scope="user-read-email user-read-private playlist-read-private playlist-read-collaborative",
    )
    client.authenticate()

    lib = SpotifyLibrary(client)

    # 1) Tus playlists
    my_playlists = lib.get_my_playlists()
    print(f"Playlists: {len(my_playlists)}")
    for p in my_playlists:
        print(p)

    # 2) Pistas de la primera playlist (si existe)
    if my_playlists:
        pid = my_playlists[0]["id"]
        tracks = lib.get_playlist_tracks(pid)
        print(f"Pistas en {my_playlists[0]['name']}: {len(tracks)}")
        for t in tracks[:5]:
            print(t)
