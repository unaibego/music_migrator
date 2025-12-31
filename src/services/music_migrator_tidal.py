from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple
from pathlib import Path
from datetime import datetime

from .tidal_library import TidalLibrary
from .spotify_library import SpotifyLibrary
import os
import imghdr

# Se asume que ya tienes implementadas estas capas:
# - SpotifyLibrary: lee playlists y pistas desde Spotify
# - TidalLibrary: tu clase del mensaje (envuelve TidalUserClient)
#
# Interfaces mínimas esperadas:
#   SpotifyLibrary.get_my_playlists() -> List[{"id", "name", "tracks_total"}]
#   SpotifyLibrary.get_playlist_tracks(playlist_id) -> List[{"name", "artists":[{"name"}]}]
#   TidalLibrary.get_or_create_playlist(title, description="") -> {"id","title",...}
#   TidalLibrary.find_best_match_with_score(track, artist, limit=...) -> (tid, score, info)
#       con info={"id","title","artists"}
#   TidalLibrary.add_tracks_by_ids(playlist_id, track_ids: Iterable[int], avoid_duplicates=True)
#   TidalLibrary.search_tracks_with_scores(track=..., artist=..., limit=..., offset=...) -> lista items con "_score"
#
# Si tus firmas varían, ajusta los nombres en el migrador.

# ----------------------------
# Utilidades
# ----------------------------

# URLs típicas de TIDAL para pistas:
# - https://tidal.com/browse/track/12345678
# - https://listen.tidal.com/track/12345678
# - https://open.tidal.com/track/12345678

_TIDAL_TRACK_URL_RE = re.compile(
    r"(?:https?://)?(?:www\.)?(?:listen\.|open\.)?tidal\.com/(?:browse/)?track/(\d+)",
    re.IGNORECASE,
)

PASAR_CANCIONES = "a"

def extract_tidal_track_id(value: str) -> Optional[int]:
    """Obtiene el track_id a partir de una URL de TIDAL o de una cadena numérica."""
    if not value:
        return None
    s = value.strip()
    m = _TIDAL_TRACK_URL_RE.search(s)
    if m:
        try:
            return int(m.group(1))
        except Exception:
            return None
    # ¿Es un número plano?
    if s.isdigit():
        try:
            return int(s)
        except Exception:
            return None
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
    tidal_id: Optional[int]
    score: int
    title: Optional[str]
    artists: Optional[str]  # texto plano "Artista1, Artista2"


