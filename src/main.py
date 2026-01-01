from src.services.spotify_getter import SpotifyUserClient
from src.services.spotify_library import SpotifyLibrary
from src.services.tidal_client import TidalUserClient
from src.services.tidal_library import TidalLibrary
from src.services.music_migrator_tidal import SpotifyToTidalMigrator



from src.core.settings.youtube_service import get_youtube_settings
from src.core.settings.spotify_service import get_spotify_settings

youtube_settings = get_youtube_settings()
spotify_settings = get_spotify_settings()

# 1) Buscar mejor coincidencia para un tema

tidal = TidalUserClient("Maialen")
tidal.authenticate()

    # 2) Capa de biblioteca
tidal_lib = TidalLibrary(tidal)

client = SpotifyUserClient(
        client_id=spotify_settings.client_id,
        redirect_uri="http://127.0.0.1:8080/callback",  # o el que tengas en el Dashboard
        scope="user-read-email user-read-private  playlist-read-collaborative user-library-read playlist-read-private",
        user_name="Maialen"
    )
client.authenticate()

spotify_lib = SpotifyLibrary(client)


# migrator = SpotifyToYouTubeMigrator(
#     spotify_lib=spotify_lib,
#     youtube_lib=youtube_lib,
#     score_threshold=70,     # ajusta a tu gusto
#     per_query_limit=5,      # candidatos a considerar
#     privacy_status="private"  # "public" | "unlisted"
# )
# migrator.run()


migrator = SpotifyToTidalMigrator(
    spotify_lib,
    tidal_lib,
    score_threshold=30,
    per_query_limit=5,
    ask_per_playlist=True,
    avoid_duplicates=True,
)
migrator.run()
migrator.migrate_liked_songs()


