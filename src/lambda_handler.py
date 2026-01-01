from src.services.tidal_client import TidalUserClient
from src.services.tidal_library import TidalLibrary
from src.services.tidal_playlist_sync import TidalPlaylistsSynchronizer



from src.core.settings.youtube_service import get_youtube_settings
from src.core.settings.spotify_service import get_spotify_settings

youtube_settings = get_youtube_settings()
spotify_settings = get_spotify_settings()


def lambda_handler(event, context):
        
    tidal_unai = TidalUserClient(user_name="Unai")
    tidal_unai.authenticate()
    tidal_june = TidalUserClient(user_name="June")
    tidal_june.authenticate()



    tidal_lib_unai = TidalLibrary(tidal_unai)
    tidal_lib_june = TidalLibrary(tidal_june)


    sync = TidalPlaylistsSynchronizer(
            tidal_a=tidal_lib_unai,
            tidal_b=tidal_lib_june,
            avoid_duplicates=True,
            ask_per_playlist=False,
        )

    sync.run()


    return {"ok": True}