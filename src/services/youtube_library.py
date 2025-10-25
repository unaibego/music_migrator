"""
YouTubeMusicLibrary — Capa de servicio sobre YouTubeMusicUserClient

Objetivo
--------
Ofrecer utilidades de alto nivel para:
  - Buscar canciones por texto, título + artista.
  - Crear playlists.
  - Añadir vídeos (pistas) a playlists, evitando duplicados.

Requisitos
---------
- Depende de una instancia autenticada de `YouTubeMusicUserClient` (definida en tu proyecto).
- Para operaciones de escritura (crear playlist, añadir vídeos) necesitas el scope amplio:
  `https://www.googleapis.com/auth/youtube`

Ejemplo de uso
--------------

    from ytmusic_client import YouTubeMusicUserClient
    from ytmusic_library import YouTubeMusicLibrary

    client = YouTubeMusicUserClient(
        client_id="TU_CLIENT_ID",
        scope="https://www.googleapis.com/auth/youtube"  # <- escritura
    )
    client.authenticate()

    lib = YouTubeMusicLibrary(client)

    # Buscar mejor coincidencia para un tema
    vid = lib.find_best_match(track="Viva la Vida", artist="Coldplay")
    print("videoId:", vid)

    # Crear playlist y añadir temas por (track, artist)
    playlist = lib.create_playlist(title="Migrated — Coldplay")
    added = lib.add_tracks_by_metadata(
        playlist_id=playlist["id"],
        songs=[
            {"track": "Viva la Vida", "artist": "Coldplay"},
            {"track": "Yellow", "artist": "Coldplay"},
        ],
        pick_strategy="best",  # o "first"
        avoid_duplicates=True,
    )
    print("insertados:", added)
"""
from __future__ import annotations

import re
import json
import os
from typing import Any, Dict, Iterable, List, Optional, Tuple


