"""
TidalUserClient — login por Device Code (tidalapi) + utilidades de usuario/playlist/búsqueda.

Requisitos:
    pip install tidalapi requests

Flujo de login:
    - Intenta cargar una sesión OAuth guardada en disco (.tidal_oauth.json)
    - Si no existe o no es válida, usa login OAuth "device" (te muestra URL + código)
    - Tras enlazar la cuenta, guarda el token en disco para siguientes ejecuciones

Métodos principales:
    - authenticate()
    - get_current_user()
    - get_user_playlists(limit=50, offset=0)
    - get_playlist_tracks(playlist_id, limit=100, offset=0)
    - create_playlist(title, description="")
    - add_tracks_to_playlist(playlist_id, track_ids)
    - search_tracks(query, limit=20, offset=0)

Limitaciones:
    - La API oficial de TIDAL no es pública; se apoya en la librería 'tidalapi'.
    - La estructura exacta de los objetos (User/Playlist/Track) es la de 'tidalapi'.
"""

from __future__ import annotations

import json
import os
import base64
from typing import Any, Dict, List, Iterable

import tidalapi


class TidalUserClient:
    def __init__(self, token_path: str = ".tidal_oauth.json") -> None:
        self.token_path = token_path
        self.session = tidalapi.Session()

    # ------------------------
    # Autenticación
    # ------------------------
    def authenticate(self) -> None:
        """
        Asegura que hay sesión iniciada:
          1) Intenta cargar token OAuth desde disco
          2) Si falla o no es válido, hace device-login y guarda la sesión
        """
        # 1) Intentar cargar sesión previa
        if self._load_oauth_if_possible() and self._is_logged():
            return

        # 2) Device login (acepta bool/dict/None según versión)
        print("Iniciando login de TIDAL (OAuth device) ...")
        ok = self._device_login()
        if not ok and not self._is_logged():
            raise RuntimeError("No se pudo iniciar sesión en TIDAL.")

        # 3) Guardar sesión si es posible
        self._save_oauth_if_possible()

    def list_all_user_playlists(self) -> List[Dict[str, Any]]:
        """Devuelve TODAS las playlists del usuario (internamente puede paginar)."""
        self._ensure_logged()
        user = self.session.user
        try:
            playlists_iter = user.playlists()      # algunas versiones: método
        except TypeError:
            playlists_iter = user.playlists        # otras: propiedad iterable
        pls = list(playlists_iter)                 # fuerza a traerlas todas

        out = []
        for p in pls:
            out.append({
                "id": getattr(p, "id", None),
                "p":p,
                "title": getattr(p, "name", None) or getattr(p, "title", None),
                "description": getattr(p, "description", None),
                "items_count": getattr(p, "num_tracks", None) or getattr(p, "numberOfTracks", None),
            })
        return out


    def list_all_playlist_tracks(self, pl) -> List[Dict[str, Any]]:
        """Devuelve TODAS las pistas de una playlist (internamente puede paginar)."""
        self._ensure_logged()
        try:
            tracks_iter = pl.tracks()   # método
        except TypeError:
            tracks_iter = pl.tracks     # propiedad
        tracks = list(tracks_iter)

        out = []
        for t in tracks:
            artists = []
            for a in getattr(t, "artists", []) or []:
                artists.append({"id": getattr(a, "id", None), "name": getattr(a, "name", None)})
            out.append({
                "id": getattr(t, "id", None),
                "title": getattr(t, "name", None) or getattr(t, "title", None),
                "duration": getattr(t, "duration", None),
                "album": {
                    "id": getattr(getattr(t, "album", None), "id", None),
                    "title": getattr(getattr(t, "album", None), "name", None) or getattr(getattr(t, "album", None), "title", None),
                },
                "artists": artists,
            })
        return out



    # --- helpers de login ---
    def _is_logged(self) -> bool:
        """Devuelve True si la sesión está operativa."""
        try:
            # Algunas versiones exponen session.check_login()
            if hasattr(self.session, "check_login"):
                return bool(self.session.check_login())
            # Forzar acceso al usuario; si no hay login, suele lanzar
            _ = self.session.user
            return True
        except Exception:
            return False

    def _device_login(self) -> bool:
        """Ejecuta el flujo device y devuelve True si parece logueado."""
        import webbrowser

        def handler(*args):
            # tidalapi puede llamar: (url, code[, expires]) o solo (url, code)
            url = args[0]
            code = args[1] if len(args) > 1 else url.rsplit("/", 1)[-1]
            print(f"Visit {url} to log in, the code will expire soon.")
            print(f"Código a introducir: {code}")
            try:
                webbrowser.open(url)
            except Exception:
                pass

        result = None
        if hasattr(self.session, "login_oauth_simple"):
            # Algunas versiones aceptan function=handler
            try:
                result = self.session.login_oauth_simple(function=handler)
            except TypeError:
                # fallback si no acepta 'function'
                result = self.session.login_oauth_simple()
        elif hasattr(self.session, "login_oauth"):
            result = self.session.login_oauth(function=handler)
        else:
            raise RuntimeError("Actualiza tidalapi: no hay login OAuth disponible.")

        # Normalizar resultado:
        # - bool -> úsalo tal cual
        # - dict (usuario) -> ok
        # - None -> verifica sesión
        if isinstance(result, bool):
            return result
        if isinstance(result, dict):
            # Tu caso: {'id': ..., 'username': ...}
            return True
        # Último recurso: comprobar estado real de la sesión
        return self._is_logged()

    # ------------------------
    # Usuario y Playlists
    # ------------------------
    def get_current_user(self) -> Dict[str, Any]:
        """Devuelve dict con info básica del usuario actual."""
        self._ensure_logged()
        u = self.session.user
        return {
            "id": getattr(u, "id", None),
            "username": getattr(u, "username", None),
            "country_code": getattr(self.session, "country_code", None),
            "product": getattr(u, "subscription", None),
        }

    def get_user_playlists(self, limit: int = 50, offset: int = 0) -> List[Dict[str, Any]]:
        """Lista playlists del usuario autenticado."""
        self._ensure_logged()
        user = self.session.user
        pls = user.playlists(limit=limit, offset=offset)
        out: List[Dict[str, Any]] = []
        for p in pls:
            out.append({
                "id": getattr(p, "id", None),
                "title": getattr(p, "name", None) or getattr(p, "title", None),
                "description": getattr(p, "description", None),
                "items_count": getattr(p, "num_tracks", None) or getattr(p, "numberOfTracks", None),
            })
        return out

    def get_playlist_tracks(self, playlist_id: str, limit: int = 100, offset: int = 0) -> List[Dict[str, Any]]:
        """Devuelve las pistas de una playlist (id)."""
        self._ensure_logged()
        pl = tidalapi.playlist.Playlist(self.session, playlist_id)
        tracks = pl.tracks(limit=limit, offset=offset)
        out: List[Dict[str, Any]] = []
        for t in tracks:
            artists = []
            try:
                for a in getattr(t, "artists", []) or []:
                    artists.append({"id": getattr(a, "id", None), "name": getattr(a, "name", None)})
            except Exception:
                pass
            out.append({
                "id": getattr(t, "id", None),
                "title": getattr(t, "name", None) or getattr(t, "title", None),
                "duration": getattr(t, "duration", None),
                "album": {
                    "id": getattr(getattr(t, "album", None), "id", None),
                    "title": getattr(getattr(t, "album", None), "name", None) or getattr(getattr(t, "album", None), "title", None),
                },
                "artists": artists,
            })
        return out

    def create_playlist(self, title: str, description: str = ""):
        """Crea una playlist en la cuenta del usuario."""
        self._ensure_logged()
        user = self.session.user
        pl = user.create_playlist(title, description)  # devuelve tidalapi.playlist.Playlist
        return pl

    def add_tracks_to_playlist(self, pl, track_ids: List[int]) -> None:
        """Añade una lista de track IDs a una playlist."""
        self._ensure_logged()

        # Normaliza IDs (acepta int/str con dígitos) y descarta valores vacíos
        norm_ids: List[int] = []
        for t in track_ids or []:
            if isinstance(t, int):
                norm_ids.append(t)
            elif isinstance(t, str) and t.strip().isdigit():
                norm_ids.append(int(t.strip()))

        if not norm_ids:
            return  # nada que hacer

        # Elimina duplicados preservando orden
        norm_ids = self._dedupe_preserve_order(norm_ids)


        # Enviar en lotes por seguridad (p.ej., 100 por petición)
        BATCH_SIZE = 100
        for i in range(0, len(norm_ids), BATCH_SIZE):
            chunk = norm_ids[i:i + BATCH_SIZE]
            try:
                # tidalapi.Playlist.add admite lista de ints
                pl.add(chunk)
            except Exception as e:
                # Re-lanza con contexto útil
                raise RuntimeError(
                    f"Fallo al añadir pistas a la playlist {pl.id} "
                    f"en el rango [{i}, {i+len(chunk)}): {e}"
                ) from e


    def set_playlist_image(self, pl, image_bytes: bytes) -> bool:
        """
        INTENTO de subir la portada a una playlist de TIDAL.
        Devuelve True si alguna firma conocida funciona; False si no hay soporte.
        - Prueba métodos habituales que algunas builds de tidalapi podrían exponer.
        - Prueba con bytes y con base64 según firma.
        - No lanza excepción (salvo errores inesperados); devuelve False.
        """
        self._ensure_logged()
        if not image_bytes:
            return False

        # Candidatos de método y payload
        methods = [
            ("set_image", image_bytes),
            ("set_image", base64.b64encode(image_bytes).decode("ascii")),
            ("set_cover", image_bytes),
            ("set_cover", base64.b64encode(image_bytes).decode("ascii")),
            ("upload_image", image_bytes),
            ("upload_image", base64.b64encode(image_bytes).decode("ascii")),
            ("upload_cover", image_bytes),
            ("upload_cover", base64.b64encode(image_bytes).decode("ascii")),
        ]
        for meth, payload in methods:
            fn = getattr(pl, meth, None)
            if not callable(fn):
                continue
            try:
                # Algunas firmas aceptan (bytes) o (str base64) o (mime, bytes)
                try:
                    fn(payload)
                    return True
                except TypeError:
                    # segundo intento común: (mime, bytes)
                    fn("image/jpeg", payload if isinstance(payload, (bytes, bytearray)) else image_bytes)
                    return True
            except Exception:
                # seguimos probando
                continue
        return False
    

    def _get_favorites_obj(self):
        """
        Devuelve el objeto de 'favoritos' del usuario, tolerando diferencias de versión.
        """
        self._ensure_logged()
        user = self.session.user
        fav = getattr(user, "favorites", None)
        if fav is not None:
            return fav
        # Fallback común en versiones antiguas:
        try:
            Favorites = getattr(tidalapi, "Favorites", None)
            if Favorites is not None:
                return Favorites(self.session, getattr(user, "id", None))
        except Exception:
            pass
        raise RuntimeError("No se pudo acceder a los favoritos del usuario en 'tidalapi'.")

    def list_all_favorite_tracks(self) -> List[Dict[str, Any]]:
        """
        Devuelve TODAS las canciones marcadas como favoritas del usuario.
        Estructura homogénea: {id, title, duration, album:{id,title}, artists:[{id,name}]}
        """
        fav = self._get_favorites_obj()
        try:
            tracks_iter = fav.tracks()   # método habitual
        except TypeError:
            tracks_iter = fav.tracks     # propiedad iterable
        tracks = list(tracks_iter or [])

        out = []
        for t in tracks:
            artists = []
            for a in getattr(t, "artists", []) or []:
                artists.append({"id": getattr(a, "id", None), "name": getattr(a, "name", None)})
            out.append({
                "id": getattr(t, "id", None),
                "title": getattr(t, "name", None) or getattr(t, "title", None),
                "duration": getattr(t, "duration", None),
                "album": {
                    "id": getattr(getattr(t, "album", None), "id", None),
                    "title": getattr(getattr(t, "album", None), "name", None) or getattr(getattr(t, "album", None), "title", None),
                },
                "artists": artists,
            })
        return out

    def add_favorite_tracks(self, track_ids: List[int]) -> None:
        """
        Marca como favoritas las pistas indicadas. Hace de-duplicado básico y
        añade una a una (algunas versiones de tidalapi no soportan lote).
        """
        self._ensure_logged()
        fav = self._get_favorites_obj()

        # Normaliza y de-dup
        norm_ids: List[int] = []
        for t in track_ids or []:
            if isinstance(t, int):
                norm_ids.append(t)
            elif isinstance(t, str) and t.strip().isdigit():
                norm_ids.append(int(t.strip()))
        if not norm_ids:
            return
        seen = set()
        norm_ids = [x for x in norm_ids if not (x in seen or seen.add(x))]

        # Añadir uno a uno (API suele ser add_track(id))
        for tid in norm_ids:
            try:
                if hasattr(fav, "add_track"):
                    fav.add_track(tid)
                elif hasattr(fav, "add"):
                    # algunas versiones aceptan add(track_id) o add([ids])
                    try:
                        fav.add(tid)
                    except TypeError:
                        fav.add([tid])
                else:
                    raise RuntimeError("El objeto 'favorites' no expone add_track/add.")
            except Exception as e:
                raise RuntimeError(f"Fallo al añadir favorito (track_id={tid}): {e}") from e
                  
    def _dedupe_preserve_order(self, ids: Iterable[int]) -> List[int]:
        seen = set()
        out = []
        for x in ids:
            if x not in seen:
                seen.add(x)
                out.append(x)
        return out


    # ------------------------
    # Búsqueda
    # ------------------------
    def search_tracks(self, query: str, limit: int = 20, offset: int = 0) -> List[Dict[str, Any]]:
        """Busca pistas por texto libre y devuelve una lista simplificada.
        Soporta varias firmas de 'tidalapi' (enum/bool/dict)."""
        self._ensure_logged()

        # 1) Intenta con el enum SearchType.TRACKS (versiones nuevas)
        res = None
        try:
            from tidalapi import media  # type: ignore
            if hasattr(media, "SearchType"):
                try:
                    res = self.session.search(media.SearchType.TRACKS, query, limit=limit, offset=offset)
                except TypeError:
                    # algunas builds usan 'top_level' en vez de offset
                    res = self.session.search(media.SearchType.TRACKS, query, limit=limit, top_level=offset)
        except Exception:
            pass

        # 2) Intenta con la API antigua string ("tracks")
        if res is None:
            try:
                res = self.session.search("tracks", query, limit=limit, offset=offset)
            except Exception:
                pass

        # 3) Intenta con la variante basada en modelos (muy antigua)
        if res is None:
            try:
                # algunas versiones aceptan: search(query, models=[tidalapi.Track], ...)
                TrackModel = getattr(tidalapi, "Track", None)
                if TrackModel is not None:
                    res = self.session.search(query, models=[TrackModel], limit=limit, offset=offset)
            except Exception:
                pass

        if res is None:
            raise RuntimeError("No se pudo realizar la búsqueda: la versión de 'tidalapi' no soporta ninguna firma conocida.")

        # Normalizar resultados: puede venir como dict o como objeto con atributo .tracks
        tracks = []
        try:
            # objeto con atributo
            tracks = getattr(res, "tracks", None) or []
        except Exception:
            tracks = []

        if not tracks and isinstance(res, dict):
            tracks = res.get("tracks", []) or []

        # Convertir a salida homogénea
        out: List[Dict[str, Any]] = []
        for t in tracks:
            artists = []
            try:
                for a in getattr(t, "artists", []) or []:
                    artists.append({"id": getattr(a, "id", None), "name": getattr(a, "name", None)})
            except Exception:
                pass
            out.append({
                "id": getattr(t, "id", None),
                "title": getattr(t, "name", None) or getattr(t, "title", None),
                "duration": getattr(t, "duration", None),
                "album": {
                    "id": getattr(getattr(t, "album", None), "id", None),
                    "title": getattr(getattr(t, "album", None), "name", None) or getattr(getattr(t, "album", None), "title", None),
                },
                "artists": artists,
            })
        return out


    # ------------------------
    # Helpers internos
    # ------------------------
    def _ensure_logged(self) -> None:
        try:
            _ = self.session.user
        except Exception:
            raise RuntimeError("No hay sesión de TIDAL. Llama primero a authenticate().")

    def _load_oauth_if_possible(self) -> bool:
        """Carga sesión OAuth desde disco si la versión de tidalapi lo soporta."""
        if not os.path.exists(self.token_path):
            return False
        try:
            with open(self.token_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            return False

        # Algunas versiones exponen 'load_oauth_session'
        if hasattr(self.session, "load_oauth_session"):
            try:
                ok = self.session.load_oauth_session(data)
                if ok:
                    return True
            except Exception:
                pass

        # Fallback: algunas versiones usan 'oauth_token' en session; lo intentamos inyectar
        try:
            for k, v in data.items():
                setattr(self.session, k, v)
            # si esto no basta, el check_login fallará y haremos login
            return True
        except Exception:
            return False

    def _save_oauth_if_possible(self) -> None:
        """Guarda los datos de OAuth expuestos por la sesión (si existen)."""
        payload: Dict[str, Any] = {}
        # Prioridad: si la sesión exporta un dict de OAuth, úsalo
        for key in ("oauth_token", "oauth_session", "token", "auth"):
            val = getattr(self.session, key, None)
            if isinstance(val, dict):
                payload = val
                break

        # Si no, serializamos los campos más comunes si existen
        if not payload:
            for attr in ("token_type", "access_token", "refresh_token", "expires_in", "expiry_time", "user_id"):
                if hasattr(self.session, attr):
                    payload[attr] = getattr(self.session, attr)

        if not payload:
            # No pasa nada; iniciarás login la próxima vez.
            return

        try:
            with open(self.token_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2, ensure_ascii=False)
        except Exception:
            pass


# ------------------------
# Demo CLI mínimo
# ------------------------
if __name__ == "__main__":
    client = TidalUserClient()
    client.authenticate()

    print("Usuario:", client.get_current_user())

    # Crear playlist y meter 2 canciones (IDs de ejemplo — ¡pon IDs reales!)
    new_pl = client.create_playlist("Migrated — Demo", "Playlist creada desde API")
    client.add_tracks_to_playlist(new_pl, [12345678, 23456789])

    # Buscar
    tracks = client.search_tracks("Coldplay Viva la Vida", limit=5)
    for t in tracks:
        print(t["id"], "-", t["title"], "—", ", ".join(a["name"] for a in t["artists"]))
