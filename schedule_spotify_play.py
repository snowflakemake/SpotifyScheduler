#!/usr/bin/env python3
"""Schedule Spotify playback of a specific track at a given time."""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timedelta, time as dt_time
from typing import Optional

try:
    import spotipy
    from spotipy.oauth2 import SpotifyOAuth
except ImportError as exc:  # pragma: no cover - dependency guidance
    print(
        "The spotipy package is required. Install it with `pip install spotipy`.",
        file=sys.stderr,
    )
    raise

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError as exc:  # pragma: no cover - dependency guidance
    print(
        "The python-dotenv package is recommended for loading environment variables from a .env file. "
        "Install it with `pip install python-dotenv`.",
        file=sys.stderr,
    )
    if(input("Continue without it? [y/N] ").lower() != 'y'):
        raise

def parse_track_uri(raw: str) -> str:
    """Normalise supported track identifiers to a Spotify track URI."""
    text = raw.strip()
    if text.startswith("spotify:track:"):
        return text
    if "open.spotify.com/track/" in text:
        track_id = text.split("track/")[1].split("?")[0].strip("/")
        if track_id:
            return f"spotify:track:{track_id}"
    if len(text) == 22 and text.isalnum():
        return f"spotify:track:{text}"
    raise ValueError(
        "Unsupported track reference. Provide a track URI, share link, or 22-character track ID."
    )


def parse_clock(time_str: str) -> dt_time:
    parts = time_str.split(":")
    if len(parts) not in (2, 3):
        raise ValueError("Use HH:MM or HH:MM:SS for --time.")
    try:
        hours = int(parts[0])
        minutes = int(parts[1])
        seconds = int(parts[2]) if len(parts) == 3 else 0
    except ValueError as exc:
        raise ValueError("Clock values must be integers.") from exc
    if not (0 <= hours < 24 and 0 <= minutes < 60 and 0 <= seconds < 60):
        raise ValueError("Clock values are out of range.")
    return dt_time(hour=hours, minute=minutes, second=seconds)


def determine_target_datetime(
    *,
    at: Optional[str],
    time_only: Optional[str],
    date_only: Optional[str],
) -> datetime:
    now = datetime.now()
    if at:
        try:
            target = datetime.fromisoformat(at)
        except ValueError as exc:
            raise ValueError(
                "Unable to parse --at. Use ISO format, e.g. 2025-10-03T08:30 or 2025-10-03 08:30."
            ) from exc
        if target <= now:
            raise ValueError("The --at datetime must be in the future.")
        return target

    if not time_only:
        raise ValueError("Provide either --at or --time.")

    target_time = parse_clock(time_only)
    if date_only:
        try:
            target_date = datetime.fromisoformat(date_only).date()
        except ValueError as exc:
            raise ValueError("Unable to parse --date. Use YYYY-MM-DD.") from exc
    else:
        target_date = now.date()
    candidate = datetime.combine(target_date, target_time)
    if candidate <= now:
        if date_only:
            raise ValueError("The chosen date/time is in the past.")
        candidate += timedelta(days=1)
    return candidate


def wait_until(target: datetime) -> None:
    while True:
        now = datetime.now()
        remaining = (target - now).total_seconds()
        if remaining <= 0:
            return
        sleep_for = min(remaining, 60)
        time.sleep(max(sleep_for, 0.5))


def select_device(sp: spotipy.Spotify, preferred_name: Optional[str]) -> str:
    devices = sp.devices().get("devices", [])
    if not devices:
        raise RuntimeError(
            "No available Spotify devices. Open Spotify on your target device and try again."
        )
    if preferred_name:
        lower = preferred_name.lower()
        for device in devices:
            if device.get("name", "").lower() == lower:
                return device["id"]
        device_names = ", ".join(device.get("name", "<unnamed>") for device in devices)
        raise RuntimeError(
            f"Device named '{preferred_name}' not found. Available devices: {device_names}."
        )
    for device in devices:
        if device.get("is_active"):
            return device["id"]
    return devices[0]["id"]


