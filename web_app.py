from __future__ import annotations

import os
from datetime import datetime
from types import SimpleNamespace
from typing import Optional

from flask import Flask, flash, redirect, render_template_string, request, url_for

from schedule_spotify_play import (
    build_spotify_client,
    determine_target_datetime,
    parse_media_reference,
    schedule_system_job,
)

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev-secret-key")


def fetch_devices() -> list[dict]:
    client = build_spotify_client(open_browser=False)
    payload = client.devices()
    devices = payload.get("devices", []) if isinstance(payload, dict) else []
    devices.sort(key=lambda item: (item.get("name") or "").lower())
    return devices


@app.route("/", methods=["GET", "POST"])
def index():
    devices: list[dict] = []
    device_error: Optional[str] = None
    try:
        devices = fetch_devices()
    except Exception as exc:  # pragma: no cover - surface auth/network issues
        device_error = f"Unable to load Spotify devices: {exc}"

    if request.method == "POST":
        media_input = (request.form.get("media") or "").strip()
        device_name = (request.form.get("device") or "").strip() or None
        iso_at = (request.form.get("iso_at") or "").strip() or None
        date_input = (request.form.get("date") or "").strip() or None
        time_input = (request.form.get("time") or "").strip() or None

        errors = []
        if not media_input:
            errors.append("Media is required.")
        else:
            try:
                parse_media_reference(media_input)
            except ValueError as exc:
                errors.append(str(exc))

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
            args = SimpleNamespace(media=media_input, device=device_name)
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

    template = """
    <!doctype html>
    <title>Spotify Scheduler</title>
    <style>
      body { font-family: sans-serif; margin: 2rem; }
      form { display: grid; gap: 1rem; max-width: 32rem; }
      label { display: grid; gap: 0.5rem; }
      .messages { margin-bottom: 1rem; }
      .messages div { padding: 0.5rem 0.75rem; border-radius: 0.25rem; }
      .messages .error { background: #ffeaea; color: #521616; }
      .messages .success { background: #e7ffee; color: #13552c; }
      fieldset { border: 1px solid #ccc; padding: 1rem; border-radius: 0.5rem; }
      legend { font-weight: bold; }
      button { padding: 0.75rem; font-size: 1rem; }
      select, input { padding: 0.5rem; font-size: 1rem; }
      .device-warning { color: #a04900; }
    </style>
    <h1>Schedule Spotify Playback</h1>
    <div class="messages">
      {% for category, message in get_flashed_messages(with_categories=True) %}
        <div class="{{ category }}">{{ message }}</div>
      {% endfor %}
      {% if device_error %}
        <div class="error">{{ device_error }}</div>
      {% endif %}
    </div>
    <form method="post">
      <label>
        Spotify media (URI, link, or 22-char ID)
        <input name="media" type="text" required>
      </label>
      <label>
        Device
        <select name="device">
          <option value="">Auto-select active/default</option>
          {% for device in devices %}
            <option value="{{ device.name }}">{{ device.name }} ({{ device.type }})</option>
          {% endfor %}
        </select>
      </label>
      <fieldset>
        <legend>Schedule</legend>
        <label>
          ISO datetime (e.g. 2025-10-03T08:30)
          <input name="iso_at" type="text" placeholder="Optional">
        </label>
        <div>— or —</div>
        <label>
          Date (YYYY-MM-DD)
          <input name="date" type="date" placeholder="Optional">
        </label>
        <label>
          Time (HH:MM or HH:MM:SS)
          <input name="time" type="time" step="1" placeholder="Optional">
        </label>
      </fieldset>
      <button type="submit">Schedule Playback</button>
    </form>
    <section>
      <h2>Detected Devices</h2>
      {% if devices %}
        <ul>
          {% for device in devices %}
            <li>{{ device.name }} — {{ device.type }}{% if device.is_active %} (active){% endif %}</li>
          {% endfor %}
        </ul>
      {% else %}
        <p class="device-warning">No devices reported. Launch Spotify to make one available.</p>
      {% endif %}
    </section>
    """
    return render_template_string(
        template,
        devices=[SimpleNamespace(**item) for item in devices],
        device_error=device_error,
    )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=os.environ.get("FLASK_DEBUG") == "1")
