"""Finds newer versions on the project's GitHub Releases page and installs them."""
import hashlib
import json
import os
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

REPO = "EmporiumDevelopment/TTLSCustomRoomIdGrabber"
LATEST_RELEASE = f"https://api.github.com/repos/{REPO}/releases/latest"
INSTALLER = "RoomIDOverlay-Setup.exe"
USER_AGENT = {"User-Agent": "RoomIDOverlay"}
BASE = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))


def current_version():
    """The version this copy was built as (version.txt is bundled into the app)."""
    return (BASE / "version.txt").read_text().strip()


def _parts(version):
    numbers = [int(n) for n in re.findall(r"\d+", version)]
    return tuple((numbers + [0, 0, 0])[:3])


def find_update():
    """The newer release as {version, url, size, sha256}, or None. Raises if GitHub can't be reached."""
    if not getattr(sys, "frozen", False):  # running from source: nothing to replace
        return None
    request = urllib.request.Request(LATEST_RELEASE, headers={**USER_AGENT, "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(request, timeout=15) as response:
        release = json.load(response)
    version = release["tag_name"].lstrip("v")
    if _parts(version) <= _parts(current_version()):
        return None
    asset = next((a for a in release.get("assets", []) if a["name"] == INSTALLER), None)
    if asset is None:
        return None
    digest = asset.get("digest") or ""
    return {"version": version, "url": asset["browser_download_url"], "size": asset["size"],
            "sha256": digest.removeprefix("sha256:")}


def download(update, dest, on_progress):
    """Save the installer to dest, calling on_progress(0..1). Raises if the file comes down wrong."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(".part")
    sha, done = hashlib.sha256(), 0
    request = urllib.request.Request(update["url"], headers=USER_AGENT)
    with urllib.request.urlopen(request, timeout=30) as response, open(part, "wb") as f:
        while chunk := response.read(65536):
            f.write(chunk)
            sha.update(chunk)
            done += len(chunk)
            on_progress(done / update["size"])
    if done != update["size"] or (update["sha256"] and sha.hexdigest() != update["sha256"]):
        part.unlink()
        raise ValueError("The downloaded update was incomplete or damaged")
    os.replace(part, dest)


def install(installer):
    """Start the installer. It closes this app, updates the files and reopens the app."""
    subprocess.Popen([str(installer), "/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/SP-"],
                     creationflags=subprocess.DETACHED_PROCESS, close_fds=True)
