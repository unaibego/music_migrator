from src.services.tidal_client import TidalUserClient
from src.services.tidal_library import TidalLibrary
from src.services.tidal_playlist_sync import TidalPlaylistsSynchronizer
from src.db.dynamo_handler import DynamoHandler
from src.services.songs_cache import SongsCacheExporter


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

SongsCacheExporter().export_songs(dynamo.list_all_songs())
