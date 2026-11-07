# Room ID Overlay

Reads the **Room ID** from the PUBG Mobile custom room screen and shows it, with her
password, on stream in TikTok LIVE Studio. It's a normal Windows app with an installer and a
step-by-step setup wizard, so she never touches Python or a terminal.

## 1. Build the installer (you, once)

**Option A: GitHub (nothing to install).** Push this folder to a GitHub repo. The workflow in
`.github/workflows/build.yml` builds it on Windows. Go to the repo's **Actions** tab → latest run →
download **RoomIDOverlay-Setup** under Artifacts.

**Option B: a Windows PC.** Install Python 3.11 and Inno Setup 6, then double-click `build.bat`.
The installer ends up in `Output\RoomIDOverlay-Setup.exe`.

## 2. Test it without an Elgato (you)

Use any screenshot that shows the custom room screen:
- **Easiest:** drag the screenshot onto the **Room ID Overlay** icon (desktop shortcut or the .exe).
  The app opens in test mode and uses the picture as if it were the Elgato.
- Or from the project folder: `python app.py --image screenshot.png`

The best test picture is a **screenshot taken on her phone** while the custom room is open,
because that's exactly what the Elgato sees. A screenshot of her TikTok stream works too (tested).

Test mode keeps its own settings, so it never touches her real setup.

## 3. Send her the installer

Send `RoomIDOverlay-Setup.exe` with this message:

> Double-click it → Install. If Windows says "Windows protected your PC", click **More info**
> then **Run anyway**. The app then walks you through setup step by step.

The wizard does the rest:
1. **Game:** shows each camera/capture device; she clicks *Yes, that's my game* when she sees PUBG.
2. **Room:** she opens a custom room (with an example picture to show what that looks like).
3. **Room ID:** the app finds it **by itself** and asks "Is this your Room ID?". If it can't find
   it, she can draw a box instead.
4. **Password:** she types it once.
5. **LIVE Studio:** *Copy link* → Add source → Link → paste. The app shows a ✓ once LIVE Studio
   is connected, so she knows it worked.

After that it opens to a simple screen showing what's on stream, with a password box and a
*Run setup again* button.

## If something's off
- **Every device is black:** her Elgato may not allow two apps at once. The wizard then offers
  *Your whole screen*, which reads her LIVE Studio preview. That's less reliable because the preview is small.
- **Wrong number on stream:** she can type the right one in *Wrong ID?* on the main screen.
- Settings: `%APPDATA%\RoomIDOverlay\settings.json`

## Files
- `app.py`: desktop app and setup wizard
- `roomid.py`: OCR and overlay engine (also usable from the command line)
- `overlay.html`: the box shown on stream
- `example_room.png`, `example_roomid.png`: pictures shown in the wizard
- `installer.iss`, `build.bat`, `requirements-build.txt`, `.github/workflows/build.yml`: packaging
