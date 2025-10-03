from __future__ import annotations

import os
import shlex
from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import Any, Optional

import shutil
import subprocess
from flask import Flask, flash, redirect, render_template, request, url_for

from schedule_spotify_play import (
    build_spotify_client,
    determine_target_datetime,
    parse_media_reference,
    schedule_system_job,
)

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev-secret-key")


def fetch_devices(client: Optional[Any] = None) -> list[dict]:
    spotify_client = client or build_spotify_client(open_browser=False)
    payload = spotify_client.devices()
    devices = payload.get("devices", []) if isinstance(payload, dict) else []
    devices.sort(key=lambda item: (item.get("name") or "").lower())
    return devices


def _inspect_at_job_details(job_id: str) -> Optional[dict[str, Any]]:
    try:
        result = subprocess.run(
            ["at", "-c", job_id], capture_output=True, text=True, check=False
        )
    except FileNotFoundError:
        return None
    if result.returncode != 0:
        return None
    lines = result.stdout.splitlines()
    sleep_seconds: Optional[int] = None
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("sleep "):
            parts = stripped.split()
            if len(parts) >= 2:
                try:
                    sleep_seconds = int(parts[1])
                except ValueError:
                    sleep_seconds = None
            continue
    command: Optional[str] = None
    for line in reversed(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("export "):
            continue
        if stripped.startswith("cd ") or stripped.startswith("sleep "):
            continue
        if stripped.startswith(". "):
            continue
        if stripped.startswith("umask") or stripped.startswith("trap "):
            continue
        if "=" in stripped:
            key = stripped.split("=", 1)[0]
            if key.isidentifier():
                continue
        command = stripped
        break
    if command is None and sleep_seconds is None:
        return None
    return {"command": command, "sleep_seconds": sleep_seconds}


def inspect_at_job_command(job_id: str) -> Optional[str]:
    details = _inspect_at_job_details(job_id)
    if not details:
        return None
    return details.get("command")


def _extract_media_from_command(command: Optional[str]) -> Optional[tuple[str, str]]:
    if not command:
        return None
    try:
        tokens = shlex.split(command)
    except ValueError:
        return None

    script_index = None
    for idx, part in enumerate(tokens):
        if part.endswith("schedule_spotify_play.py"):
            script_index = idx
            break
        if os.path.basename(part) == "schedule_spotify_play.py":
            script_index = idx
            break
    if script_index is None:
        return None
    media_index = script_index + 1
    if media_index >= len(tokens):
        return None
    candidate = tokens[media_index]
    try:
        media_type, media_uri = parse_media_reference(candidate)
    except ValueError:
        return None
    return media_type, media_uri


def _extract_volume_from_command(command: Optional[str]) -> Optional[int]:
    if not command:
        return None
    try:
        tokens = shlex.split(command)
    except ValueError:
        return None
    for idx, token in enumerate(tokens):
        if token == "--volume" and idx + 1 < len(tokens):
            candidate = tokens[idx + 1]
            try:
                value = int(candidate)
            except ValueError:
                return None
            if 0 <= value <= 100:
                return value
            return None
    return None


def _format_duration_ms(value: Any) -> Optional[str]:
    try:
        duration_ms = int(value)
    except (TypeError, ValueError):
        return None
    if duration_ms < 0:
        return None
    total_seconds = duration_ms // 1000
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes}:{seconds:02d}"


