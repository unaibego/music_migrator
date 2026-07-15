import base64
import json
from decimal import Decimal
from typing import Any, Dict

from src.db.dynamo_handler import DynamoHandler
from src.services.songs_cache import SongsCacheExporter


def _json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        if value % 1 == 0:
            return int(value)
        return float(value)
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


JSON_HEADERS = {"Content-Type": "application/json"}


def _http_response(status_code: int, body: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "statusCode": status_code,
        "headers": JSON_HEADERS,
        "body": json.dumps(body, default=_json_default),
    }


def _get_http_method(event: Dict[str, Any]) -> str:
    if not event:
        return "POST"
    request_context = event.get("requestContext") or {}
    http = request_context.get("http") or {}
    return (http.get("method") or event.get("httpMethod") or "POST").upper()


def _parse_event_body(event: Dict[str, Any]) -> Dict[str, Any]:
    raw = event.get("body") or "{}"
    if event.get("isBase64Encoded"):
        raw = base64.b64decode(raw).decode("utf-8")
    if not raw:
        return {}
    return json.loads(raw)


def _export_songs() -> int:
    songs = DynamoHandler().list_all_songs()
    SongsCacheExporter().export_songs(songs)
    return len(songs)


def _list_songs() -> Dict[str, Any]:
    songs = DynamoHandler().list_all_songs()
    SongsCacheExporter().export_songs(songs)
    return {
        "ok": True,
        "action": "list",
        "count": len(songs),
        "songs": songs,
    }


def _update_inserted_by(body: Dict[str, Any]) -> Dict[str, Any]:
    song_id = body.get("songId")
    if not song_id:
        raise ValueError("Falta songId")

    inserted_by = body.get("insertedBy")
    if inserted_by == "" or inserted_by is None:
        inserted_by = None

    DynamoHandler().update_inserted_by(str(song_id), inserted_by)
    count = _export_songs()
    return {
        "ok": True,
        "action": "update_inserted_by",
        "songId": str(song_id),
        "insertedBy": inserted_by,
        "count": count,
    }


def _run_sync() -> Dict[str, Any]:
    from src.services.tidal_client import TidalUserClient
    from src.services.tidal_library import TidalLibrary
    from src.services.tidal_playlist_sync import TidalPlaylistsSynchronizer

    tidal_unai = TidalUserClient(user_name="Unai")
    tidal_unai.authenticate()
    tidal_june = TidalUserClient(user_name="June")
    tidal_june.authenticate()

    tidal_lib_unai = TidalLibrary(tidal_unai)
    tidal_lib_june = TidalLibrary(tidal_june)

    dynamo = DynamoHandler()
    sync = TidalPlaylistsSynchronizer(
        tidal_a=tidal_lib_unai,
        tidal_b=tidal_lib_june,
        avoid_duplicates=True,
        ask_per_playlist=False,
        dynamo_handler=dynamo,
    )
    sync.run()

    count = _export_songs()
    return {"ok": True, "action": "sync", "count": count}


def _handle_post(event: Dict[str, Any]) -> Dict[str, Any]:
    body = _parse_event_body(event)
    action = body.get("action", "sync")
    if action == "update_inserted_by":
        return _update_inserted_by(body)
    return _run_sync()


def lambda_handler(event, context):
    method = _get_http_method(event or {})

    if method == "GET":
        try:
            return _http_response(200, _list_songs())
        except Exception as e:
            return _http_response(500, {"ok": False, "error": str(e)})

    if method == "POST":
        try:
            return _http_response(200, _handle_post(event or {}))
        except Exception as e:
            return _http_response(500, {"ok": False, "error": str(e)})

    return _http_response(405, {"ok": False, "error": f"Method {method} not allowed"})