def start_playback(sp: spotipy.Spotify, device_id: str, track_uri: str) -> None:
    sp.transfer_playback(device_id=device_id, force_play=False)
    sp.start_playback(device_id=device_id, uris=[track_uri], position_ms=0)


def build_spotify_client() -> spotipy.Spotify:
    scope = "user-modify-playback-state user-read-playback-state"
    auth_manager = SpotifyOAuth(scope=scope, open_browser=True)
    return spotipy.Spotify(auth_manager=auth_manager)


def print_devices(sp: spotipy.Spotify) -> None:
    devices = sp.devices().get("devices", [])
    if not devices:
        print("No available Spotify devices. Launch Spotify somewhere and try again.")
        return
    print("Available Spotify devices:")
    for device in devices:
        status_bits = []
        if device.get("is_active"):
            status_bits.append("active")
        if device.get("is_private_session"):
            status_bits.append("private")
        status = f" ({', '.join(status_bits)})" if status_bits else ""
        name = device.get("name", "<unnamed>")
        device_type = device.get("type", "unknown")
        device_id = device.get("id", "<no-id>")
        print(f"- {name:<20} [{device_type + "]":<12} id={device_id}{status}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Schedule a Spotify track to start playing at a future time.",
    )
    parser.add_argument(
        "track",
        nargs="?",
        help="Track URI, share link, or 22-character track ID",
    )
    parser.add_argument(
        "--at",
        help="Absolute timestamp (ISO 8601) for playback, e.g. 2025-10-03T08:30",
    )
    parser.add_argument(
        "--time",
        help="Clock time (HH:MM or HH:MM:SS). Without --date it schedules for the next occurrence.",
    )
    parser.add_argument(
        "--date",
        help="Date (YYYY-MM-DD) to combine with --time. Must be today or in the future.",
    )
    parser.add_argument(
        "--device",
        help="Name of the Spotify Connect device to target. Defaults to active device.",
    )
    parser.add_argument(
        "--list-devices",
        action="store_true",
        help="List available Spotify Connect devices and exit.",
    )
    args = parser.parse_args()

    if args.list_devices:
        try:
            spotify_client = build_spotify_client()
        except Exception as exc:  # pragma: no cover - auth issues passed to user
            parser.error(f"Unable to authenticate with Spotify: {exc}")
        print_devices(spotify_client)
        return

    if not args.track:
        parser.error("Track argument is required unless --list-devices is used.")

    try:
        track_uri = parse_track_uri(args.track)
    except ValueError as exc:
        parser.error(str(exc))

    try:
        target = determine_target_datetime(at=args.at, time_only=args.time, date_only=args.date)
    except ValueError as exc:
        parser.error(str(exc))

    print(f"Scheduling playback for {target.isoformat(sep=' ', timespec='seconds')}.")

    try:
        spotify_client = build_spotify_client()
    except Exception as exc:  # pragma: no cover - auth issues passed to user
        parser.error(f"Unable to authenticate with Spotify: {exc}")

    try:
        device_id = select_device(spotify_client, args.device)
    except RuntimeError as exc:
        parser.error(str(exc))

    now = datetime.now()
    if target - now > timedelta(seconds=1):
        remaining = target - now
        minutes, seconds = divmod(int(remaining.total_seconds()), 60)
        hours, minutes = divmod(minutes, 60)
        print(
            "Waiting for ~{:02d}:{:02d}:{:02d} (hh:mm:ss) before starting playback...".format(
                hours, minutes, seconds
            )
        )
        wait_until(target)

    try:
        start_playback(spotify_client, device_id, track_uri)
    except spotipy.SpotifyException as exc:
        parser.error(f"Spotify refused to start playback: {exc}")

    print("Playback started. Enjoy!")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("Aborted by user.")
        sys.exit(130)