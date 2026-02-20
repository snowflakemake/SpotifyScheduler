# Spotify Play Scheduler

This script schedules a Spotify track, album, playlist, or artist radio to start playing on one of your Spotify Connect devices at a specific time.

## Prerequisites

- Python 3.9 or newer.
- The [`spotipy`](https://spotipy.readthedocs.io) package (install with `pip install -r requirements.txt`).
- A Spotify Premium account and a registered application on the [Spotify Developer Dashboard](https://developer.spotify.com/dashboard).
- For `--system-schedule`: the platform's scheduler tools must be available (Task Scheduler / `schtasks.exe` on Windows, `at` on Linux).

Set the following environment variables before running the script:

```powershell
$env:SPOTIPY_CLIENT_ID = "your-client-id"
$env:SPOTIPY_CLIENT_SECRET = "your-client-secret"
$env:SPOTIPY_REDIRECT_URI = "http://127.0.0.1:8080/callback"
```

Any redirect URI you configure here must also be added to your Spotify app settings. The first run launches a browser window so you can grant playback permissions; tokens are cached locally in `.cache`.

If you are on a headless server, pass `--no-browser`. Spotipy will print an authorization URL that you can open on another device; after approving access, paste the redirect URL back into the terminal.

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

Create an OS-level job that will trigger playback later and let the script exit immediately:

```powershell
python schedule_spotify_play.py "spotify:album:2C6Z7gsiF3sPXso19p7MqU" --time 07:30 --system-schedule --device "Living Room"
```

- `media`: Accepts a Spotify URI (`spotify:track:...`, `spotify:album:...`, `spotify:playlist:...`, `spotify:artist:...`), share link, or raw 22-character ID (assumed to be a track when no type is given).
- `--now`: Skip scheduling and start playback immediately.
- `--system-schedule`: Use the OS scheduler (`schtasks` on Windows, `at` on Linux) to queue the playback and exit. Ensure the relevant tool is installed and accessible (e.g. Raspberry Pi OS users may need `sudo apt install at`).
- `--time HH:MM[:SS]`: Sets the clock time. Without `--date` it schedules for the next occurrence of that time.
- `--date YYYY-MM-DD`: Optional date to pair with `--time`. Must be today or in the future.
- `--at YYYY-MM-DDTHH:MM[:SS]`: Alternative to `--time/--date` for an absolute timestamp.
- `--device`: Optional Spotify Connect device name. Defaults to your active device, or the first available one.
- `--list-devices`: Authenticate, print the available Spotify Connect devices, and exit without scheduling playback.
- `--no-browser`: Prevent the script from trying to launch a web browser during Spotify authorization (helpful on servers).

The script confirms the scheduled playback time (unless `--now` is used), waits until the target moment, and then issues the playback command. Make sure the selected device is online shortly before the scheduled time; otherwise, playback will fail.

## Web Interface

Prefer a browser-based workflow? A minimal Flask app is included that wraps the same scheduling logic:

```bash
pip install -r requirements.txt
python web_app.py
```

Then visit [http://127.0.0.1:5000](http://127.0.0.1:5000) (or replace `127.0.0.1` with your host's LAN IP) to:

- See the Spotify Connect devices available to your account
- Pick a device, supply media (URI, share link, or ID), and choose either an ISO timestamp or a date/time pair
- Schedule the OS-level job (`--system-schedule` is used under the hood) from the browser
- Review existing scheduled jobs in the sidebar and cancel any you no longer need

Set `FLASK_SECRET_KEY` if you need to override the default development secret. The web app reuses your existing Spotipy credentials and token cache; make sure those environment variables remain set when you launch it. The server binds to `0.0.0.0` so other devices on the network can reach it; set `PORT` or `FLASK_DEBUG=1` via environment variables if you need a different port or debug mode. Only expose the app on trusted networks, as there is no authentication layer built in.

## Notes

- Spotify requires an active Premium subscription for programmatic playback.
- The machine running the script must stay awake until the scheduled time (unless `--now` or `--system-schedule` offloads to the OS scheduler).
- If you need to schedule multiple items, run the script once per item in separate terminals or background jobs, or create multiple system jobs.
- Ensure your environment variables or Spotipy cache are available to scheduled jobs (e.g., run the script from the same directory so the `.cache` file is reused).
- Linux systems may need to enable the `atd` service (`sudo systemctl enable --now atd`) before jobs will execute.
- When `--system-schedule` runs on Linux, the job sources `$VIRTUAL_ENV` or a local `venv`/`.venv` before invoking Python so dependencies are available.
- Windows scheduled tasks inherit the security context of the user who creates them; make sure that user has rights to run the Python script and access the cache.