def _describe_spotify_media(
    spotify_client: Optional[Any], media_type: str, media_uri: str
) -> Optional[dict[str, Any]]:
    if spotify_client is None:
        return None
    try:
        if media_type == "track":
            data = spotify_client.track(media_uri)
            if not isinstance(data, dict):
                return None
            name = data.get("name")
            artists = ", ".join(
                artist.get("name")
                for artist in data.get("artists", [])
                if isinstance(artist, dict) and artist.get("name")
            )
            album = None
            album_payload = data.get("album")
            if isinstance(album_payload, dict):
                album = album_payload.get("name")
            parts = []
            if name:
                parts.append({"identifier": "track", "text": name})
            if artists:
                parts.append({"identifier": "artist", "text": artists})
            if album:
                parts.append({"identifier": "album", "text": album})
            pieces = [item["text"] for item in parts]
            summary = "Track: " + " — ".join(pieces) if pieces else None
            duration_label = _format_duration_ms(data.get("duration_ms"))
            return {
                "summary": summary,
                "type_label": "Track",
                "parts": parts,
                "duration_ms": data.get("duration_ms"),
                "duration_label": duration_label,
            }

        if media_type == "playlist":
            data = spotify_client.playlist(media_uri)
            if not isinstance(data, dict):
                return None
            name = data.get("name")
            owner_payload = data.get("owner")
            owner = None
            if isinstance(owner_payload, dict):
                owner = owner_payload.get("display_name") or owner_payload.get("id")
            parts = []
            if name:
                parts.append({"identifier": "playlist", "text": name})
            if owner:
                parts.append({"identifier": "owner", "text": owner})
            pieces = [item["text"] for item in parts]
            summary = "Playlist: " + " — ".join(pieces) if pieces else None
            return {
                "summary": summary,
                "type_label": "Playlist",
                "parts": parts,
            }

        if media_type == "album":
            data = spotify_client.album(media_uri)
            if not isinstance(data, dict):
                return None
            name = data.get("name")
            artists = ", ".join(
                artist.get("name")
                for artist in data.get("artists", [])
                if isinstance(artist, dict) and artist.get("name")
            )
            parts = []
            if name:
                parts.append({"identifier": "album", "text": name})
            if artists:
                parts.append({"identifier": "artist", "text": artists})
            pieces = [item["text"] for item in parts]
            summary = "Album: " + " — ".join(pieces) if pieces else None
            return {
                "summary": summary,
                "type_label": "Album",
                "parts": parts,
            }

        if media_type == "artist":
            data = spotify_client.artist(media_uri)
            if not isinstance(data, dict):
                return None
            name = data.get("name")
            if not name:
                return None
            return {
                "summary": f"Artist: {name}",
                "type_label": "Artist",
                "parts": [{"identifier": "artist", "text": name}],
            }
    except Exception:
        return None
    return None


def _build_job_media_details(
    command: Optional[str], spotify_client: Optional[Any]
) -> Optional[dict]:
    media = _extract_media_from_command(command)
    if not media:
        return None
    media_type, media_uri = media
    description = _describe_spotify_media(spotify_client, media_type, media_uri)
    media_type_label = media_type.title()
    media_parts: list[dict[str, str]] = []
    summary: Optional[str]
    duration_ms: Optional[int] = None
    duration_label: Optional[str] = None
    if description is None:
        summary = f"{media_type_label}: {media_uri}"
        media_parts.append({"identifier": "uri", "text": media_uri})
    else:
        summary = description.get("summary") or f"{media_type_label}: {media_uri}"
        media_type_label = description.get("type_label") or media_type_label
        duration_value = description.get("duration_ms")
        if duration_value is not None:
            try:
                duration_ms = int(duration_value)
            except (TypeError, ValueError):
                duration_ms = None
        duration_label = description.get("duration_label")
        for part in description.get("parts", []):
            text = part.get("text")
            identifier = part.get("identifier")
            if not text or not identifier:
                continue
            media_parts.append(
                {
                    "identifier": str(identifier),
                    "text": str(text),
                }
            )
        if not media_parts and summary:
            media_parts.append({"identifier": "value", "text": summary})
    return {
        "media_type": media_type,
        "media_uri": media_uri,
        "media_summary": summary,
        "media_type_label": media_type_label,
        "media_parts": media_parts,
        "media_duration_ms": duration_ms,
        "media_duration_label": duration_label,
    }


def list_system_jobs(*, spotify_client: Optional[Any] = None) -> tuple[list[dict], Optional[str]]:
    if os.name == "nt":
        return [], "Job listing via the web UI is not yet supported on Windows."
    if shutil.which("atq") is None:
        return [], "'atq' command not found. Install the 'at' package to manage jobs."

    result = subprocess.run(["atq"], capture_output=True, text=True, check=False)
    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip() or "Unable to list jobs."
        return [], message

    jobs: list[dict] = []
    for raw_line in result.stdout.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if "\t" in line:
            job_id, rest = line.split("\t", 1)
        else:
            parts = line.split(None, 1)
            job_id = parts[0]
            rest = parts[1] if len(parts) > 1 else ""
        details = rest.strip()
        tokens = details.split()
        scheduled_for = None
        scheduled_dt: Optional[datetime] = None
        queue = None
        user = None
        if len(tokens) >= 5:
            scheduled_for = " ".join(tokens[:5])
            try:
                scheduled_dt = datetime.strptime(scheduled_for, "%a %b %d %H:%M:%S %Y")
            except ValueError:
                scheduled_dt = None
        if len(tokens) >= 6:
            queue = tokens[5]
        if len(tokens) >= 7:
            user = tokens[6]
        job_details = _inspect_at_job_details(job_id)
        command = job_details.get("command") if job_details else None
        sleep_seconds = job_details.get("sleep_seconds") if job_details else None
        playback_dt: Optional[datetime] = None
        playback_label: Optional[str] = None
        if scheduled_dt is not None:
            offset_seconds = 0
            if sleep_seconds is not None:
                try:
                    offset_seconds = int(sleep_seconds)
                except (TypeError, ValueError):
                    offset_seconds = 0
            playback_dt = scheduled_dt + timedelta(seconds=offset_seconds)
            playback_label = playback_dt.strftime("%a %b %d %H:%M:%S %Y")
        volume = _extract_volume_from_command(command)
        volume_label = f"{volume}%" if volume is not None else None
        job_payload = {
            "id": job_id,
            "scheduled_for": scheduled_for,
            "scheduled_datetime": scheduled_dt,
            "playback_datetime": playback_dt,
            "playback_label": playback_label,
            "sleep_seconds": sleep_seconds,
            "queue": queue,
            "user": user,
            "details": details,
            "command": command,
            "volume": volume,
            "volume_label": volume_label,
        }
        media_details = _build_job_media_details(command, spotify_client)
        if media_details:
            job_payload.update(media_details)
        jobs.append(job_payload)
    return jobs, None