class YouTubeMusicLibrary:
    """Capa de conveniencia que compone un `YouTubeMusicUserClient`.

    No gestiona autenticación: debe recibir un cliente ya autenticado.
    """
    PENAL_WORDS = ("cover", "karaoke", "remix", "instrumental")

    def __init__(self, client) -> None:
        self.client = client

    # -------------------------------
    # BÚSQUEDA
    # -------------------------------
    def search_tracks(self, query: Optional[str] = None, *,
                  track: Optional[str] = None, artist: Optional[str] = None,
                  limit: int = 10, skip_videos_lookup: bool = True) -> Dict[str, Any]:
        """Busca vídeos; opcionalmente evita /videos (ahorra 1 unidad por llamada).
        Cuando skip_videos_lookup=True, se devuelven items tal cual del search (sin filtrar por categoría).
        """
        if not query:
            parts = [p for p in [track, artist] if p]
            if not parts:
                raise ValueError("Proporciona 'query' o al menos 'track'/'artist'.")
            query = " - ".join(parts)
        res = self.client.api_request("GET", "/search", params={
            "part": "id,snippet",
            "q": query,
            "type": "video",
            "maxResults": limit,
            "order": "relevance",
        })
        if skip_videos_lookup:
            return res  # 100 unidades (en vez de 101)
        video_ids = [it.get("id", {}).get("videoId") for it in res.get("items", []) if it.get("id", {}).get("videoId")]
        if not video_ids:
            return {"items": []}
        vids = self.client.api_request("GET", "/videos", params={
            "part": "id,snippet,contentDetails",
            "id": ",".join(video_ids),
        })
        items = [v for v in vids.get("items", []) if v.get("snippet", {}).get("categoryId") == "10"]
        return {"items": items}

    def find_best_match(
        self,
        *,
        track: str,
        artist: Optional[str] = None,
        limit: int = 10,
    ) -> Optional[str]:
        """Heurística sencilla para elegir el mejor `videoId` para (track, artist).

        - Prioriza títulos que contengan el nombre del tema completo.
        - Si se facilita `artist`, prioriza canal/título que lo mencionen.
        - Devuelve `videoId` o `None` si no hay resultados.
        """
        res = self.search_tracks(track=track, artist=artist, limit=limit)
        items = res.get("items", [])
        if not items:
            return None

        # Estandarización para comparar
        def norm(s: str) -> str:
            s = s.lower()
            s = re.sub(r"\s+", " ", s)
            return s.strip()

        ntrack = norm(track)
        nartist = norm(artist) if artist else None

        # Score simple
        scored: List[Tuple[int, Dict[str, Any]]] = []
        for it in items:
            snip = it.get("snippet", {})
            title = norm(snip.get("title", ""))
            channel = norm(snip.get("channelTitle", ""))
            score = 0
            if ntrack and ntrack in title:
                score += 3
            if nartist:
                if nartist in title:
                    score += 2
                if nartist in channel:
                    score += 2
            # Penaliza versiones "live", "cover" si no están pedidas explícitamente
            if "live" in title and (not ntrack or "live" not in ntrack):
                score -= 1
            if "cover" in title:
                score -= 1
            scored.append((score, it))

        scored.sort(key=lambda x: x[0], reverse=True)
        best = scored[0][1]
        return best.get("id") or best.get("contentDetails", {}).get("videoId") or best.get("id", {}).get("videoId")
    

    def _norm(self, s: Optional[str]) -> str:
        if not s: 
            return ""
        s = s.lower()
        s = re.sub(r"\s+", " ", s)
        s = s.strip()
        return s

    def _contains(self, haystack: str, needle: str) -> bool:
        return needle in haystack if needle and haystack else False

    

    def score_candidate(self, track: str, artist: Optional[str], item: Dict[str, Any]) -> int:
        """Calcula un score 0..100 para un resultado de búsqueda."""
        sn = item.get("snippet", {}) if "snippet" in item else {}
        title = self._norm(sn.get("title"))
        channel = self._norm(sn.get("channelTitle"))
        ntrack = self._norm(track)
        nartist = self._norm(artist)

        score = 0

        # Coincidencia del título con el track (peso alto)
        if ntrack and self._contains(title, ntrack):
            score += 55
        elif ntrack:
            # alguna coincidencia parcial (tokens principales)
            toks = [t for t in re.split(r"[^\w]+", ntrack) if t]
            hits = sum(1 for t in toks if t in title)
            score += min(35, 7 * hits)

        # Coincidencia del artista (título o canal) (peso medio)
        if nartist:
            if self._contains(title, nartist):
                score += 20
            if self._contains(channel, nartist):
                score += 15

        # Penalizaciones por palabras “sospechosas”
        for w in self.PENAL_WORDS:
            if w in title and (not ntrack or w not in ntrack):
                score -= 10

        # Bonus pequeño si la categoría es Música
        cat = item.get("snippet", {}).get("categoryId")
        if cat == "10":
            score += 5

        # Clipa 0..100
        return max(0, min(100, score))

    def search_tracks_with_scores(self, query: Optional[str] = None, *, track: Optional[str] = None,
                                artist: Optional[str] = None, limit: int = 10) -> Dict[str, Any]:
        """Como search_tracks, pero añade un campo 'score' en cada item."""
        raw = self.search_tracks(query=query, track=track, artist=artist, limit=limit)
        items = raw.get("items", [])
        t = track or (query or "")
        for it in items:
            it["_score"] = self.score_candidate(t, artist, it)
        # ordena por score
        items.sort(key=lambda x: x.get("_score", 0), reverse=True)
        return {"items": items}

    def find_best_match_with_score(self, *, track: str, artist: Optional[str] = None,
                                limit: int = 10) -> Tuple[Optional[str], int, Optional[Dict[str, Any]]]:
        """Devuelve (video_id, score, info) del mejor match."""
        if hasattr(self.client, "resolve_best_video"):
            data = self.client.resolve_best_video(track=track, artist=artist, limit=min(limit, 5), use_cache=True)
            vid = data.get("videoId")
            score = int(data.get("score") or 0)
            info = {
                "id": vid,
                "title": data.get("title"),
                "channel": data.get("channel"),
                "url": data.get("url"),
            }
            return vid, score, info
        # Fallback a tu implementación anterior (sin caché)
        res = self.search_tracks_with_scores(track=track, artist=artist, limit=limit)
        items = res.get("items", [])
        if not items:
            return None, 0, None
        best = items[0]
        vid = best.get("id") or best.get("contentDetails", {}).get("videoId") or best.get("id", {}).get("videoId")
        sn = best.get("snippet", {})
        info = {
            "id": vid,
            "title": sn.get("title"),
            "channel": sn.get("channelTitle"),
            "url": f"https://www.youtube.com/watch?v={vid}" if vid else None,
        }
        return vid, best.get("_score", 0), info

    def plan_tracks_by_metadata(self, songs, *, pick_strategy: str = "best",
                                per_query_limit: int = 5, return_scores: bool = True,
                                min_score_flag: int = 70) -> List[Dict[str, Any]]:
        """No inserta; resuelve cada (track,artist) y devuelve plan con score y marca low_confidence."""
        plan = []
        for s in songs:
            track, artist = s.get("track"), s.get("artist")
            if not track:
                continue
            if pick_strategy == "best":
                vid, score, info = self.find_best_match_with_score(track=track, artist=artist, limit=per_query_limit)
                video_id = vid
            else:
                q = f"{track} - {artist}" if artist else track
                res = self.search_tracks_with_scores(query=q, limit=per_query_limit)
                items = res.get("items", [])
                if not items:
                    video_id, score, info = None, 0, None
                else:
                    best = items[0]
                    video_id = best.get("id") or best.get("contentDetails", {}).get("videoId") or best.get("id", {}).get("videoId")
                    score = best.get("_score", 0)
                    sn = best.get("snippet", {})
                    info = {
                        "id": video_id,
                        "title": sn.get("title"),
                        "channel": sn.get("channelTitle"),
                        "url": f"https://www.youtube.com/watch?v={video_id}" if video_id else None,
                    }
            item = {
                "track": track,
                "artist": artist,
                "videoId": video_id,
                "score": score if return_scores else None,
                "low_confidence": (score < min_score_flag),
                "title": (info or {}).get("title"),
                "channel": (info or {}).get("channel"),
                "url": (info or {}).get("url"),
            }
            plan.append(item)
        return plan



    def add_planned(self, playlist_id: str, planned: List[Dict[str, Any]],
                    *, avoid_duplicates: bool = True, min_score: int = 70):
        """Inserta solo los items con score >= min_score."""
        filtered = [p.get("videoId") for p in planned if p.get("videoId") and (p.get("score", 0) >= min_score)]
        return self.add_videos(playlist_id=playlist_id, video_ids=filtered, avoid_duplicates=avoid_duplicates)

    def _ensure_cache_loaded(self) -> None:
        if hasattr(self, "_search_cache_loaded") and self._search_cache_loaded:
            return
        self._search_cache_path = getattr(self, "_search_cache_path", ".yt_search_cache.json")
        self._search_cache = {}
        try:
            if os.path.exists(self._search_cache_path):
                with open(self._search_cache_path, "r", encoding="utf-8") as f:
                    self._search_cache = json.load(f)
        except Exception:
            self._search_cache = {}
        self._search_cache_loaded = True

    def _cache_key(self, *, track: Optional[str], artist: Optional[str]) -> str:
        import re
        def _norm(s: Optional[str]) -> str:
            if not s: return ""
            s = s.lower()
            s = re.sub(r"\s+", " ", s).strip()
            return s
        return f"{_norm(track)}|||{_norm(artist)}"

    def _save_cache(self) -> None:
        try:
            with open(self._search_cache_path, "w", encoding="utf-8") as f:
                json.dump(self._search_cache, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    # Heurística rápida 0..100 solo con snippet (sin /videos)
    def _score_snippet(self, *, track: str, artist: Optional[str], snippet: Dict[str, Any]) -> int:
        import re
        def _n(x: Optional[str]) -> str:
            if not x: return ""
            x = x.lower()
            x = re.sub(r"\s+", " ", x).strip()
            return x
        title = _n(snippet.get("title"))
        channel = _n(snippet.get("channelTitle"))
        nt = _n(track); na = _n(artist)
        score = 0
        if nt and nt in title:
            score += 55
        else:
            toks = [t for t in re.split(r"[^\w]+", nt) if t]
            score += min(35, 7 * sum(1 for t in toks if t in title))
        if na:
            if na in title: score += 20
            if na in channel: score += 15
        for w in ("cover","karaoke","remix","instrumental","live"):
            if w in title and (not nt or w not in nt):
                score -= 10
        return max(0, min(100, score))

    def resolve_best_video(self, *, track: str, artist: Optional[str] = None,
                        limit: int = 3, use_cache: bool = True) -> Dict[str, Any]:
        """Devuelve {videoId, score, title, channel, url}. Usa cache para reducir llamadas.
        Evita /videos (solo usa /search, 100 unidades por fallo de caché, 0 por hit).
        """
        if use_cache:
            self._ensure_cache_loaded()
            key = self._cache_key(track=track, artist=artist)
            hit = self._search_cache.get(key)
            if hit:
                return hit

        res = self.search_tracks(track=track, artist=artist, limit=limit, skip_videos_lookup=True)
        items = res.get("items", [])
        if not items:
            out = {"videoId": None, "score": 0, "title": None, "channel": None, "url": None}
            if use_cache:
                self._search_cache[key] = out; self._save_cache()
            return out

        # ordenar por score local
        scored = []
        for it in items:
            sn = it.get("snippet", {})
            score = self._score_snippet(track=track, artist=artist, snippet=sn)
            vid = it.get("id", {}).get("videoId")
            scored.append((score, vid, sn))
        scored.sort(key=lambda x: x[0], reverse=True)
        score, vid, sn = scored[0]
        out = {
            "videoId": vid,
            "score": score,
            "title": sn.get("title"),
            "channel": sn.get("channelTitle"),
            "url": f"https://www.youtube.com/watch?v={vid}" if vid else None,
        }
        if use_cache:
            self._search_cache[key] = out; self._save_cache()
        return out

    # -------------------------------
    # PLAYLISTS
    # -------------------------------
    def create_playlist(self, title: str, description: str = "", privacy_status: str = "private") -> Dict[str, Any]:
        return self.client.create_playlist(title=title, description=description, privacy_status=privacy_status)

    def get_or_create_playlist(self, title: str, description: str = "", privacy_status: str = "private") -> Dict[str, Any]:
        # Busca por nombre exacto entre las propias playlists (paginando hasta encontrar)
        page_token: Optional[str] = None
        while True:
            res = self.client.get_my_playlists(max_results=50, page_token=page_token)
            for it in res.get("items", []):
                if it.get("snippet", {}).get("title") == title:
                    return it
            page_token = res.get("nextPageToken")
            if not page_token:
                break
        # Si no existe, la crea
        return self.create_playlist(title=title, description=description, privacy_status=privacy_status)

    def list_playlist_video_ids(self, playlist_id: str) -> List[str]:
        ids: List[str] = []
        page_token: Optional[str] = None
        while True:
            res = self.client.get_playlist_items(playlist_id=playlist_id, max_results=50, page_token=page_token)
            for it in res.get("items", []):
                rid = it.get("snippet", {}).get("resourceId", {})
                vid = rid.get("videoId")
                if vid:
                    ids.append(vid)
            page_token = res.get("nextPageToken")
            if not page_token:
                break
        return ids

    def add_videos(
        self,
        playlist_id: str,
        video_ids: Iterable[str],
        *,
        avoid_duplicates: bool = True,
    ) -> List[Dict[str, Any]]:
        existing = set(self.list_playlist_video_ids(playlist_id)) if avoid_duplicates else set()
        to_add = [v.get("videoId") for v in video_ids if v.get("videoId") and (v.get("videoId") not in existing)]
        if not to_add:
            return []
        return self.client.add_videos_to_playlist(playlist_id=playlist_id, video_ids=to_add)

    def add_tracks_by_queries(
        self,
        playlist_id: str,
        queries: Iterable[str],
        *,
        pick_strategy: str = "first",  # "first" | "best"
        avoid_duplicates: bool = True,
        per_query_limit: int = 5,
    ) -> List[Dict[str, Any]]:
        """Busca cada query y añade el primer/best match.
        - pick_strategy="first": usa el primer resultado (más rápido).
        - pick_strategy="best": aplica heurística similar a `find_best_match`.
        """
        vids: List[str] = []
        for q in queries:
            res = self.search_tracks(query=q, limit=per_query_limit)
            items = res.get("items", [])
            if not items:
                continue
            if pick_strategy == "best":
                # reusar heurística con el título completo como track
                best = self.find_best_match(track=q, artist=None, limit=per_query_limit)
                if best:
                    vids.append(_extract_video_id(best))
            else:
                vids.append(_extract_video_id(items[0]))
        return self.add_videos(playlist_id=playlist_id, video_ids=[v for v in vids if v])

    def add_tracks_by_metadata(
        self,
        playlist_id: str,
        songs: Iterable[Dict[str, str]],
        *,
        pick_strategy: str = "best",
        avoid_duplicates: bool = True,
        per_query_limit: int = 5,
    ) -> List[Dict[str, Any]]:
        """`songs` es un iterable de dicts con claves: `track` y opcional `artist`.
        Intenta encontrar el mejor match por cada elemento y lo añade a la playlist.
        """
        vids: List[str] = []
        for s in songs:
            track = s.get("track")
            artist = s.get("artist")
            if not track:
                continue
            if pick_strategy == "best":
                vid = self.find_best_match(track=track, artist=artist, limit=per_query_limit)
                if vid:
                    vids.append(_extract_video_id(vid))
            else:
                # "first": usa la primera coincidencia cruda
                q = f"{track} - {artist}" if artist else track
                res = self.search_tracks(query=q, limit=per_query_limit)
                items = res.get("items", [])
                if items:
                    vids.append(_extract_video_id(items[0]))
        return self.add_videos(playlist_id=playlist_id, video_ids=[v for v in vids if v], avoid_duplicates=avoid_duplicates)


# ---------------------------------
# Helpers internos
# ---------------------------------

def _extract_video_id(item: Any) -> Optional[str]:
    """Extrae videoId desde distintas formas de item (search/videos)."""
    if isinstance(item, str):
        return item
    if not isinstance(item, dict):
        return None
    # /search
    vid = item.get("id", {}).get("videoId")
    if vid:
        return vid
    # /videos
    return item.get("id") or item.get("contentDetails", {}).get("videoId")
