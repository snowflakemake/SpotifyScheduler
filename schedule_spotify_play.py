#!/usr/bin/env python3
"""Schedule Spotify playback of a track, album, playlist, or artist at a given time."""

from __future__ import annotations

import argparse
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
import uuid
import tempfile
from datetime import datetime, timedelta, time as dt_time
from pathlib import Path
from typing import Optional, Tuple

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
except ImportError:
    load_dotenv = None

if load_dotenv:
    load_dotenv()

SCRIPT_DIR = Path(__file__).resolve().parent
os.chdir(SCRIPT_DIR)

_SPOTIFY_URI_PATTERN = re.compile(
    r"spotify:(?P<type>track|album|playlist|artist):(?P<id>[A-Za-z0-9]{22})"
)
_SPOTIFY_URL_PATTERN = re.compile(
    r"open\.spotify\.com/(?P<type>track|album|playlist|artist)/(?P<id>[A-Za-z0-9]{22})"
)


def find_venv_activation_script() -> Optional[Path]:
    """Locate a virtualenv activation script to source before scheduled runs."""
    candidates = []
    env_venv = os.environ.get("VIRTUAL_ENV")
    if env_venv:
        candidates.append(Path(env_venv) / "bin" / "activate")
    candidates.append(SCRIPT_DIR / "venv" / "bin" / "activate")
    candidates.append(SCRIPT_DIR / ".venv" / "bin" / "activate")
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None