def remove_system_job(job_id: str) -> tuple[bool, str]:
    job_id = (job_id or "").strip()
    if not job_id:
        return False, "Missing job id."
    if not job_id.isdigit():
        return False, "Job id must be numeric."
    if os.name == "nt":
        return False, "Removing jobs from the web UI is not yet supported on Windows."
    if shutil.which("atrm") is None:
        return False, "'atrm' command not found. Install the 'at' package to manage jobs."

    result = subprocess.run(["atrm", job_id], capture_output=True, text=True, check=False)
    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip() or "Unable to remove job."
        return False, message
    return True, f"Removed scheduled job {job_id}."


@app.route("/", methods=["GET", "POST"])
def index():
    spotify_client: Optional[Any] = None
    devices: list[dict] = []
    device_error: Optional[str] = None
    try:
        spotify_client = build_spotify_client(open_browser=False)
        devices = fetch_devices(spotify_client)
    except Exception as exc:  # pragma: no cover - surface auth/network issues
        device_error = f"Unable to load Spotify devices: {exc}"

    jobs, jobs_error = list_system_jobs(spotify_client=spotify_client)

    if request.method == "POST":
        media_input = (request.form.get("media") or "").strip()
        device_name = (request.form.get("device") or "").strip() or None
        volume_raw = (request.form.get("volume") or "").strip()
        iso_at = (request.form.get("iso_at") or "").strip() or None
        date_input = (request.form.get("date") or "").strip() or None
        time_input = (request.form.get("time") or "").strip() or None

        errors = []
        volume_value: Optional[int] = None
        if not media_input:
            errors.append("Media is required.")
        else:
            try:
                parse_media_reference(media_input)
            except ValueError as exc:
                errors.append(str(exc))

        if volume_raw:
            try:
                candidate = int(volume_raw)
            except ValueError:
                errors.append("Volume must be an integer between 0 and 100.")
            else:
                if 0 <= candidate <= 100:
                    volume_value = candidate
                else:
                    errors.append("Volume must be between 0 and 100.")

        target: Optional[datetime] = None
        if not errors:
            try:
                target = determine_target_datetime(
                    at=iso_at,
                    time_only=time_input,
                    date_only=date_input,
                )
            except ValueError as exc:
                errors.append(str(exc))

        if not iso_at and not time_input:
            errors.append("Provide either an ISO timestamp or both date and time.")

        if errors:
            for item in errors:
                flash(item, "error")
        elif target is not None:
            args = SimpleNamespace(media=media_input, device=device_name, volume=volume_value)
            try:
                job_label = schedule_system_job(target, args)
            except Exception as exc:
                flash(str(exc), "error")
            else:
                flash(
                    f"Created {job_label} for {target.isoformat(sep=' ', timespec='seconds')}.",
                    "success",
                )
                return redirect(url_for("index"))

    devices_ns = [SimpleNamespace(**item) for item in devices]
    jobs_ns = [SimpleNamespace(**item) for item in jobs]

    # Get next minute rounded time for default
    next_minute = (datetime.now() + timedelta(minutes=1)).replace(second=0, microsecond=0)

    return render_template(
        "index.html",
        devices=devices_ns,
        device_error=device_error,
        jobs=jobs_ns,
        jobs_error=jobs_error,
        date=datetime.now().date().isoformat(),
        time=next_minute.time().isoformat(),
        volume=spotify_client.current_playback().get("device", {}).get("volume_percent") if spotify_client else None,
    )


@app.post("/jobs/remove")
def remove_job() -> Any:
    success, message = remove_system_job(request.form.get("job_id"))
    flash(message, "success" if success else "error")
    return redirect(url_for("index"))


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=os.environ.get("FLASK_DEBUG") == "1")
