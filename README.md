# Spotify Play Scheduler

This script schedules a Spotify track, album, playlist, or artist radio to start playing on one of your Spotify Connect devices at a specific time.

## Prerequisites

- Python 3.9 or newer.
- The [`spotipy`](https://spotipy.readthedocs.io) package (install with `pip install -r requirements.txt`).
- A Spotify Premium account and a registered application on the [Spotify Developer Dashboard](https://developer.spotify.com/dashboard).

Set the following environment variables before running the script in your terminal or in a `.env` file:

```bash
# In .env-file
SPOTIPY_CLIENT_ID = "your-client-id"
SPOTIPY_CLIENT_SECRET = "your-client-secret"
SPOTIPY_REDIRECT_URI = "http://localhost:8080/callback"
```

```powershell
# In PowerShell
$env:SPOTIPY_CLIENT_ID = "your-client-id"
$env:SPOTIPY_CLIENT_SECRET = "your-client-secret"
$env:SPOTIPY_REDIRECT_URI = "http://localhost:8080/callback"
```

```bash
# In bash
export SPOTIPY_CLIENT_ID="your-client-id"
export SPOTIPY_CLIENT_SECRET="your-client-secret"
export SPOTIPY_REDIRECT_URI="http://localhost:8080/callback"
```

Any redirect URI you configure here must also be added to your Spotify app settings. The first run launches a browser window so you can grant playback permissions; tokens are cached locally in `.cache`.

## Usage

List available devices:

```powershell
python schedule_spotify_play.py --list-devices
```

Start immediate playback:

```powershell
python schedule_spotify_play.py "spotify:playlist:37i9dQZF1DXcBWIGoYBM5M" --now --device "Office"
```

Schedule playback:

```powershell
python schedule_spotify_play.py "https://open.spotify.com/album/2C6Z7gsiF3sPXso19p7MqU" --time 07:30 --device "Living Room"
```

- `media`: Accepts a Spotify URI (`spotify:track:...`, `spotify:album:...`, `spotify:playlist:...`, `spotify:artist:...`), share link, or raw 22-character ID (assumed to be a track when no type is given).
- `--now`: Skip scheduling and start playback immediately.
- `--time HH:MM[:SS]`: Sets the clock time. Without `--date` it schedules for the next occurrence of that time.
- `--date YYYY-MM-DD`: Optional date to pair with `--time`. Must be today or in the future.
- `--at YYYY-MM-DDTHH:MM[:SS]`: Alternative to `--time/--date` for an absolute timestamp.
- `--device`: Optional Spotify Connect device name. Defaults to your active device, or the first available one.
- `--list-devices`: Authenticate, print the available Spotify Connect devices, and exit without scheduling playback.

The script confirms the scheduled playback time (unless `--now` is used), waits until the target moment, and then issues the playback command. Make sure the selected device is online shortly before the scheduled time; otherwise, playback will fail.

## Notes

- Spotify requires an active Premium subscription for programmatic playback.
- The machine running the script must stay awake until the scheduled time (unless `--now` is used).
- If you need to schedule multiple items, run the script once per item in separate terminals or background jobs.