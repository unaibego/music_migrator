from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional, Tuple
from .tidal_client import TidalUserClient
import base64


class TidalLibrary:
    """
    Capa de conveniencia sobre TidalUserClient.

    Depende de una instancia autenticada de `TidalUserClient`.
    Métodos principales:
      - search_tracks(query|track+artist)
      - find_best_match(track, artist) -> track_id
      - create_playlist / get_or_create_playlist
      - list_playlist_track_ids
      - add_tracks_by_ids
      - add_tracks_by_metadata(songs=[{track, artist}], pick_strategy="best")
      - plan_tracks_by_metadata(...) -> devuelve plan con score/elección
    """

    PENAL_WORDS = ("cover", "karaoke", "remix", "instrumental", "live")

    def __init__(self, client: TidalUserClient) -> None:
        self.client = client  # instancia autenticada

    # -------------------------------
    # BÚSQUEDA
    # -------------------------------
    def search_tracks(
        self,
        query: Optional[str] = None,
        *,
        track: Optional[str] = None,
        artist: Optional[str] = None,
        limit: int = 20,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """
        Busca pistas en TIDAL. Devuelve lista de dicts simplificados tal como
        los produce `TidalUserClient.search_tracks`.
        Nota: tu TidalUserClient sí soporta limit/offset en búsqueda (con fallback).
        """
        if not query:
            parts = [p for p in [track, artist] if p]
            if not parts:
                raise ValueError("Proporciona 'query' o al menos 'track'/'artist'.")
            query = " - ".join(parts)
        return self.client.search_tracks(query, limit=limit, offset=offset)

    def list_favorite_track_ids(self) -> List[int]:
        items = self.client.list_all_favorite_tracks()
        out: List[int] = []
        for t in items:
            if t.get("id") is not None:
                out.append(int(t["id"]))
        return out
    
    def set_playlist_image(self, pl, image_bytes: bytes) -> bool:
        """
        Wrapper de alto nivel que delega en el cliente.
        """
        return self.client.set_playlist_image(pl, image_bytes)

    def add_favorites_by_ids(
        self,
        track_ids: Iterable[int],
        *,
        avoid_duplicates: bool = True,
    ) -> None:
        ids = [int(tid) for tid in track_ids if tid is not None]
        if not ids:
            return
        if avoid_duplicates:
            existing = set(self.list_favorite_track_ids())
            ids = [tid for tid in ids if tid not in existing]
        if ids:
            self.client.add_favorite_tracks(ids)

    def add_favorites_by_metadata(
        self,
        songs: Iterable[Dict[str, str]],
        *,
        pick_strategy: str = "best",
        per_query_limit: int = 10,
        avoid_duplicates: bool = True,
    ) -> int:
        """
        Resuelve (track, artist) a IDs de TIDAL y los añade a favoritos.
        Devuelve nº insertados.
        """
        if pick_strategy == "best":
            plan = self.plan_tracks_by_metadata(
                songs, per_query_limit=per_query_limit, min_score_flag=0
            )
            ids = [p["tidal_id"] for p in plan if p.get("tidal_id")]
            self.add_favorites_by_ids(ids, avoid_duplicates=avoid_duplicates)
            return len(ids)

        # Estrategia "first"
        ids: List[int] = []
        for s in songs:
            track = (s.get("track") or "").strip()
            artist = (s.get("artist") or "").strip() or None
            if not track:
                continue
            q = f"{track} - {artist}" if artist else track
            results = self.search_tracks(query=q, limit=per_query_limit)
            if results:
                tid = results[0].get("id")
                if tid is not None:
                    ids.append(int(tid))
        self.add_favorites_by_ids(ids, avoid_duplicates=avoid_duplicates)
        return len(ids)
    
    
    

    
    # Heurística de puntuación 0..100 sobre un item devuelto por TIDAL
    def score_candidate(self, track: str, artist: Optional[str], item: Dict[str, Any]) -> int:
        def _n(s: Optional[str]) -> str:
            if not s:
                return ""
            s = s.lower()
            s = re.sub(r"\s+", " ", s).strip()
            return s

        title = _n(item.get("title"))
        channel = _n(", ".join(a.get("name", "") for a in (item.get("artists", []) or []) if a.get("name") != None))
        ntrack = _n(track)
        nartist = _n(artist)

        score = 0
        # Coincidencia con el título
        if ntrack and ntrack in title:
            score += 55
        else:
            toks = [t for t in re.split(r"[^\w]+", ntrack) if t]
            hits = sum(1 for t in toks if t and t in title)
            score += min(35, 7 * hits)

        # Coincidencia con artista(s)
        if nartist:
            if nartist in title:
                score += 20
            if nartist in channel:
                score += 20

        # Penalizaciones
        for w in self.PENAL_WORDS:
            if w in title and (not ntrack or w not in ntrack):
                score -= 8

        return max(0, min(100, score))

    def search_tracks_with_scores(
        self,
        query: Optional[str] = None,
        *,
        track: Optional[str] = None,
        artist: Optional[str] = None,
        limit: int = 20,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """Como search_tracks pero añade `_score` y ordena de mayor a menor."""
        items = self.search_tracks(query=query, track=track, artist=artist, limit=limit, offset=offset)
        base_track = track or (query or "")
        for it in items:
            it["_score"] = self.score_candidate(base_track, artist, it)
        items.sort(key=lambda x: x.get("_score", 0), reverse=True)
        return items

    def find_best_match(
        self,
        *,
        track: str,
        artist: Optional[str] = None,
        limit: int = 20,
        offset: int = 0,
    ) -> Optional[int]:
        """Devuelve el `track_id` del mejor match o None si no hay resultados."""
        items = self.search_tracks_with_scores(track=track, artist=artist, limit=limit, offset=offset)
        if not items:
            return None
        return items[0].get("id")

    def find_best_match_with_score(
        self,
        *,
        track: str,
        artist: Optional[str] = None,
        limit: int = 20,
        offset: int = 0,
    ) -> Tuple[Optional[int], int, Optional[Dict[str, Any]]]:
        """Devuelve (track_id, score, info_resumida)."""
        items = self.search_tracks_with_scores(track=track, artist=artist, limit=limit, offset=offset)
        if not items:
            return None, 0, None
        best = items[0]
        info = {
            "id": best.get("id"),
            "title": best.get("title"),
            "artists": ", ".join(a.get("name") for a in (best.get("artists") or [])),
        }
        return best.get("id"), int(best.get("_score", 0)), info

    # -------------------------------
    # PLAYLISTS
    # -------------------------------
    def create_playlist(self, title: str, description: str = ""):
        """Crea playlist usando el cliente base."""
        return self.client.create_playlist(title, description)

    def get_or_create_playlist(self, title: str, description: str = "") -> Dict[str, Any]:
        """
        Busca por nombre exacto entre playlists del usuario;
        ahora usa `list_all_user_playlists()` en lugar de paginar manualmente.
        """
        playlists = self.client.list_all_user_playlists()
        title_norm = title.strip().lower()
        for p in playlists:
            if (p.get("title") or "").strip().lower() == title_norm:
                return p.get("p", None)
        return self.create_playlist(title, description)

    def list_playlist_track_ids(self, pl) -> List[int]:
        """
        Devuelve todos los track IDs de una playlist usando `list_all_playlist_tracks()`
        (sin limit/offset).
        """
        items = self.client.list_all_playlist_tracks(pl)
        out: List[int] = []
        for t in items:
            if t.get("id") is not None:
                out.append(int(t["id"]))
        return out

    def add_tracks_by_ids(
        self,
        pl,
        track_ids: Iterable[int],
        *,
        avoid_duplicates: bool = True,
    ) -> None:
        """Añade una lista de IDs a la playlist (evitando duplicados si se pide)."""
        ids = [int(tid) for tid in track_ids if tid is not None]
        if not ids:
            return
        if avoid_duplicates:
            existing = set(self.list_playlist_track_ids(pl))
            ids = [tid for tid in ids if tid not in existing]
        if ids:
            self.client.add_tracks_to_playlist(pl, ids)

    # -------------------------------
    # PLANIFICACIÓN / INSERCIÓN POR METADATOS
    # -------------------------------
    def plan_tracks_by_metadata(
        self,
        songs: Iterable[Dict[str, str]],
        *,
        per_query_limit: int = 10,
        min_score_flag: int = 70,
    ) -> List[Dict[str, Any]]:
        """
        No inserta; resuelve cada (track,artist) y devuelve un plan:
        [ { track, artist, tidal_id, score, title, artists, low_confidence }, ... ]
        """
        plan: List[Dict[str, Any]] = []
        for s in songs:
            track = (s.get("track") or "").strip()
            artist = (s.get("artist") or "").strip() or None
            if not track:
                continue
            tid, score, info = self.find_best_match_with_score(
                track=track, artist=artist, limit=per_query_limit
            )
            plan.append({
                "track": track,
                "artist": artist,
                "tidal_id": tid,
                "score": score,
                "low_confidence": score < min_score_flag,
                "title": (info or {}).get("title"),
                "artists": (info or {}).get("artists"),
            })
        return plan

    def add_planned(
        self,
        pl,
        planned: List[Dict[str, Any]],
        *,
        avoid_duplicates: bool = True,
        min_score: int = 70,
    ) -> Tuple[int, int]:
        """
        Inserta en la playlist solo los items con score >= min_score.
        Devuelve (insertados, totales_plan).
        """
        ids = [
            p.get("tidal_id")
            for p in planned
            if p.get("tidal_id") and (p.get("score", 0) >= min_score)
        ]
        before = len(ids)
        self.add_tracks_by_ids(
            pl=pl, track_ids=ids, avoid_duplicates=avoid_duplicates
        )
        return (len(ids), before)

    def add_tracks_by_metadata(
        self,
        pl,
        songs: Iterable[Dict[str, str]],
        *,
        pick_strategy: str = "best",  # "best" (heurística) ó "first" (primer resultado)
        per_query_limit: int = 10,
        avoid_duplicates: bool = True,
    ) -> int:
        """
        Resuelve cada (track, artist) y los inserta. Devuelve nº insertados.
        """
        if pick_strategy == "best":
            plan = self.plan_tracks_by_metadata(
                songs, per_query_limit=per_query_limit, min_score_flag=0
            )
            ids = [p["tidal_id"] for p in plan if p.get("tidal_id")]
            self.add_tracks_by_ids(
                pl, ids, avoid_duplicates=avoid_duplicates
            )
            return len(ids)

        # Estrategia "first": coger el primer resultado crudo
        ids: List[int] = []
        for s in songs:
            track = (s.get("track") or "").strip()
            artist = (s.get("artist") or "").strip() or None
            if not track:
                continue
            q = f"{track} - {artist}" if artist else track
            results = self.search_tracks(query=q, limit=per_query_limit)
            if results:
                tid = results[0].get("id")
                if tid is not None:
                    ids.append(int(tid))
        self.add_tracks_by_ids(pl, ids, avoid_duplicates=avoid_duplicates)
        return len(ids)


if __name__ == "__main__":
    # 1) Autenticación con TIDAL
    tidal = TidalUserClient()
    tidal.authenticate()

    # 2) Capa de biblioteca
    lib = TidalLibrary(tidal)

    # Buscar tema
    candidatos = lib.search_tracks(track="Viva la Vida", artist="Coldplay", limit=5)
    print(candidatos[0] if candidatos else "Sin resultados")

    # Crear/obtener playlist y añadir temas por metadatos
    pl = lib.get_or_create_playlist("Migrated — Coldplay", "Migrada desde Spotify")
    insertados = lib.add_tracks_by_metadata(
        pl=pl,
        songs=[
            {"track": "Viva la Vida", "artist": "Coldplay"},
            {"track": "Yellow", "artist": "Coldplay"},
        ],
        pick_strategy="best",
        avoid_duplicates=True,
    )
    print("Insertados:", insertados)
