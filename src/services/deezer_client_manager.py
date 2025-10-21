
"""
DeezerUserClient — Autenticación OAuth 2.0 (Authorization Code) + llamadas de API para info de usuario

Requisitos:
    - Python 3.8+
    - requests (pip install requests)

Antes de usar:
    1) Crea una app en https://developers.deezer.com/myapps
    2) Añade un Redirect URI EXACTO a tu app: http://127.0.0.1:8080/deezer_callback
    3) Copia tu APP ID (client_id) y APP SECRET (client_secret)

Notas importantes (Deezer):
    - Deezer NO soporta PKCE; requiere client_secret en el intercambio de código.
    - El access_token suele expirar ("expires" en segundos) salvo que incluyas el permiso 'offline_access'.
    - Deezer acepta el token típicamente como query param 'access_token'.

Ejemplo de uso rápido:

    from deezer_client import DeezerUserClient

    client = DeezerUserClient(
        client_id="TU_APP_ID",
        client_secret="TU_APP_SECRET",
        scope="basic_access,email,manage_library,listening_history",
    )

    client.authenticate()  # abrirá el navegador y capturará el code

    me = client.get_current_user()
    print(me)

    playlists = client.get_user_playlists(limit=20)
    print(playlists)

    history = client.get_listening_history(limit=20)  # requiere 'listening_history'
    print(history)

"""
from __future__ import annotations

import json
import os
import socket
import threading
import time
import urllib.parse
import webbrowser
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Dict, Optional

import requests

DEEER_AUTH_URL = "https://connect.deezer.com/oauth/auth.php"
DEEER_TOKEN_URL = "https://connect.deezer.com/oauth/access_token.php"
DEEER_API_BASE = "https://api.deezer.com"


@dataclass
class Token:
    access_token: str
    expires_at: Optional[float]  # epoch seconds o None si no expira

    @property
    def is_expired(self) -> bool:
        if self.expires_at is None:
            return False
        # margen de 60 s
        return time.time() >= (self.expires_at - 60)


