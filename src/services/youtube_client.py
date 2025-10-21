"""
YouTubeMusicUserClient — Autenticación Google OAuth 2.0 (PKCE) + llamadas a YouTube Data API v3

⚠️ Nota: No existe API oficial separada para "YouTube Music". La biblioteca/música del usuario se
representa como playlists/vídeos en la API de YouTube Data v3. Con los permisos adecuados puedes
leer tus playlists (incluida la de "Me gusta", si tu cuenta lo permite) y gestionarlas.

Requisitos:
    - Python 3.8+
    - requests (pip install requests)

Antes de usar:
    1) Entra en https://console.cloud.google.com/ → crea un proyecto.
    2) Habilita **YouTube Data API v3**.
    3) Crea credenciales OAuth: tipo **Desktop App** (recomendado) o **Web App**.
       - Si eliges Desktop: no necesitas registrar manualmente redirect URIs.
       - Si eliges Web: añade EXACTO: http://127.0.0.1:8080/google_callback
    4) Copia tu **client_id** (para PKCE no usamos client_secret).

Scopes útiles:
    - youtube.readonly  → leer playlists y sus items
    - youtube           → leer/crear/editar playlists (permiso amplio)

Ejemplo de uso rápido:

    from ytmusic_client import YouTubeMusicUserClient

    client = YouTubeMusicUserClient(
        client_id="TU_CLIENT_ID",
        scope="https://www.googleapis.com/auth/youtube.readonly"
    )
    client.authenticate()

    me = client.get_current_channel()
    print(me)

    pls = client.get_my_playlists(max_results=25)
    print(pls)

    # Items de una playlist concreta
    items = client.get_playlist_items(playlist_id="PLxxxx", max_results=50)
    print(items)

    # (Opcional) Vídeos que te han gustado (puede requerir permisos adicionales o no estar disponible
    # según la configuración de tu cuenta)
    liked = client.get_liked_videos(max_results=25)
    print(liked)
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

from src.core.settings.youtube_service import get_youtube_settings


youtube_settings = get_youtube_settings()

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
YT_API_BASE = "https://www.googleapis.com/youtube/v3"


@dataclass
class Token:
    access_token: str
    refresh_token: Optional[str]
    expires_at: float  # epoch seconds

    @property
    def is_expired(self) -> bool:
        return time.time() >= (self.expires_at - 60)


class YouTubeMusicUserClient:
    """Cliente OAuth 2.0 (PKCE) para YouTube Data API v3 con utilidades
    centradas en info del usuario, playlists y sus elementos.

    Google soporta PKCE → no necesitamos client_secret.
    """

    # 1) __init__: añade client_secret opcional
    def __init__(self, client_id: str, redirect_uri: str = "http://127.0.0.1:8080/google_callback",
                scope: str = "https://www.googleapis.com/auth/youtube.readonly",
                token_path: str = ".youtube_token.json",
                client_secret: Optional[str] = None):
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri
        self.scope = scope
        self.token_path = token_path
        self._code_verifier = None
        self._token = None

        # 2) En _exchange_code_for_token: agrega client_secret si existe
        


    # ------------------------
    # Autenticación (PKCE)
    # ------------------------
    def authenticate(self) -> None:
        self._token = self._load_token()
        if self._token and not self._token.is_expired:
            return

        if self._token and self._token.refresh_token:
            try:
                self._refresh_token()
                self._save_token(); return
            except Exception:
                pass

        self._pkce_prepare()
        auth_url = self._build_auth_url()

        self._ensure_port_available(self._callback_port)
        server, code_holder = self._start_local_server_and_wait_for_code()
        webbrowser.open(auth_url)

        code = code_holder.wait_for_code(timeout=300)
        server.shutdown(); server.server_close()
        if not code:
            raise RuntimeError("No se recibió el 'code' de autorización a tiempo.")

        self._exchange_code_for_token(code)
        self._save_token()

    @property
    def _callback_port(self) -> int:
        parsed = urllib.parse.urlparse(self.redirect_uri)
        if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost"}:
            raise ValueError("El redirect_uri debe ser un loopback http://127.0.0.1:<puerto>/...")
        return parsed.port or 80

    def _pkce_prepare(self) -> None:
        self._code_verifier = self._gen_code_verifier()

    @staticmethod
    def _gen_code_verifier(length: int = 64) -> str:
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
            # Importante para refresh_token
            "access_type": "offline",
            "prompt": "consent",
            "include_granted_scopes": "true",
        }
        return f"{GOOGLE_AUTH_URL}?{urllib.parse.urlencode(params)}"

    class _CodeHolder:
        def __init__(self):
            self._code: Optional[str] = None
            self._event = threading.Event()
        def set_code(self, code: str) -> None:
            self._code = code; self._event.set()
        def wait_for_code(self, timeout: int = 300) -> Optional[str]:
            ok = self._event.wait(timeout)
            return self._code if ok else None

    def _start_local_server_and_wait_for_code(self):
        code_holder = self._CodeHolder()
        redirect_path = urllib.parse.urlparse(self.redirect_uri).path or "/google_callback"

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa
                parsed = urllib.parse.urlparse(self.path)
                if parsed.path != redirect_path:
                    self.send_response(404); self.end_headers(); self.wfile.write(b"Not Found"); return
                qs = urllib.parse.parse_qs(parsed.query)
                if "code" in qs:
                    code_holder.set_code(qs["code"][0])
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.end_headers()
                    self.wfile.write(
                        b"<html><body><h2>Autorizaci\xc3\xb3n completada (Google)</h2>"
                        b"<p>Ya puedes volver a la aplicaci\xc3\xb3n. Esta ventana se puede cerrar.</p>"
                        b"</body></html>"
                    )
                else:
                    self.send_response(400); self.end_headers(); self.wfile.write(b"Missing 'code' parameter")
            def log_message(self, *_):
                return

        server = HTTPServer(("127.0.0.1", self._callback_port), Handler)
        t = threading.Thread(target=server.serve_forever, daemon=True); t.start()
        return server, code_holder

    @staticmethod
    def _ensure_port_available(port: int) -> None:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
            except OSError:
                raise OSError(f"El puerto {port} está ocupado. Cierra el proceso o cambia el redirect_uri.")

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
        if self.client_secret:
            data["client_secret"] = self.client_secret
        resp = requests.post(GOOGLE_TOKEN_URL, data=data, timeout=30)
        if resp.status_code != 200:
            try:
                detail = resp.json()
            except Exception:
                detail = resp.text
            raise RuntimeError(f"Error de token ({resp.status_code}): {detail}")
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
        resp = requests.post(GOOGLE_TOKEN_URL, data=data, timeout=30)
        if resp.status_code != 200:
            try:
                detail = resp.json()
            except Exception:
                detail = resp.text
            raise RuntimeError(f"Error al refrescar token ({resp.status_code}): {detail}")
        payload = resp.json()
        self._token = Token(
            access_token=payload["access_token"],
            refresh_token=self._token.refresh_token,
            expires_at=time.time() + int(payload.get("expires_in", 3600)),
        )

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
        with open(self.token_path, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "access_token": self._token.access_token,
                    "refresh_token": self._token.refresh_token,
                    "expires_at": self._token.expires_at,
                },
                f,
                indent=2,
            )

    # ------------------------
    # Llamadas a YouTube Data API
    # ------------------------
    def _ensure_access_token(self) -> None:
        if not self._token:
            raise RuntimeError("No hay token. Llama primero a authenticate().")
        if self._token.is_expired:
            if self._token.refresh_token:
                self._refresh_token(); self._save_token()
            else:
                self.authenticate()

    def api_request(
        self,
        method: str,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None,
        json_body: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        self._ensure_access_token()
        headers = {"Authorization": f"Bearer {self._token.access_token}"}
        url = endpoint if endpoint.startswith("http") else f"{YT_API_BASE}/{endpoint.lstrip('/')}"
        resp = requests.request(method.upper(), url, headers=headers, params=params, json=json_body, timeout=30)
        if not resp.ok:
            detail = None
            try:
                detail = resp.json()
            except Exception:
                detail = resp.text
            # incluye 'errors[0].reason' si existe (quotaExceeded, accessNotConfigured, etc.)
            raise requests.HTTPError(f"{resp.status_code} {resp.reason} for url: {resp.url} | detail={detail}")
        return resp.json() if resp.content else {}

    # ------------------------
    # Atajos centrados en usuario/playlist
    # ------------------------
    def get_current_channel(self) -> Dict[str, Any]:
        # 'mine=true' requiere el scope de YouTube
        return self.api_request("/channels", params={"part": "id,snippet", "mine": "true"})

    def get_my_playlists(self, max_results: int = 25, page_token: Optional[str] = None) -> Dict[str, Any]:
        params = {"part": "id,snippet,contentDetails", "mine": "true", "maxResults": max_results}
        if page_token:
            params["pageToken"] = page_token
        return self.api_request("/playlists", params=params)

    def get_playlist_items(self, playlist_id: str, max_results: int = 50, page_token: Optional[str] = None) -> Dict[str, Any]:
        params = {"part": "id,snippet,contentDetails", "playlistId": playlist_id, "maxResults": max_results}
        if page_token:
            params["pageToken"] = page_token
        return self.api_request("/playlistItems", params=params)

    def get_liked_videos(self, max_results: int = 25, page_token: Optional[str] = None) -> Dict[str, Any]:
        """Devuelve los vídeos marcados con 'Me gusta'. Puede estar restringido según la cuenta.
        Internamente es la playlist especial 'LL'.
        """
        params = {"part": "id,snippet,contentDetails", "playlistId": "LL", "maxResults": max_results}
        if page_token:
            params["pageToken"] = page_token
        return self.api_request("/playlistItems", params=params)


# Demo CLI
if __name__ == "__main__":
    import argparse

  

    client = YouTubeMusicUserClient(
        client_id=youtube_settings.client_id,
        redirect_uri="http://127.0.0.1:8080/google_callback",
        client_secret=youtube_settings.client_secret,
    )
    client.authenticate()
    me = client.get_current_channel()
    print(json.dumps(me, indent=2, ensure_ascii=False))
