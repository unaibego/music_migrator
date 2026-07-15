from src.services.spotify_getter import SpotifyUserClient
from src.services.spotify_library import SpotifyLibrary
from src.services.music_migrator_tidal import SpotifyToTidalMigrator



from src.core.settings.spotify_service import get_spotify_settings

spotify_settings = get_spotify_settings()





if __name__ == "__main__":
        
    client = SpotifyUserClient(
            client_id=spotify_settings.client_id,
            redirect_uri="http://127.0.0.1:8080/callback",  # o el que tengas en el Dashboard
            scope="user-read-email user-read-private  playlist-read-collaborative user-library-read playlist-read-private",
            user_name="Unai"
        )
    client.authenticate()

    spotify_lib = SpotifyLibrary(client)



    unai_playlist = spotify_lib.get_my_playlists()

    driving_with = spotify_lib.get_playlist_tracks("3LaB7AXjyGEKtIzQY3nQEE")
    print(unai_playlist)