class DeezerUserClient:
    """Cliente de alto nivel para OAuth 2.0 de Deezer y endpoints de usuario.

    Parámetros
    ----------
    client_id : str
        App ID de Deezer.
    client_secret : str
        App secret de Deezer (requerido; Deezer no soporta PKCE).
    redirect_uri : str, opcional
        Debe coincidir EXACTAMENTE con el de la app (por defecto 'http://127.0.0.1:8080/deezer_callback').
    scope : str, opcional
        Permisos separados por comas: 'basic_access,email,manage_library,listening_history,offline_access'.
    token_path : str, opcional
        Ruta del archivo JSON donde se persiste el token (por defecto '.deezer_token.json').
    """

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        redirect_uri: str = "http://127.0.0.1:8080/deezer_callback",
        scope: str = "basic_access,email",
        token_path: str = ".deezer_token.json",
    ) -> None:
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri
        self.scope = scope
        self.token_path = token_path

        self._token: Optional[Token] = None

    # ------------------------
    # Autenticación
    # ------------------------
    def authenticate(self) -> None:
        """Asegura que hay un access token válido; si no, lanza el flujo de autorización."""
        self._token = self._load_token()
        if self._token and not self._token.is_expired:
            return

        auth_url = self._build_auth_url()
        self._ensure_port_available(self._callback_port)
        server, code_holder = self._start_local_server_and_wait_for_code()
        webbrowser.open(auth_url)

        code = code_holder.wait_for_code(timeout=180)
        server.shutdown(); server.server_close()
        if not code:
            raise RuntimeError("No se recibió el 'code' de autorización a tiempo.")

        self._exchange_code_for_token(code)
        self._save_token()

    @property
    def _callback_port(self) -> int:
        parsed = urllib.parse.urlparse(self.redirect_uri)
        if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "[::1]"}:
            raise ValueError("El redirect_uri debe ser un loopback http://127.0.0.1:<puerto>/...")
        return parsed.port or 80

    def _build_auth_url(self) -> str:
        params = {
            "app_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "perms": self.scope,  # Deezer usa 'perms' separados por comas
            # opcional 'state'
        }
        return f"{DEEER_AUTH_URL}?{urllib.parse.urlencode(params)}"

    class _CodeHolder:
        def __init__(self):
            self._code: Optional[str] = None
            self._event = threading.Event()
        def set_code(self, code: str) -> None:
            self._code = code; self._event.set()
        def wait_for_code(self, timeout: int = 180) -> Optional[str]:
            ok = self._event.wait(timeout)
            return self._code if ok else None

    def _start_local_server_and_wait_for_code(self):
        code_holder = self._CodeHolder()
        redirect_path = urllib.parse.urlparse(self.redirect_uri).path or "/deezer_callback"

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
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
                        b"<html><body><h2>Autorizaci\xc3\xb3n completada en Deezer</h2>"
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
        # Deezer devuelve por defecto 'access_token=...&expires=...' en texto plano.
        # Añadimos output=json para facilitar el parseo.
        params = {
            "app_id": self.client_id,
            "secret": self.client_secret,
            "code": code,
            "output": "json",
        }
        resp = requests.get(DEEER_TOKEN_URL, params=params, timeout=20)
        if resp.status_code != 200:
            raise RuntimeError(f"Error al obtener token ({resp.status_code}): {resp.text}")
        payload = resp.json()
        if "access_token" not in payload:
            raise RuntimeError(f"Respuesta inesperada de token: {payload}")
        expires_in = payload.get("expires")
        expires_at = time.time() + int(expires_in) if expires_in else None
        self._token = Token(access_token=payload["access_token"], expires_at=expires_at)

    # ------------------------
    # Persistencia de token
    # ------------------------
    def _load_token(self) -> Optional[Token]:
        if not os.path.exists(self.token_path):
            return None
        try:
            with open(self.token_path, "r", encoding="utf-8") as f:
                payload = json.load(f)
            return Token(access_token=payload["access_token"], expires_at=payload.get("expires_at"))
        except Exception:
            return None

    def _save_token(self) -> None:
        if not self._token:
            return
        with open(self.token_path, "w", encoding="utf-8") as f:
            json.dump({"access_token": self._token.access_token, "expires_at": self._token.expires_at}, f, indent=2)

    # ------------------------
    # Llamadas a la API
    # ------------------------
    def _ensure_access_token(self) -> None:
        if not self._token:
            raise RuntimeError("No hay token. Llama primero a authenticate().")
        if self._token.is_expired:
            # Deezer no ofrece refresh token; hay que reautenticar
            self.authenticate()

    def api_request(self, endpoint: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Petición GET autenticada a la API de Deezer.
        endpoint: '/user/me', '/user/me/playlists', etc.
        """
        self._ensure_access_token()
        params = params.copy() if params else {}
        params["access_token"] = self._token.access_token
        url = endpoint
        if not endpoint.startswith("http"):
            endpoint = endpoint.lstrip("/")
            url = f"{DEEER_API_BASE}/{endpoint}"
        resp = requests.get(url, params=params, timeout=20)
        resp.raise_for_status()
        return resp.json()

    # ------------------------
    # Atajos de usuario
    # ------------------------
    def get_current_user(self) -> Dict[str, Any]:
        return self.api_request("/user/me")

    def get_user_playlists(self, limit: int = 20, index: int = 0) -> Dict[str, Any]:
        # Deezer pagina con 'index' y 'limit'
        return self.api_request("/user/me/playlists", params={"limit": limit, "index": index})

    def get_listening_history(self, limit: int = 20, index: int = 0) -> Dict[str, Any]:
        # Requiere permiso 'listening_history'
        return self.api_request("/user/me/history", params={"limit": limit, "index": index})


# Demo CLI
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Demo DeezerUserClient")
    parser.add_argument("--client-id", required=True)
    parser.add_argument("--client-secret", required=True)
    parser.add_argument("--redirect-uri", default="http://127.0.0.1:8080/deezer_callback")
    parser.add_argument("--scope", default="basic_access,email")
    args = parser.parse_args()

    client = DeezerUserClient(
        client_id=args.client_id,
        client_secret=args.client_secret,
        redirect_uri=args.redirect_uri,
        scope=args.scope,
    )
    client.authenticate()
    me = client.get_current_user()
    print(json.dumps(me, indent=2, ensure_ascii=False))
