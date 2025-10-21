"""
SpotifyUserClient — Autenticación OAuth 2.0 (PKCE) + llamadas de API para info de usuario

Requisitos:
    - Python 3.8+
    - requests (pip install requests)

Antes de usar:
    1) Crea una app en https://developer.spotify.com/dashboard
    2) Añade un Redirect URI EXACTO a tu app: http://localhost:8080/callback
    3) Copia tu CLIENT_ID (no se necesita client secret con PKCE)

Ejemplo de uso rápido:

    from spotify_client import SpotifyUserClient

    client = SpotifyUserClient(
        client_id="TU_CLIENT_ID",
        scope="user-read-email user-read-private user-read-recently-played"
    )

    # Esto abrirá el navegador y levantará un servidor local temporal la primera vez
    client.authenticate()

    # Datos básicos del usuario actual
    me = client.get_current_user()
    print(me)

    # Playlists del usuario
    playlists = client.get_user_playlists(limit=20)
    print(playlists)

    # Historial reciente (si tienes el scope)
    recent = client.get_recently_played(limit=10)
    print(recent)

"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import random
import socket
import string
import threading
import time
import urllib.parse
import webbrowser
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Dict, Optional

import requests

from src.core.settings.spotify_service import get_spotify_settings

spotify_settings = get_spotify_settings()


SPOTIFY_AUTH_URL = "https://accounts.spotify.com/authorize"
SPOTIFY_TOKEN_URL = "https://accounts.spotify.com/api/token"
SPOTIFY_API_BASE = "https://api.spotify.com/v1"


@dataclass
class Token:
    access_token: str
    refresh_token: Optional[str]
    expires_at: float  # epoch seconds

    @property
    def is_expired(self) -> bool:
        # Añadimos un margen de 60 s para evitar expiraciones en medio de la llamada
        return time.time() >= (self.expires_at - 60)


class SpotifyUserClient:
    """Cliente de alto nivel para autenticación de usuario (OAuth 2.0 con PKCE)
    y llamadas a la Web API de Spotify centradas en información del usuario.

    - No requiere client_secret (usa PKCE)
    - Guarda/lee el token en disco para reutilizar sesiones

    Parámetros
    ----------
    client_id : str
        ID de cliente de tu app de Spotify.
    redirect_uri : str, opcional
        Debe coincidir EXACTAMENTE con el definido en el dashboard (por defecto
        'http://localhost:8080/callback').
    scope : str, opcional
        Scopes separados por espacios (p. ej. 'user-read-email user-read-private').
    token_path : str, opcional
        Ruta del archivo JSON donde se persiste el token (por defecto '.spotify_token.json').

    Métodos principales
    -------------------
    - authenticate(): ejecuta el flujo completo si no hay token válido.
    - get_current_user(): perfil del usuario autenticado.
    - get_user_playlists(limit=20, offset=0): playlists del usuario.
    - get_recently_played(limit=20, after=None, before=None): historial reciente.
    - api_request(method, endpoint, ...): llamada genérica a la Web API.
    """

    def __init__(
        self,
        client_id: str,
        redirect_uri: str = "http://localhost:8080/callback",
        scope: str = "user-read-email user-read-private",
        token_path: str = ".spotify_token.json",
    ) -> None:
        self.client_id = client_id
        self.redirect_uri = redirect_uri
        self.scope = scope
        self.token_path = token_path

        self._code_verifier: Optional[str] = None
        self._token: Optional[Token] = None

    # ------------------------
    # Autenticación (PKCE)
    # ------------------------
    def authenticate(self) -> None:
        """Asegura que hay un access token válido; si no, lanza el flujo PKCE.
        """
        # 1) Intenta cargar token desde disco
        self._token = self._load_token()
        if self._token and not self._token.is_expired:
            return

        # 2) Si hay refresh_token, intenta refrescar primero
        if self._token and self._token.refresh_token:
            try:
                self._refresh_token()
                self._save_token()
                return
            except Exception:
                # si falla, continuamos con autorización completa
                pass

        # 3) Autorización completa
        self._pkce_prepare()
        auth_url = self._build_auth_url()

        # Abrimos el navegador y esperamos el code con un servidor local temporal
        self._ensure_port_available(self._callback_port)
        server, code_holder = self._start_local_server_and_wait_for_code()

        webbrowser.open(auth_url)

        # Esperar a que llegue el code (con timeout)
        code = code_holder.wait_for_code(timeout=180)
        # Cerrar servidor
        server.shutdown()
        server.server_close()

        if not code:
            raise RuntimeError("No se recibió el 'code' de autorización a tiempo.")

        # Intercambiar code por token
        self._exchange_code_for_token(code)
        self._save_token()

    # ------------------------
    # Helpers PKCE + servidor local
    # ------------------------
    @property
    def _callback_port(self) -> int:
        parsed = urllib.parse.urlparse(self.redirect_uri)
        if parsed.scheme != "http" or parsed.hostname not in {"localhost", "127.0.0.1"}:
            raise ValueError(
                "El redirect_uri debe ser http://localhost:<puerto>/... para el servidor local"
            )
        return parsed.port or 80

    def _pkce_prepare(self) -> None:
        verifier = self._gen_code_verifier()
        self._code_verifier = verifier

    @staticmethod
    def _gen_code_verifier(length: int = 64) -> str:
        # RFC 7636: 43..128 chars, [A-Za-z0-9-._~]
        alphabet = string.ascii_letters + string.digits + "-._~"
        return "".join(random.choice(alphabet) for _ in range(length))

    @staticmethod
    def _code_challenge(verifier: str) -> str:
        digest = hashlib.sha256(verifier.encode("ascii")).digest()
        return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")

    def _build_auth_url(self) -> str:
        if not self._code_verifier:
            raise RuntimeError("PKCE no inicializado")
        params = {
            "client_id": self.client_id,
            "response_type": "code",
            "redirect_uri": self.redirect_uri,
            "scope": self.scope,
            "code_challenge_method": "S256",
            "code_challenge": self._code_challenge(self._code_verifier),
            # opcional: state
        }
        return f"{SPOTIFY_AUTH_URL}?{urllib.parse.urlencode(params)}"

    class _CodeHolder:
        def __init__(self):
            self._code: Optional[str] = None
            self._event = threading.Event()

        def set_code(self, code: str) -> None:
            self._code = code
            self._event.set()

        def wait_for_code(self, timeout: int = 180) -> Optional[str]:
            ok = self._event.wait(timeout)
            return self._code if ok else None

    def _start_local_server_and_wait_for_code(self):
        code_holder = self._CodeHolder()
        redirect_path = urllib.parse.urlparse(self.redirect_uri).path or "/callback"

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802 (nombre según BaseHTTPRequestHandler)
                parsed = urllib.parse.urlparse(self.path)
                if parsed.path != redirect_path:
                    self.send_response(404)
                    self.end_headers()
                    self.wfile.write(b"Not Found")
                    return
                qs = urllib.parse.parse_qs(parsed.query)
                if "code" in qs:
                    code_holder.set_code(qs["code"][0])
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.end_headers()
                    self.wfile.write(
                        b"<html><body><h2>Autorizaci\xc3\xb3n completada</h2>\n"
                        b"<p>Ya puedes volver a la aplicaci\xc3\xb3n. Esta ventana se puede cerrar.</p>"
                        b"</body></html>"
                    )
                else:
                    self.send_response(400)
                    self.end_headers()
                    self.wfile.write(b"Missing 'code' parameter")

            def log_message(self, fmt, *args):
                # Silenciar logs en consola
                return

        server = HTTPServer(("127.0.0.1", self._callback_port), Handler)

        # Ejecutar servidor en hilo dedicado
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
        return server, code_holder

    @staticmethod
    def _ensure_port_available(port: int) -> None:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
            except OSError:
                raise OSError(
                    f"El puerto {port} parece ocupado. Cierra el proceso que lo use o cambia el redirect_uri."
                )

    def _exchange_code_for_token(self, code: str) -> None:
        if not self._code_verifier:
            raise RuntimeError("Falta code_verifier para PKCE")

        data = {
            "client_id": self.client_id,
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self.redirect_uri,
            "code_verifier": self._code_verifier,
        }
        resp = requests.post(SPOTIFY_TOKEN_URL, data=data, timeout=20)
        self._raise_for_token_error(resp)
        payload = resp.json()
        self._token = Token(
            access_token=payload["access_token"],
            refresh_token=payload.get("refresh_token"),
            expires_at=time.time() + int(payload.get("expires_in", 3600)),
        )

    def _refresh_token(self) -> None:
        if not (self._token and self._token.refresh_token):
            raise RuntimeError("No hay refresh_token disponible")
        data = {
            "client_id": self.client_id,
            "grant_type": "refresh_token",
            "refresh_token": self._token.refresh_token,
        }
        resp = requests.post(SPOTIFY_TOKEN_URL, data=data, timeout=20)
        self._raise_for_token_error(resp)
        payload = resp.json()
        # En refresh, Spotify puede devolver un nuevo refresh_token o no
        self._token = Token(
            access_token=payload["access_token"],
            refresh_token=payload.get("refresh_token", self._token.refresh_token),
            expires_at=time.time() + int(payload.get("expires_in", 3600)),
        )

    @staticmethod
    def _raise_for_token_error(resp: requests.Response) -> None:
        if resp.status_code != 200:
            try:
                detail = resp.json()
            except Exception:
                detail = resp.text
            raise RuntimeError(f"Error de token ({resp.status_code}): {detail}")

    # ------------------------
    # Persistencia del token
    # ------------------------
    def _load_token(self) -> Optional[Token]:
        if not os.path.exists(self.token_path):
            return None
        try:
            with open(self.token_path, "r", encoding="utf-8") as f:
                payload = json.load(f)
            return Token(
                access_token=payload["access_token"],
                refresh_token=payload.get("refresh_token"),
                expires_at=float(payload["expires_at"]),
            )
        except Exception:
            return None

    def _save_token(self) -> None:
        if not self._token:
            return
        payload = {
            "access_token": self._token.access_token,
            "refresh_token": self._token.refresh_token,
            "expires_at": self._token.expires_at,
        }
        with open(self.token_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)

    # ------------------------
    # Llamadas a la Web API
    # ------------------------
    def _ensure_access_token(self) -> None:
        if not self._token:
            raise RuntimeError("No hay token. Llama primero a authenticate().")
        if self._token.is_expired:
            if self._token.refresh_token:
                self._refresh_token()
                self._save_token()
            else:
                # Si no hay refresh token, hay que reautenticar
                self.authenticate()

    def api_request(
        self,
        method: str,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None,
        json_body: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Hace una petición autenticada a la Web API.

        endpoint: '/me', '/me/playlists', etc. (con o sin prefijo '/v1')
        """
        self._ensure_access_token()
        url = endpoint
        if not endpoint.startswith("http"):
            endpoint = endpoint.lstrip("/")
            if not endpoint.startswith("v1/"):
                url = f"{SPOTIFY_API_BASE}/{endpoint}"
            else:
                url = f"https://api.spotify.com/{endpoint}"

        headers = {"Authorization": f"Bearer {self._token.access_token}"}
        resp = requests.request(
            method=method.upper(),
            url=url,
            headers=headers,
            params=params,
            json=json_body,
            timeout=20,
        )
        if resp.status_code == 401:
            # token expirado o inválido; intentar refrescar una vez
            if self._token and self._token.refresh_token:
                self._refresh_token()
                self._save_token()
                headers["Authorization"] = f"Bearer {self._token.access_token}"
                resp = requests.request(
                    method=method.upper(),
                    url=url,
                    headers=headers,
                    params=params,
                    json=json_body,
                    timeout=20,
                )
        resp.raise_for_status()
        return resp.json() if resp.content else {}

    # ------------------------
    # Atajos centrados en usuario
    # ------------------------
    def get_current_user(self) -> Dict[str, Any]:
        """Devuelve el perfil del usuario autenticado (/me).
        Requiere scope: user-read-email o user-read-private para datos más completos.
        """
        return self.api_request("GET", "/me")

    def get_user_playlists(self, limit: int = 20, offset: int = 0) -> Dict[str, Any]:
        return self.api_request(
            "GET", "/me/playlists", params={"limit": limit, "offset": offset}
        )

    def get_recently_played(
        self,
        limit: int = 20,
        after: Optional[int] = None,
        before: Optional[int] = None,
    ) -> Dict[str, Any]:
        params: Dict[str, Any] = {"limit": limit}
        if after is not None:
            params["after"] = after  # epoch ms
        if before is not None:
            params["before"] = before  # epoch ms
        return self.api_request("GET", "/me/player/recently-played", params=params)


# Si ejecutas este archivo directamente, lanzamos un mini demo CLI
if __name__ == "__main__":
    

    client = SpotifyUserClient(
        client_id=spotify_settings.client_id, redirect_uri="http://127.0.0.1:8080/callback"
    )
    client.authenticate()
    me = client.get_current_user()
    print(json.dumps(me, indent=2, ensure_ascii=False))
