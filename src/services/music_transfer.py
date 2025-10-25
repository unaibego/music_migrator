"""
SpotifyToYouTubeMigrator — Orquestador de migración de playlists Spotify → YouTube Music

Depende de:
  - SpotifyLibrary (lee playlists y pistas desde Spotify)
  - YouTubeMusicLibrary (busca, puntúa y añade vídeos en YouTube)

Flujo:
  1) Lista tus playlists de Spotify y te pregunta por cada una si quieres migrarla.
  2) Resuelve cada pista (track + artista) en YouTube con score.
  3) Crea la playlist en YouTube (mismo nombre, privacidad configurable) y va añadiendo los vídeos.
  4) Para cada canción con score < UMBRAL, te pregunta:
       - [A]ñadir sugerida
       - [U]sar URL manual de YouTube
       - [S]altar
     (opcional) [L]istar top-N alternativas y elegir por índice.

Parámetros clave:
  - score_threshold: int (por defecto 70)
  - per_query_limit: int número de candidatos a considerar (por defecto 5)
  - privacy_status: "private" | "public" | "unlisted"

Ejemplo de uso:

    from spotify_library import SpotifyLibrary
    from ytmusic_library import YouTubeMusicLibrary

    # Asumiendo que ya creaste y autenticastes los clientes base:
    spot_lib = SpotifyLibrary(spotify_client)
    yt_lib = YouTubeMusicLibrary(youtube_client)

    migrator = SpotifyToYouTubeMigrator(spot_lib, yt_lib, score_threshold=70)
    migrator.run()   # te irá preguntando por consola

"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple


# ----------------------------
# Utilidades
# ----------------------------
_YT_URL_RE = re.compile(
    r"(?:https?://)?(?:www\.)?(?:m\.)?(?:youtube\.com/(?:watch\?v=|playlist\?list=)|youtu\.be/)([\w-]{11})",
    re.IGNORECASE,
)

def extract_video_id_from_url(url: str) -> Optional[str]:
    if not url:
        return None
    m = _YT_URL_RE.search(url.strip())
    if m:
        return m.group(1)
    # fallback para URLs tipo youtube.com/watch?v=ID&...
    if "watch?v=" in url:
        try:
            from urllib.parse import urlparse, parse_qs
            q = parse_qs(urlparse(url).query)
            vids = q.get("v")
            if vids:
                return vids[0]
        except Exception:
            pass
    return None


def prompt(msg: str, default: Optional[str] = None) -> str:
    sfx = f" [{default}]" if default else ""
    val = input(f"{msg}{sfx}: ").strip()
    if not val and default is not None:
        return default
    return val


def prompt_yn(msg: str, default_yes: bool = True) -> bool:
    d = "S" if default_yes else "n"
    ans = prompt(f"{msg} (s/n)", d).lower()
    return ans.startswith("s")


# ----------------------------
# Datos de plan de migración
# ----------------------------
@dataclass
class PlannedItem:
    track: str
    artist: Optional[str]
    video_id: Optional[str]
    score: int
    title: Optional[str]
    channel: Optional[str]
    url: Optional[str]


# ----------------------------
# Clase principal de migración
# ----------------------------
class SpotifyToYouTubeMigrator:
    def __init__(
        self,
        spotify_lib,  # SpotifyLibrary
        youtube_lib,  # YouTubeMusicLibrary
        *,
        score_threshold: int = 70,
        per_query_limit: int = 5,
        privacy_status: str = "private",
        ask_per_playlist: bool = True,
    ) -> None:
        self.spot = spotify_lib
        self.yt = youtube_lib
        self.score_threshold = score_threshold
        self.per_query_limit = per_query_limit
        self.privacy_status = privacy_status
        self.ask_per_playlist = ask_per_playlist

    # ------------------------
    # Entrypoint interactivo
    # ------------------------
    def run(self) -> None:
        playlists = self.spot.get_my_playlists()
        if not playlists:
            print("No se encontraron playlists en Spotify.")
            return
        print(f"Encontradas {len(playlists)} playlists en Spotify.\n")
        for p in playlists:
            name = p.get("name")
            pid = p.get("id")
            count = p.get("tracks_total")
            if self.ask_per_playlist:
                ok = prompt_yn(f"¿Migrar la playlist '{name}' ({count} pistas)?", default_yes=True)
                if not ok:
                    continue
            self.migrate_playlist(playlist_id=pid, playlist_name=name)

    # ------------------------
    # Migrar una playlist
    # ------------------------
    def migrate_playlist(self, playlist_id: str, playlist_name: str) -> None:
        print(f"\n==== Migrando: {playlist_name} ====")
        tracks = self.spot.get_playlist_tracks(playlist_id)
        if not tracks:
            print("(vacía)\n")
            return
        print(f"Pistas a resolver: {len(tracks)}")

        # 1) Resolver candidatos con score
        plan: List[PlannedItem] = []
        for t in tracks:
            name = t.get("name") or ""
            artists = t.get("artists") or []
            artist = (artists[0] or {}).get("name") if artists else None
            vid, score, info = self.yt.find_best_match_with_score(track=name, artist=artist, limit=self.per_query_limit)
            url = info.get("url") if info else None
            title = info.get("title") if info else None
            channel = info.get("channel") if info else None
            plan.append(PlannedItem(track=name, artist=artist, video_id=vid, score=score, title=title, channel=channel, url=url))
            # if len(plan) == 4:
            #     break
        # 2) Crear/obtener playlist destino en YouTube
        dest = self.yt.get_or_create_playlist(title=playlist_name, description="Migrated from Spotify", privacy_status=self.privacy_status)
        dest_id = dest.get("id")
        print(f"Destino YouTube: {playlist_name} (id={dest_id})")

        # 3) Añadir elementos con validación para low-score
        inserted = 0
        for i, item in enumerate(plan, 1):
            base = f"{item.track} — {item.artist or ''}".strip()
            if item.video_id and item.score >= self.score_threshold:
                print(f"{i:>3}. ✅ {base} | {item.title} [{item.score}]")
                self.yt.add_videos(playlist_id=dest_id, video_ids=[item.video_id], avoid_duplicates=True)
                inserted += 1
                continue

            # Low confidence o sin resultado → preguntar
            print(f"{i:>3}. ❓ {base} | Sugerencia: {item.title or '—'} | Canal: {item.channel or '—'} | score={item.score}")
            print(f"     {item.url or '(sin URL)'}")
            action = prompt("Acción (A=añadir sugerida, U=URL manual, L=listar opciones, S=salta)", "A" if item.video_id else "U").lower()

            if action == "a" and item.video_id:
                self.yt.add_videos(playlist_id=dest_id, video_ids=[item.video_id], avoid_duplicates=True)
                inserted += 1
                continue
            if action == "u":
                manual = prompt("Pega URL de YouTube (o Enter para omitir)", "").strip()
                vid = extract_video_id_from_url(manual)
                if not vid:
                    print("  → URL inválida; omitido.")
                    continue
                self.yt.add_videos(playlist_id=dest_id, video_ids=[vid], avoid_duplicates=True)
                inserted += 1
                continue
            if action == "l":
                # Listar top-N alternativas y permitir elegir
                opts = self.yt.preview_search(track=item.track, artist=item.artist, limit=self.per_query_limit)
                if not opts:
                    print("  → sin alternativas; omitido.")
                    continue
                for j, o in enumerate(opts, 1):
                    print(f"    {j}. {o['title']} | {o['channel']}\n       {o['url']}")
                pick = prompt("Elige índice o Enter para omitir", "").strip()
                if not pick:
                    continue
                try:
                    idx = int(pick) - 1
                    chosen = opts[idx]
                    vid = chosen.get("id")
                    if vid:
                        self.yt.add_videos(playlist_id=dest_id, video_ids=[vid], avoid_duplicates=True)
                        inserted += 1
                    else:
                        print("  → opción sin id; omitido.")
                except Exception:
                    print("  → selección inválida; omitido.")
                continue
            # 's' u otra cosa → saltar
            print("  → omitido.")

        print(f"\nHecho. Insertados: {inserted} / {len(plan)} en '{playlist_name}'.\n")


# ----------------------------
# Ejemplo CLI mínimo (opcional)
# ----------------------------
if __name__ == "__main__":
    print("Este módulo expone la clase SpotifyToYouTubeMigrator.\n"
          "Instánciala con SpotifyLibrary y YouTubeMusicLibrary y llama migrator.run().")