# ----------------------------
# Clase principal de migración
# ----------------------------
class SpotifyToTidalMigrator:
    """
    SpotifyToTidalMigrator — Orquestador de migración de playlists Spotify → TIDAL

    Depende de:
      - SpotifyLibrary (lee playlists y pistas desde Spotify)
      - TidalLibrary (busca, puntúa y añade pistas en TIDAL)

    Flujo:
      1) Lista tus playlists de Spotify y por cada una pregunta si quieres migrarla.
      2) Resuelve cada canción (track + artista) en TIDAL con score.
      3) Crea la playlist en TIDAL (mismo nombre) y añade las pistas con score alto.
      4) Para cada canción con score < UMBRAL:
           - [A]ñadir sugerida
           - [U]sar URL/ID manual de TIDAL
           - [L]istar top-N alternativas y elegir por índice
           - [S]altar

    Parámetros:
      - score_threshold: int (por defecto 70)
      - per_query_limit: int candidatos por búsqueda (por defecto 5)
      - ask_per_playlist: bool (pregunta por playlist; por defecto True)
      - avoid_duplicates: bool (evita duplicados en destino; por defecto True)
    """
    tidal : TidalLibrary
    spot : SpotifyLibrary

    def __init__(
        self,
        spotify_lib,          # SpotifyLibrary
        tidal_lib,            # TidalLibrary
        *,
        score_threshold: int = 70,
        per_query_limit: int = 5,
        ask_per_playlist: bool = True,
        avoid_duplicates: bool = True,
    ) -> None:
        self.spot = spotify_lib
        self.tidal = tidal_lib
        self.score_threshold = score_threshold
        self.per_query_limit = per_query_limit
        self.ask_per_playlist = ask_per_playlist
        self.avoid_duplicates = avoid_duplicates

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


    def _copy_playlist_image(self, *, playlist_name : str,  spotify_playlist_id: str, tidal_playlist_obj) -> None:
        """
        Intenta copiar la portada de la playlist de Spotify a la playlist de TIDAL.
        No lanza; loguea el resultado.
        """
        try:
            url = self.spot.get_best_playlist_image_url(spotify_playlist_id, strategy="largest")
            if not url:
                print("  (Sin portada en Spotify o no disponible)")
                return
            data = self.spot.download_bytes_from_url(url)
            # Detectar extensión
            kind = imghdr.what(None, h=data)  # 'jpeg', 'png', 'gif', 'webp', ...
            ext_map = {"jpeg": "jpg", "png": "png", "gif": "gif", "webp": "webp"}
            ext = ext_map.get(kind, "jpg")  # por defecto jpg

            # Asegurar carpeta destino
            out_dir = os.path.join(".", "blob")
            os.makedirs(out_dir, exist_ok=True)

            # Nombre de archivo: <playlist_id>.<ext>
            filename = f"{playlist_name.replace("/", "-")}.{ext}"
            out_path = os.path.join(out_dir, filename)

            # Si existe, no machacar sin querer: añade sufijo incremental
            if os.path.exists(out_path):
                base, _ = os.path.splitext(filename)
                n = 2
                while True:
                    candidate = os.path.join(out_dir, f"{base}_{n}.{ext}")
                    if not os.path.exists(candidate):
                        out_path = candidate
                        break
                    n += 1

            with open(out_path, "wb") as f:
                f.write(data)

            print(f"  Portada guardada en: {out_path}")
            return out_path

        except Exception as e:
            print(f"  (Fallo al guardar portada: {e})")
            return None



    # ------------------------
    # Migrar una playlist
    # ------------------------
    def migrate_playlist(self, playlist_id: str, playlist_name: str) -> None:
        print(f"\n==== Migrando: {playlist_name} ====")
        pl_exit = self.tidal.check_playlist(
            title=playlist_name, description="Migrated from Spotify"
        )
        if pl_exit:
            print(f"La playlist {playlist_name} ya existe. Elige la siguente accion")
            if PASAR_CANCIONES == "a":
                return None
            action = prompt(
                    "Acción (A=Añadir playlist, S=salta)",
                    "S",
                ).lower()
            if action == "s":
                return None
        tracks = self.spot.get_playlist_tracks(playlist_id)
        if not tracks:
            print("(vacía)\n")
            return
        print(f"Pistas a resolver: {len(tracks)}")

        # 1) Resolver candidatos con score contra TIDAL
        plan: List[PlannedItem] = []
        for t in tracks:
            name = (t.get("name") or "").strip()
            name = re.sub(r"\([^()]*\)|--.*?--", "", name)
            artists = t.get("artists") or []
            artist = (artists[0] or {}).get("name") if artists else None
            tid, score, info = self.tidal.find_best_match_with_score(
                track=name, artist=artist, limit=self.per_query_limit
            )
            title = (info or {}).get("title")
            artists_txt = (info or {}).get("artists")
            plan.append(
                PlannedItem(
                    track=name, artist=artist, tidal_id=tid, score=score, title=title, artists=artists_txt
                )
            )

        # 2) Crear/obtener playlist destino en TIDAL
        dest_pl = self.tidal.get_or_create_playlist(
            title=playlist_name, description="Migrated from Spotify"
        )
        self._copy_playlist_image(playlist_name=playlist_name , spotify_playlist_id=playlist_id, tidal_playlist_obj=dest_pl)
        # 3) Añadir elementos, pidiendo confirmación en low-score
        inserted = 0
        pending_manual_ids: List[int] = []

        for i, item in enumerate(plan, 1):
            base = f"{item.track} — {item.artist or ''}".strip()

            # Alta confianza: añadir directamente
            if item.tidal_id and item.score >= self.score_threshold:
                print(f"{i:>3}. ✅ {base} | {item.title} — {item.artists} [{item.score}]")
                self.tidal.add_tracks_by_ids(
                    pl=dest_pl, track_ids=[item.tidal_id], avoid_duplicates=self.avoid_duplicates
                )
                inserted += 1
                continue

            # Baja confianza o sin resultado → interacción
            sug = f"{item.title or '—'} — {item.artists or '—'}"
            print(f"{i:>3}. ❓ {base} | Sugerencia: {sug} | score={item.score}")
            if not PASAR_CANCIONES:
                action = prompt(
                    "Acción (A=añadir sugerida, U=URL/ID TIDAL manual, L=listar opciones, S=salta)",
                    "A" if item.tidal_id else "U",
                ).lower()
            else :
                action = PASAR_CANCIONES

            if action == "a" and item.tidal_id:
                self.tidal.add_tracks_by_ids(
                    pl=dest_pl, track_ids=[item.tidal_id], avoid_duplicates=self.avoid_duplicates
                )
                inserted += 1
                continue

            if action == "u":
                manual = prompt("Pega URL/ID de pista de TIDAL (o Enter para omitir)", "").strip()
                tid_manual = extract_tidal_track_id(manual)
                if not tid_manual:
                    print("  → Entrada inválida; omitido.")
                    continue
                pending_manual_ids.append(tid_manual)
                # Añadimos en lote al final por eficiencia/duplicados
                continue

            if action == "l":
                # Listar alternativas usando la búsqueda con scores
                opts = self._preview_search_tidal(track=item.track, artist=item.artist, limit=self.per_query_limit)
                if not opts:
                    print("  → sin alternativas; omitido.")
                    continue
                for j, o in enumerate(opts, 1):
                    print(f"    {j}. {o['title']} — {o['artists']} [score={o['_score']}] (id={o['id']})")
                pick = prompt("Elige índice o Enter para omitir", "").strip()
                if not pick:
                    continue
                try:
                    idx = int(pick) - 1
                    chosen = opts[idx]
                    vid = chosen.get("id")
                    if vid:
                        self.tidal.add_tracks_by_ids(
                            playlist_id=dest_pl, track_ids=[vid], avoid_duplicates=self.avoid_duplicates
                        )
                        inserted += 1
                    else:
                        print("  → opción sin id; omitido.")
                except Exception:
                    print("  → selección inválida; omitido.")
                continue

            # 's' u otra cosa → saltar
            print("  → omitido.")
            self._log_skipped_track(base, playlist_name, log_path= Path("blob", "skipped_tracks.txt"))


        # Enviar manuales en un único lote
        if pending_manual_ids:
            self.tidal.add_tracks_by_ids(
                pl=dest_pl,
                track_ids=pending_manual_ids,
                avoid_duplicates=self.avoid_duplicates,
            )
            inserted += len(pending_manual_ids)

        print(f"\nHecho. Insertados: {inserted} / {len(plan)} en '{playlist_name}'.\n")

    def _log_skipped_track(self, base: str, playlist_name: str, log_path: Path) -> None:
        """
        Registra en un .txt la pista omitida manualmente (acción 's' u otra no reconocida).
        Formato: [YYYY-mm-dd HH:MM:SS] <playlist_name> | <base>
        """
        try:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with log_path.open("a", encoding="utf-8") as f:
                f.write(f"{playlist_name} | {base}\n")
        except Exception as e:
            # No interrumpir el flujo por un fallo de escritura
            print(f"(aviso) No se pudo escribir el log de omitidos: {e}")


    def migrate_liked_songs(self) -> None:
        """
        Migra las canciones guardadas (Liked Songs) de Spotify como 'Favoritos' en TIDAL.
        - Resuelve candidatos con score y añade automáticamente los >= threshold.
        - Para los de baja confianza, aplica la misma lógica ‘sugerida/URL/listar/saltar’
          que ya usas con playlists (reutiliza la variable global PASAR_CANCIONES).
        """
        print("\n==== Migrando: Canciones guardadas (Me gusta) ====")
        tracks = self.spot.get_my_saved_tracks()
        if not tracks:
            print("(ninguna canción guardada)\n")
            return
        print(f"Pistas a resolver: {len(tracks)}")

        # 1) Resolver candidatos con score contra TIDAL
        plan: List[PlannedItem] = []
        for t in tracks:
            name = (t.get("name") or "").strip()
            name = re.sub(r"\([^()]*\)|--.*?--", "", name)
            artists = t.get("artists") or []
            artist = (artists[0] or {}).get("name") if artists else None
            tid, score, info = self.tidal.find_best_match_with_score(
                track=name, artist=artist, limit=self.per_query_limit
            )
            title = (info or {}).get("title")
            artists_txt = (info or {}).get("artists")
            plan.append(
                PlannedItem(
                    track=name, artist=artist, tidal_id=tid, score=score, title=title, artists=artists_txt
                )
            )

        # 2) Añadir a favoritos, pidiendo confirmación en low-score
        inserted = 0
        pending_manual_ids: List[int] = []

        for i, item in enumerate(plan, 1):
            base = f"{item.track} — {item.artist or ''}".strip()

            # Alta confianza → añadir directo a favoritos
            if item.tidal_id and item.score >= self.score_threshold:
                print(f"{i:>3}. ✅ {base} | {item.title} — {item.artists} [score={item.score}]")
                self.tidal.add_favorites_by_ids([item.tidal_id], avoid_duplicates=self.avoid_duplicates)
                inserted += 1
                continue

            # Baja confianza → interacción (igual que en playlists)
            sug = f"{item.title or '—'} — {item.artists or '—'}"
            print(f"{i:>3}. ❓ {base} | Sugerencia: {sug} | score={item.score}")
            if not PASAR_CANCIONES:
                action = prompt(
                    "Acción (A=añadir sugerida, U=URL/ID TIDAL manual, L=listar opciones, S=salta)",
                    "A" if item.tidal_id else "U",
                ).lower()
            else:
                action = PASAR_CANCIONES

            if action == "a" and item.tidal_id:
                self.tidal.add_favorites_by_ids([item.tidal_id], avoid_duplicates=self.avoid_duplicates)
                inserted += 1
                continue

            if action == "u":
                manual = prompt("Pega URL/ID de pista de TIDAL (o Enter para omitir)", "").strip()
                tid_manual = extract_tidal_track_id(manual)
                if not tid_manual:
                    print("  → Entrada inválida; omitido.")
                    continue
                pending_manual_ids.append(tid_manual)
                continue

            if action == "l":
                opts = self._preview_search_tidal(track=item.track, artist=item.artist, limit=self.per_query_limit)
                if not opts:
                    print("  → sin alternativas; omitido.")
                    continue
                for j, o in enumerate(opts, 1):
                    print(f"    {j}. {o['title']} — {o['artists']} [score={o['_score']}] (id={o['id']})")
                pick = prompt("Elige índice o Enter para omitir", "").strip()
                if not pick:
                    continue
                try:
                    idx = int(pick) - 1
                    chosen = opts[idx]
                    vid = chosen.get("id")
                    if vid:
                        self.tidal.add_favorites_by_ids([vid], avoid_duplicates=self.avoid_duplicates)
                        inserted += 1
                    else:
                        print("  → opción sin id; omitido.")
                except Exception:
                    print("  → selección inválida; omitido.")
                continue

            print("  → omitido.")

        if pending_manual_ids:
            self.tidal.add_favorites_by_ids(
                pending_manual_ids, avoid_duplicates=self.avoid_duplicates
            )
            inserted += len(pending_manual_ids)

        print(f"\nHecho. Insertados en Favoritos: {inserted} / {len(plan)}.\n")

    # ------------------------
    # Helpers
    # ------------------------
    def _preview_search_tidal(self, *, track: str, artist: Optional[str], limit: int = 5) -> List[Dict[str, Any]]:
        """Devuelve candidatos formateados de TIDAL (id, title, artists, _score)."""
        items = self.tidal.search_tracks_with_scores(track=track, artist=artist, limit=limit)
        out: List[Dict[str, Any]] = []
        if not items:
            return out
        for it in items:
            out.append({
                "id": it.get("id"),
                "title": it.get("title"),
                "artists": ", ".join(a.get("name") for a in (it.get("artists") or [])),
                "_score": int(it.get("_score", 0)),
            })
        return out


# ----------------------------
# Ejemplo CLI mínimo (opcional)
# ----------------------------
if __name__ == "__main__":
    print(
        "Este módulo expone la clase SpotifyToTidalMigrator.\n"
        "Instánciala con SpotifyLibrary y TidalLibrary y llama migrator.run()."
    )