def parse_media_reference(raw: str) -> Tuple[str, str]:
    """Normalise track/album/playlist/artist identifiers to a Spotify URI."""
    text = raw.strip()
    if not text:
        raise ValueError("Media reference must not be empty.")

    match = _SPOTIFY_URI_PATTERN.fullmatch(text)
    if match:
        media_type = match.group("type")
        media_id = match.group("id")
        return media_type, f"spotify:{media_type}:{media_id}"

    if text.startswith("http://") or text.startswith("https://"):
        url_match = _SPOTIFY_URL_PATTERN.search(text)
        if url_match:
            media_type = url_match.group("type")
            media_id = url_match.group("id")
            return media_type, f"spotify:{media_type}:{media_id}"

    if len(text) == 22 and text.isalnum():
        # Treat bare IDs as tracks by default.
        return "track", f"spotify:track:{text}"

    raise ValueError(
        "Unsupported media reference. Provide a track/album/playlist/artist URI, share link, or 22-character ID."
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


def start_playback(
    sp: spotipy.Spotify,
    device_id: str,
    media_type: str,
    media_uri: str,
) -> None:
    sp.transfer_playback(device_id=device_id, force_play=False)
    if media_type == "track":
        sp.start_playback(device_id=device_id, uris=[media_uri], position_ms=0)
    else:
        sp.start_playback(device_id=device_id, context_uri=media_uri)


def build_spotify_client(*, open_browser: bool) -> spotipy.Spotify:
    scope = "user-modify-playback-state user-read-playback-state"
    auth_manager = SpotifyOAuth(scope=scope, open_browser=open_browser)
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
        print(f"- {name:<20} [{device_type}] id={device_id}{status}")


def build_system_command(args: argparse.Namespace, target: datetime) -> list[str]:
    script_path = Path(__file__).resolve()
    python_executable = Path(sys.executable).resolve()
    command = [
        str(python_executable),
        str(script_path),
        args.media,
        f"--now",
        ">> ~/schedule_spotify_play.log 2>&1",
    ]
    if args.device:
        command.extend(["--device", args.device])
    command.append("--no-browser")
    return command


def schedule_system_job(target: datetime, args: argparse.Namespace) -> str:
    command = build_system_command(args, target)
    if os.name == "nt":
        if shutil.which("schtasks") is None:
            raise RuntimeError("'schtasks' command not found. Cannot create Windows scheduled task.")
        task_command = subprocess.list2cmdline(command)
        task_name = f"SpotifyPlay_{target.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
        create_cmd = [
            "schtasks",
            "/Create",
            "/SC",
            "ONCE",
            "/TN",
            task_name,
            "/TR",
            task_command,
            "/ST",
            target.strftime("%H:%M"),
            "/SD",
            target.strftime("%Y/%m/%d"),
            "/F",
        ]
        result = subprocess.run(create_cmd, capture_output=True, text=True)
        if result.returncode != 0:
            message = result.stderr.strip() or result.stdout.strip() or "Unable to create scheduled task."
            raise RuntimeError(message)
        return f"Windows Scheduled Task '{task_name}'"

    if shutil.which("at") is None:
        raise RuntimeError("'at' command not found. Install it or use another scheduling method.")

    command_line = " ".join(shlex.quote(part) for part in command)
    at_time = target.strftime("%Y%m%d%H%M")
    activate_script = find_venv_activation_script()

    script_lines = [
        "#!/bin/sh",
        "set -e",
        f"cd {shlex.quote(str(SCRIPT_DIR))}",
        f"sleep {target.second}"
    ]
    if activate_script:
        script_lines.append(f". {shlex.quote(str(activate_script))}")
    script_lines.append(command_line)
    script_content = "\n".join(script_lines) + "\n"

    tmp_script: Optional[Path] = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", delete=False, encoding="utf-8", dir=str(SCRIPT_DIR), suffix="_at_job.sh"
        ) as handle:
            handle.write(script_content)
            tmp_script = Path(handle.name)

        result = subprocess.run(
            ["at", "-t", at_time, "-f", str(tmp_script)],
            capture_output=True,
            text=True,
        )
    finally:
        if tmp_script is not None:
            try:
                tmp_script.unlink()
            except OSError:
                pass

    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip() or "Unable to schedule job with 'at'."
        raise RuntimeError(message)
    print(f"Scheduled job script: {script_content}\n at path: {tmp_script}")
    return result.stdout.strip() or result.stderr.strip() or "at job scheduled"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Schedule a Spotify track, album, playlist, or artist to start playing at a future time.",
    )
    parser.add_argument(
        "media",
        nargs="?",
        help="Track, album, playlist, or artist URI/share link/ID",
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
        "--now",
        action="store_true",
        help="Start playback immediately instead of scheduling it.",
    )
    parser.add_argument(
        "--system-schedule",
        action="store_true",
        help="Create an OS-level scheduled job and exit.",
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
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Do not attempt to launch a browser for Spotify authorization.",
    )
    args = parser.parse_args()

    if args.list_devices and args.system_schedule:
        parser.error("--system-schedule cannot be combined with --list-devices.")

    if args.list_devices:
        try:
            spotify_client = build_spotify_client(open_browser=not args.no_browser)
        except Exception as exc:  # pragma: no cover - auth issues passed to user
            parser.error(f"Unable to authenticate with Spotify: {exc}")
        print_devices(spotify_client)
        return

    if not args.media:
        parser.error("Media argument is required unless --list-devices is used.")

    if args.now and any([args.at, args.time, args.date]):
        parser.error("--now cannot be combined with --at, --time, or --date.")

    if args.system_schedule and args.now:
        parser.error("--system-schedule cannot be combined with --now.")

    try:
        media_type, media_uri = parse_media_reference(args.media)
    except ValueError as exc:
        parser.error(str(exc))

    target: Optional[datetime] = None
    if not args.now:
        try:
            target = determine_target_datetime(at=args.at, time_only=args.time, date_only=args.date)
        except ValueError as exc:
            parser.error(str(exc))

    if args.system_schedule:
        if target is None:
            parser.error("--system-schedule requires a future time via --at or --time/--date.")
        try:
            job_label = schedule_system_job(target, args)
        except RuntimeError as exc:
            parser.error(str(exc))
        print(
            f"Created {job_label} for {target.isoformat(sep=' ', timespec='seconds')}.",
        )
        print("The scheduled job will run this script with --now at the specified time.")
        return

    try:
        spotify_client = build_spotify_client(open_browser=not args.no_browser)
    except Exception as exc:  # pragma: no cover - auth issues passed to user
        parser.error(f"Unable to authenticate with Spotify: {exc}")

    try:
        device_id = select_device(spotify_client, args.device)
    except RuntimeError as exc:
        parser.error(str(exc))

    if target is not None:
        print(
            f"Scheduling {media_type} playback for {target.isoformat(sep=' ', timespec='seconds')}.",
        )
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
    else:
        print(f"Starting {media_type} playback now.")

    try:
        start_playback(spotify_client, device_id, media_type, media_uri)
    except spotipy.SpotifyException as exc:
        parser.error(f"Spotify refused to start playback: {exc}")

    print("Playback started. Enjoy!")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("Aborted by user.")
        sys.exit(130)





