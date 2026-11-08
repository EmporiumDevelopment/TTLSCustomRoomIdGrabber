"""
Room ID Overlay - desktop app with a step-by-step setup wizard.

First launch walks through setup one screen at a time:
  welcome > find the game feed > open a custom room > auto-find the Room ID >
  password > add to TikTok LIVE Studio (auto-detected) > done
After that it opens to a small everyday screen.
"""
import sys

if "--watch" in sys.argv:  # the background watcher: stop here, before the heavy imports below
    import watcher
    raise SystemExit(watcher.run())

import functools
import json
import os
import re
import threading
import time
import tkinter as tk
from http.server import ThreadingHTTPServer
from pathlib import Path
from tkinter import messagebox, ttk

import mss
import numpy as np
from PIL import Image, ImageTk

import roomid as core
import updater
import watcher

APP_NAME = "Room ID Overlay"
PORT = watcher.APP_PORT
LOCAL_URL = f"http://localhost:{PORT}/"
RELAY = os.environ.get("ROOMID_RELAY", "https://ntfy.sh/")  # free relay: the app posts updates, the overlay page listens


def overlay_base():
    """Address of the overlay page on GitHub Pages; written into the app when it's built."""
    try:
        return (core.BASE / "overlay_url.txt").read_text().strip()
    except Exception:
        return ""
SCREEN = "screen"
SETTINGS_DIR = Path(os.environ.get("APPDATA", Path.home())) / "RoomIDOverlay"
SETTINGS_FILE = SETTINGS_DIR / "settings.json"
UPDATE_FILE = SETTINGS_DIR / "update" / updater.INSTALLER

BG, PANEL, TEXT, MUTED, ACCENT, GOOD, BAD = (
    "#262626", "#303030", "#ececec", "#9b9b9b", "#f5b400", "#4cd07d", "#ff6b6b")
FIELD, BUTTON, BUTTON_HOVER, TOPBAR, SIDEBAR, SELECTED, DIVIDER = (
    "#1b1b1b", "#4d4d4d", "#5c5c5c", "#0c0c0c", "#1f1f1f", "#484848", "#3d3d3d")
NOTICE, NOTICE_HOVER = "#8ab8e6", "#a3c8ee"
FONT = "Segoe UI"
FONT_SEMI = "Segoe UI Semibold"
WIDTH = 640
HEIGHT = 764
STEPS = ["Game", "Room", "Room ID", "Password", "LIVE Studio"]

# How the Room ID box looks on stream. These defaults are the original look; "Customise box" changes them.
# Sizes marked % are a percentage of the text size, so the box keeps its shape when the text is resized.
DEFAULT_STYLE = {
    # text
    "font": "Segoe UI", "size": 64, "weight": 800, "italic": False, "text_color": "#ffffff",
    "letter_spacing": 2, "align": "right",
    # labels
    "show_labels": True, "id_label": "ID", "pw_label": "PW", "label_color": "#ffffff", "label_size": 100,
    # box fill and border
    "box_color": "#141418", "box_opacity": 82, "gradient": False, "box_color2": "#6a3093",
    "gradient_angle": 135, "border_width": 0, "border_color": "#ffffff",
    # shape and spacing
    "shape": "rounded", "radius": 10, "layout": "stacked", "line_height": 115,
    "pad_x": 60, "pad_y": 25, "col_gap": 160, "row_gap": 5,
    # text effects
    "shadow": True, "shadow_color": "#000000", "shadow_opacity": 50, "shadow_blur": 6, "shadow_y": 2,
    "outline_width": 0, "outline_color": "#000000",
    # box effects
    "glow_size": 0, "glow_color": "#f5b400", "anim": "zoom", "flash": True, "flash_color": "#ffffff",
}


# ------------------------------------------------------------- box preview ---
# Draws the box the way the overlay page does, so the app can show a preview.

@functools.lru_cache(maxsize=None)
def _font_files():
    """Installed fonts: {"segoe ui bold": "C:/Windows/Fonts/segoeuib.ttf", ...}"""
    out = {}
    try:
        import winreg
    except ImportError:
        return out
    key = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts"
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            k = winreg.OpenKey(hive, key)
        except OSError:
            continue
        i = 0
        while True:
            try:
                name, file, _ = winreg.EnumValue(k, i)
            except OSError:
                break
            i += 1
            name = re.sub(r"\s*\(.*\)\s*$", "", name)  # drop "(TrueType)"
            path = file if os.path.isabs(file) else os.path.join(
                os.environ.get("WINDIR", "C:/Windows"), "Fonts", file)
            for n in name.split("&"):
                out.setdefault(n.strip().lower(), path)
    return out


WEIGHT_NAMES = {300: ["Light", "Semilight"], 400: [""], 500: ["Medium", ""], 600: ["Semibold", "SemiBold", "Bold"],
                700: ["Bold"], 800: ["ExtraBold", "Extra Bold", "Black", "Bold"], 900: ["Black", "Heavy", "Bold"]}


# How each thickness appears in an installed font's name
FONT_WEIGHTS = {300: ("light",), 400: ("", "regular"), 500: ("medium",), 600: ("semibold", "demibold", "demi"),
                700: ("bold",), 800: ("extrabold", "extra bold", "ultrabold"), 900: ("black", "heavy")}


@functools.lru_cache(maxsize=None)
def available_weights(family):
    """The thicknesses this font really has. If it isn't in the font list, all of them, as we can't tell."""
    from PIL import ImageFont
    files, name = _font_files(), family.lower()
    found = [w for w, words in FONT_WEIGHTS.items() if any(f"{name} {word}".strip() in files for word in words)]
    if found == [400]:  # one file can still hold every thickness (a "variable" font)
        try:
            axis = next(a for a in ImageFont.truetype(files[name]).get_variation_axes() if a["name"] == b"Weight")
            found = [w for w in FONT_WEIGHTS if axis["minimum"] <= w <= axis["maximum"]]
        except Exception:  # an ordinary font: Regular really is all it has
            pass
    return found or list(FONT_WEIGHTS)


def closest_weight(weight, available):
    """The same choice browsers make for a missing thickness, so switching to it never changes the look."""
    if weight in available:
        return weight
    heavier = sorted(w for w in available if w > weight)
    lighter = sorted((w for w in available if w < weight), reverse=True)
    return (heavier + lighter if weight > 500 else lighter + heavier)[0]


@functools.lru_cache(maxsize=256)
def load_font(family, px, weight=400, italic=False):
    from PIL import ImageFont
    files = _font_files()
    for fam in (family, "Segoe UI"):
        for w in WEIGHT_NAMES.get(weight, [""]) + [""]:
            for it in (["Italic", "Oblique", ""] if italic else [""]):
                path = files.get(" ".join(x for x in (fam, w, it) if x).lower())
                if path:
                    try:
                        return ImageFont.truetype(path, px)
                    except OSError:
                        pass
    return ImageFont.load_default(px)


def _rgb(hex_color):
    return tuple(int(hex_color[i:i + 2], 16) for i in (1, 3, 5))


SWITCH_FRAMES = 8
FADE_SECONDS = 0.18


def smooth(t):
    """Eases 0..1 so movement starts and stops gently."""
    return t * t * (3 - 2 * t)


@functools.lru_cache(maxsize=None)
def switch_image(frame):
    """One frame of the on/off switch (0 = off, SWITCH_FRAMES = on), drawn large and shrunk so its curves are smooth."""
    from PIL import ImageDraw
    t = smooth(frame / SWITCH_FRAMES)
    scale, w, h = 4, 36, 18
    color = tuple(round(off + (on - off) * t) for off, on in zip(_rgb(BUTTON), _rgb(GOOD)))
    img = Image.new("RGB", (w * scale, h * scale), _rgb(BG))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle([0, 0, w * scale - 1, h * scale - 1], h * scale // 2, fill=color)
    inset, knob = 2 * scale, (h - 4) * scale
    left = inset + round((w * scale - 2 * inset - knob) * t)
    draw.ellipse([left, inset, left + knob, inset + knob], fill=(255, 255, 255))
    return ImageTk.PhotoImage(img.resize((w, h), Image.LANCZOS))


def animations_enabled():
    """Follows Windows' own "Animation effects" setting."""
    try:
        import ctypes
        on = ctypes.c_int(1)
        ctypes.windll.user32.SystemParametersInfoW(0x1042, 0, ctypes.byref(on), 0)  # SPI_GETCLIENTAREAANIMATION
        return bool(on.value)
    except Exception:
        return True


def render_box(st, room_id="12345678", password="1234"):
    """The overlay box as a transparent PIL image at its real on-stream size."""
    from PIL import ImageChops, ImageDraw, ImageFilter
    st = {**DEFAULT_STYLE, **st}
    s = st["size"]
    f = load_font(st["font"], s, st["weight"], st["italic"])
    lf = load_font(st["font"], max(6, round(s * st["label_size"] / 100)), st["weight"], st["italic"])
    ls = s * st["letter_spacing"] / 100

    def tlen(font, t):
        return font.getlength(t) + ls * len(t)

    pairs = [(st["id_label"], room_id), (st["pw_label"], password)]
    rows = [pairs[0] + pairs[1]] if st["layout"] == "row" else [pairs[0], pairs[1]]
    rows = [[(t, i % 2 == 0) for i, t in enumerate(r) if st["show_labels"] or i % 2] for r in rows]
    ncol = len(rows[0])
    widths = [max(tlen(lf if r[i][1] else f, r[i][0]) for r in rows) for i in range(ncol)]
    line, gap_c, gap_r = s * st["line_height"] / 100, s * st["col_gap"] / 100, s * st["row_gap"] / 100
    pad_x, pad_y = s * st["pad_x"] / 100, s * st["pad_y"] / 100
    w = sum(widths) + gap_c * (ncol - 1) + 2 * pad_x
    h = line * len(rows) + gap_r * (len(rows) - 1) + 2 * pad_y
    slant = st["shape"] == "slant"
    m = int(max(st["glow_size"] * 2, st["shadow_blur"] * 2 + abs(st["shadow_y"]), 4)
            + (h * 0.22 if slant else 0) + 8)
    W, H = int(w + 2 * m), int(h + 2 * m)
    x0, y0, x1, y1 = m, m, m + w, m + h

    def outline(draw, inset=0.0, **kw):
        """Draw the box's shape (inset pulls it inwards, for the border)."""
        a, b, c, d = x0 + inset, y0 + inset, x1 - inset, y1 - inset
        shape, r = st["shape"], st["radius"]
        if shape == "cut":
            k = max(0, min(r, w / 2, h / 2) - inset * 0.4)
            draw.polygon([(a + k, b), (c - k, b), (c, b + k), (c, d - k), (c - k, d), (a + k, d), (a, d - k),
                          (a, b + k)], **kw)
        else:
            rad = ((d - b) / 2 if shape == "pill" else 0 if shape == "square"
                   else max(0, min(r, w / 2, h / 2) - inset))
            draw.rounded_rectangle([a, b, c, d], rad, **kw)

    # box fill (solid or gradient) at the chosen opacity
    mask = Image.new("L", (W, H), 0)
    outline(ImageDraw.Draw(mask), fill=255)
    if st["gradient"]:  # same direction rules as CSS linear-gradient
        ang = np.radians(st["gradient_angle"])
        dx, dy = np.sin(ang), -np.cos(ang)
        length = abs(w * dx) + abs(h * dy) or 1
        ys, xs = np.mgrid[0:H, 0:W]
        t = np.clip(((xs - W / 2) * dx + (ys - H / 2) * dy) / length + 0.5, 0, 1)[..., None]
        rgb = np.array(_rgb(st["box_color"])) * (1 - t) + np.array(_rgb(st["box_color2"])) * t
    else:
        rgb = np.broadcast_to(np.array(_rgb(st["box_color"]), float), (H, W, 3))
    a_chan = (np.asarray(mask, float) * st["box_opacity"] / 100)[..., None]
    layer = Image.fromarray(np.concatenate([rgb, a_chan], axis=2).astype(np.uint8), "RGBA")
    bw = st["border_width"]
    if bw:
        border = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        outline(ImageDraw.Draw(border), inset=bw / 2, outline=_rgb(st["border_color"]) + (255,), width=int(bw))
        layer = Image.alpha_composite(layer, border)
    if slant:  # CSS skewX(-12deg) around the middle
        k = float(np.tan(np.radians(12)))
        layer = layer.transform((W, H), Image.AFFINE, (1, k, -k * H / 2, 0, 1, 0), Image.BICUBIC)
    out = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    if st["glow_size"]:
        glow = Image.new("RGBA", (W, H), _rgb(st["glow_color"]) + (0,))
        glow.putalpha(layer.getchannel("A"))
        solid = mask.transform((W, H), Image.AFFINE, (1, k, -k * H / 2, 0, 1, 0), Image.BICUBIC) if slant else mask
        for radius in (st["glow_size"] / 4, st["glow_size"] / 2):
            halo = glow.filter(ImageFilter.GaussianBlur(radius))
            if st["shape"] != "cut":  # like the overlay: glow only outside the box, so see-through boxes keep their colour
                halo.putalpha(ImageChops.subtract(halo.getchannel("A"), solid))
            out = Image.alpha_composite(out, halo)
    out = Image.alpha_composite(out, layer)

    # the text, lined up on a shared baseline like the overlay's grid
    asc, desc = f.getmetrics()
    stroke = round(st["outline_width"] / 2)

    def draw_text(draw, dy, fill, stroke_fill):
        for ri, r in enumerate(rows):
            base = y0 + pad_y + ri * (line + gap_r) + line / 2 + (asc - desc) / 2 + dy
            x = x0 + pad_x
            for i, (t, lab) in enumerate(r):
                font = lf if lab else f
                tw = tlen(font, t)
                if lab or st["align"] == "left":
                    tx = x
                elif st["align"] == "center":
                    tx = x + (widths[i] - tw) / 2
                else:
                    tx = x + widths[i] - tw
                color = fill or _rgb(st["label_color"] if lab else st["text_color"]) + (255,)
                for ch in t:
                    draw.text((tx, base), ch, font=font, fill=color, anchor="ls",
                              stroke_width=stroke, stroke_fill=stroke_fill)
                    tx += font.getlength(ch) + ls
                x += widths[i] + gap_c

    if st["shadow"]:
        sh = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        sc = _rgb(st["shadow_color"]) + (round(255 * st["shadow_opacity"] / 100),)
        draw_text(ImageDraw.Draw(sh), st["shadow_y"], sc, sc)
        if st["shadow_blur"]:
            sh = sh.filter(ImageFilter.GaussianBlur(st["shadow_blur"] / 2))
        out = Image.alpha_composite(out, sh)
    txt = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw_text(ImageDraw.Draw(txt), 0, None, _rgb(st["outline_color"]) + (255,))
    return Image.alpha_composite(out, txt)


# --------------------------------------------------------------- settings ---

def load_settings():
    try:
        return json.loads(SETTINGS_FILE.read_text())
    except Exception:
        return {}


def save_settings(s):
    SETTINGS_DIR.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(json.dumps(s, indent=2))


def list_devices():
    """Capture devices in DirectShow order (same order OpenCV uses)."""
    try:
        from pygrabber.dshow_graph import FilterGraph
        names = FilterGraph().get_input_devices()
    except Exception:
        names = [f"Camera {i}" for i in range(4)]
    return list(enumerate(names))


def looks_like_capture_card(name):
    n = name.lower()
    return any(k in n for k in ("elgato", "game capture", "capture", "hd60", "4k60", "cam link", "neo"))


# ---------------------------------------------------------------- windows ---
# Reading one app window (e.g. a phone mirroring app) instead of the whole screen.
# The window is captured directly, so it still works when other windows cover it
# or it gets moved to another monitor.

WINDOW = "window"
MIRROR_HINTS = ("douwan", "airplay", "mirror", "scrcpy", "lonelyscreen", "reflector", "airserver",
                "phone link", "vysor", "letsview", "pubg", "game")
# Windows' own invisible/helper windows that can never show the game
SYSTEM_EXES = ("explorer.exe", "textinputhost.exe", "tabtip.exe", "shellexperiencehost.exe",
               "startmenuexperiencehost.exe", "searchhost.exe", "lockapp.exe", "applicationframehost.exe")


@functools.lru_cache(maxsize=None)
def _win32():
    import ctypes
    from ctypes import wintypes
    u, g, k = ctypes.windll.user32, ctypes.windll.gdi32, ctypes.windll.kernel32
    H = wintypes.HANDLE
    for fn, res, args in (
        (u.GetDC, H, [wintypes.HWND]), (u.ReleaseDC, ctypes.c_int, [wintypes.HWND, H]),
        (u.PrintWindow, wintypes.BOOL, [wintypes.HWND, H, wintypes.UINT]),
        (u.GetClientRect, wintypes.BOOL, [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]),
        (u.IsWindow, wintypes.BOOL, [wintypes.HWND]), (u.IsIconic, wintypes.BOOL, [wintypes.HWND]),
        (u.IsWindowVisible, wintypes.BOOL, [wintypes.HWND]),
        (u.ClientToScreen, wintypes.BOOL, [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]),
        (u.GetWindowTextW, ctypes.c_int, [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]),
        (u.GetWindowThreadProcessId, wintypes.DWORD, [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]),
        (g.CreateCompatibleDC, H, [H]), (g.CreateCompatibleBitmap, H, [H, ctypes.c_int, ctypes.c_int]),
        (g.SelectObject, H, [H, H]), (g.DeleteObject, wintypes.BOOL, [H]), (g.DeleteDC, wintypes.BOOL, [H]),
        (g.GetDIBits, ctypes.c_int, [H, H, wintypes.UINT, wintypes.UINT, ctypes.c_void_p, ctypes.c_void_p,
                                     wintypes.UINT]),
        (k.OpenProcess, H, [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]),
        (k.CloseHandle, wintypes.BOOL, [H]),
        (k.QueryFullProcessImageNameW, wintypes.BOOL, [H, wintypes.DWORD, wintypes.LPWSTR,
                                                        ctypes.POINTER(wintypes.DWORD)]),
    ):
        fn.restype, fn.argtypes = res, args
    return ctypes, wintypes, u, g, k


def _window_exe(hwnd):
    ctypes, wintypes, u, _, k = _win32()
    pid = wintypes.DWORD()
    u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    h = k.OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return pid.value, ""
    try:
        buf, size = ctypes.create_unicode_buffer(512), wintypes.DWORD(512)
        k.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size))
        return pid.value, Path(buf.value).name.lower()
    finally:
        k.CloseHandle(h)


def list_windows():
    """Visible app windows big enough to hold a game picture: [(title, exe), ...]."""
    try:
        ctypes, wintypes, u, _, _ = _win32()
        dwm = ctypes.windll.dwmapi
    except Exception:
        return []
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def each(hwnd, _):
        try:
            if not u.IsWindowVisible(hwnd) or u.IsIconic(hwnd):
                return True
            cloaked = wintypes.DWORD()  # hidden Store-app windows report as visible
            dwm.DwmGetWindowAttribute(hwnd, 14, ctypes.byref(cloaked), 4)
            if cloaked.value:
                return True
            buf = ctypes.create_unicode_buffer(256)
            u.GetWindowTextW(hwnd, buf, 256)
            title = buf.value.strip()
            r = wintypes.RECT()
            u.GetClientRect(hwnd, ctypes.byref(r))
            if not title or r.right < 200 or r.bottom < 150:
                return True
            pid, exe = _window_exe(hwnd)
            if pid != os.getpid() and exe not in SYSTEM_EXES:
                found.append((title, exe))
        except Exception:
            pass
        return True

    u.EnumWindows(each, 0)
    hint = lambda w: not any(h in (w[0] + " " + w[1]).lower() for h in MIRROR_HINTS)
    return sorted(found, key=hint)  # mirroring apps first


def is_live_studio(title, exe):
    t = (title + " " + exe).lower()
    return "live studio" in t or "livestudio" in t or ("tiktok" in t and "studio" in t)


def find_window(title, exe):
    """The window with this title, or failing that one from the same program."""
    try:
        ctypes, wintypes, u, _, _ = _win32()
    except Exception:
        return None
    exact, same_app = [], []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def each(hwnd, _):
        try:
            if not u.IsWindowVisible(hwnd):
                return True
            buf = ctypes.create_unicode_buffer(256)
            u.GetWindowTextW(hwnd, buf, 256)
            if buf.value.strip() == title:
                exact.append(hwnd)
            elif exe and _window_exe(hwnd)[1] == exe:
                r = wintypes.RECT()
                u.GetClientRect(hwnd, ctypes.byref(r))
                if r.right >= 200 and r.bottom >= 150:
                    same_app.append(hwnd)
        except Exception:
            pass
        return True

    u.EnumWindows(each, 0)
    return (exact or same_app or [None])[0]


class WindowStream:
    """Live pictures of one window through Windows Graphics Capture (what OBS uses). Works for
    GPU-drawn apps like DouWan and TikTok LIVE Studio, even when other windows cover them."""

    def __init__(self, hwnd):
        from windows_capture import WindowsCapture
        self.latest, self.closed = None, False
        cap = None
        for border in (False, None):  # hiding the yellow capture border needs Windows 11
            try:
                cap = WindowsCapture(cursor_capture=False, draw_border=border,
                                     minimum_update_interval=200, window_hwnd=int(hwnd))
                break
            except Exception:
                if border is None:
                    raise

        @cap.event
        def on_frame_arrived(frame, control):
            self.latest = np.array(frame.frame_buffer)[:, :, :3].copy()  # BGRA -> BGR

        @cap.event
        def on_closed():
            self.closed = True

        self.control = cap.start_free_threaded()

    def stop(self):
        try:
            self.control.stop()
        except Exception:
            pass


def grab_window(hwnd, sct):
    """Picture of a window's contents (BGR). Asks the window to draw itself first, which works
    even when it's covered; apps that draw video on the GPU (like DouWan) refuse that, so then
    the window's spot on screen is read instead, wherever it has been moved to."""
    ctypes, wintypes, u, g, _ = _win32()
    if not u.IsWindow(hwnd) or u.IsIconic(hwnd):
        return None
    r = wintypes.RECT()
    u.GetClientRect(hwnd, ctypes.byref(r))
    w, h = r.right, r.bottom
    if w < 20 or h < 20:
        return None
    f = _print_window(hwnd, w, h)
    if f is not None and not is_black(f):
        return f
    pt = wintypes.POINT(0, 0)
    u.ClientToScreen(hwnd, ctypes.byref(pt))
    return np.array(sct.grab({"left": pt.x, "top": pt.y, "width": w, "height": h}))[:, :, :3]


def _print_window(hwnd, w, h):
    ctypes, wintypes, u, g, _ = _win32()

    class BMI(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("w", wintypes.LONG), ("h", wintypes.LONG),
                    ("planes", wintypes.WORD), ("bits", wintypes.WORD), ("comp", wintypes.DWORD),
                    ("img", wintypes.DWORD), ("xppm", wintypes.LONG), ("yppm", wintypes.LONG),
                    ("used", wintypes.DWORD), ("imp", wintypes.DWORD), ("pad", wintypes.DWORD * 4)]

    hdc = u.GetDC(hwnd)
    mdc = g.CreateCompatibleDC(hdc)
    bmp = g.CreateCompatibleBitmap(hdc, w, h)
    old = g.SelectObject(mdc, bmp)
    try:
        if not u.PrintWindow(hwnd, mdc, 3):  # PW_CLIENTONLY | PW_RENDERFULLCONTENT
            return None
        bmi = BMI(size=40, w=w, h=-h, planes=1, bits=32)  # negative height = rows top to bottom
        buf = ctypes.create_string_buffer(w * h * 4)
        g.GetDIBits(mdc, bmp, 0, h, buf, ctypes.byref(bmi), 0)
    finally:
        g.SelectObject(mdc, old)
        g.DeleteObject(bmp)
        g.DeleteDC(mdc)
        u.ReleaseDC(hwnd, hdc)
    return np.frombuffer(buf.raw, np.uint8).reshape(h, w, 4)[:, :, :3].copy()


# ---------------------------------------------------------------- capture ---

def device_worker(conn, index):
    """Runs in its own process: opens one camera/capture device and hands frames to the app.
    Some camera drivers crash when opened; in here that only kills this helper, not the app."""
    setup_crash_log("device")
    cap = core.open_device(index)
    if not cap.isOpened():
        conn.send(("failed", None))
        return
    latest = [None]

    def pump():
        while True:
            ok, f = cap.read()
            latest[0] = f if ok else None
            if not ok:
                time.sleep(0.1)

    threading.Thread(target=pump, daemon=True).start()
    conn.send(("ok", None))
    while True:
        try:
            msg = conn.recv()
        except EOFError:
            break
        if msg == "stop":
            break
        f = latest[0]
        conn.send(("frame", None if f is None else (f.shape, f.tobytes())))
    cap.release()


class Capture:
    """Background thread that always holds the newest full frame from the chosen source."""

    def __init__(self):
        self.lock = threading.Lock()
        self.source = None  # device index (int), SCREEN, or None
        self.frame = None
        self.failed = set()  # devices that crashed or wouldn't open
        threading.Thread(target=self._run, daemon=True).start()

    def set_source(self, source):
        with self.lock:
            self.source = source
            self.frame = None

    def get(self):
        with self.lock:
            return self.frame

    def _run(self):
        import multiprocessing as mp
        sct = None
        proc = conn = None
        hwnd = stream = None
        use_wgc = True
        current = object()
        try:  # this thread sees real pixel sizes on every monitor, whatever their scaling
            import ctypes
            ctypes.windll.user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
        except Exception:
            pass
        while True:
            src = self.source
            if src != current:
                if proc is not None:  # close the old device helper
                    try:
                        conn.send("stop")
                    except Exception:
                        pass
                    proc.join(1)
                    if proc.is_alive():
                        proc.terminate()
                    proc = conn = None
                current = src
                hwnd = None
                if stream:
                    stream.stop()
                    stream = None
                if isinstance(src, int) and src not in self.failed:
                    conn, child = mp.Pipe()
                    proc = mp.Process(target=device_worker, args=(child, src), daemon=True)
                    proc.start()
                    child.close()  # so we notice straight away if the helper dies
                    try:
                        ok = conn.poll(15) and conn.recv()[0] == "ok"
                    except (EOFError, OSError):  # helper crashed while opening the device
                        ok = False
                    if not ok:
                        print(f"Device {src} couldn't be opened (exit code {proc.exitcode})")
                        self.failed.add(src)
                        proc.terminate()
                        proc = conn = None
            frame = None
            try:
                if src == SCREEN:
                    if sct is None:
                        sct = mss.mss()
                    frame = np.array(sct.grab(sct.monitors[0]))[:, :, :3]
                    time.sleep(0.25)
                elif isinstance(src, tuple) and src[0] == WINDOW:
                    if hwnd is None or not ctypes.windll.user32.IsWindow(hwnd) or (stream and stream.closed):
                        if stream:
                            stream.stop()
                        stream = None
                        hwnd = find_window(src[1], src[2])  # app restarted or window reopened
                        if hwnd and use_wgc:
                            try:
                                stream = WindowStream(hwnd)
                            except Exception as e:  # older Windows: fall back to the simpler ways
                                print("Window capture unavailable, using screen reading:", e)
                                use_wgc = False
                    if stream:
                        frame = stream.latest
                    elif hwnd:
                        if sct is None:
                            sct = mss.mss()
                        frame = grab_window(hwnd, sct)
                    time.sleep(0.25)
                elif proc is not None:
                    conn.send("get")
                    if conn.poll(5):
                        _, data = conn.recv()
                        if data is not None:
                            shape, raw = data
                            frame = np.frombuffer(raw, np.uint8).reshape(shape)
                    else:
                        raise RuntimeError("device stopped answering")
                    time.sleep(0.15)
                else:
                    time.sleep(0.2)
            except Exception:
                if proc is not None and not proc.is_alive():  # the driver crashed the helper
                    self.failed.add(src)
                    proc = conn = None
                time.sleep(0.3)
            with self.lock:
                if self.source == src:
                    self.frame = frame


# ------------------------------------------------------------- crash log ---

LOG_DIR = Path(os.environ.get("APPDATA", Path.home())) / "RoomIDOverlay"


def setup_crash_log(name="app"):
    """Write crashes and errors to %APPDATA%\\RoomIDOverlay\\<name>-log.txt (windowed apps have no console)."""
    import faulthandler
    import sys
    import traceback
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        f = open(LOG_DIR / f"{name}-log.txt", "w", buffering=1, encoding="utf-8")
    except Exception:
        return
    sys.stdout = sys.stderr = f
    faulthandler.enable(f, all_threads=True)  # native crashes (driver bugs) still leave a trace

    def log_exc(*args):
        traceback.print_exception(*args, file=f)

    sys.excepthook = log_exc
    threading.excepthook = lambda a: traceback.print_exception(a.exc_type, a.exc_value, a.exc_traceback, file=f)
    print(f"{name} started {time.ctime()}")


def is_black(frame):
    return frame is None or frame.mean() < 5


def crop(frame, region):
    """region = [x, y, w, h] as fractions of the frame, so it survives resolution changes."""
    if frame is None or not region:
        return None
    H, W = frame.shape[:2]
    x, y, w, h = region
    c = frame[int(y * H):int((y + h) * H), int(x * W):int((x + w) * W)]
    return c if c.size else None


def to_photo(bgr_or_pil, max_w, max_h):
    img = bgr_or_pil if isinstance(bgr_or_pil, Image.Image) else Image.fromarray(bgr_or_pil[:, :, ::-1])
    img = img.copy()
    img.thumbnail((max_w, max_h))
    return ImageTk.PhotoImage(img)


# ---------------------------------------------------------------- scanner ---

ROOM_WORD = re.compile(r"r[o0]{2}m", re.I)  # "Room", allowing for OCR slips


class Scanner:
    """Reads the Room ID area twice a second and updates the overlay."""

    def __init__(self, capture, state, settings):
        self.capture, self.state, self.settings = capture, state, settings
        self.reader = None
        self.ocr_lock = threading.Lock()
        self.error = None
        self.last_text = None
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        try:
            self.reader = core.Reader(0.6)
        except Exception as e:
            self.error = f"The text reader didn't start: {e}"
            return
        last, streak, was_open = None, 0, False
        lines = []
        while True:
            t0 = time.time()
            is_open = time.time() - self.state.last_seen < self.state.HIDE_AFTER
            if is_open != was_open:  # leave a trail in the log for troubleshooting
                print(f"[{time.strftime('%H:%M:%S')}] Room screen {'found' if is_open else 'gone'}"
                      f"  (last read: {lines})")
                was_open = is_open
            try:
                img = crop(self.capture.get(), self.settings.get("region"))
                if img is not None:
                    with self.ocr_lock:
                        lines = self.reader.lines(img)
                    rid, _ = core.parse(lines, 6, 10)
                    known = rid is not None and rid == self.state.data["room_id"]
                    if rid and not known and not ROOM_WORD.search(" ".join(lines)):
                        rid = None  # a new number, but not the "Room ID" text: no room screen open
                    self.last_text = rid
                    if rid:
                        streak = streak + 1 if rid == last else 1
                        last = rid
                        if streak >= 2:  # read the same ID twice in a row
                            self.state.detected(rid, None)  # also marks the room as open
            except Exception as e:
                self.error = f"Read error: {e}"
            time.sleep(max(0.0, 0.5 - (time.time() - t0)))

    def find_room_id(self, frame):
        """Look over the whole picture for 'Room ID: 1234567'. Returns a region or None."""
        while self.reader is None and self.error is None:
            time.sleep(0.2)
        if self.reader is None:
            return None
        with self.ocr_lock:
            res, _ = self.reader.engine(np.ascontiguousarray(frame))
        if not res:
            return None
        H, W = frame.shape[:2]
        items = []
        for box, txt, _ in res:
            xs, ys = [p[0] for p in box], [p[1] for p in box]
            items.append((min(xs), min(ys), max(xs), max(ys), core.join_digit_groups(txt)))
        room = re.compile(r"r[o0]{2}m\s*[il1|][d0o]", re.I)  # tolerate OCR slips like "RoomI0"
        digits = re.compile(r"\d{6,10}")
        pick = None
        for it in items:  # label and number read as one piece of text
            if room.search(it[4]) and digits.search(it[4]):
                pick = it[:4]
                break
        if pick is None:  # label and number read separately, side by side
            for lab in items:
                if not room.search(lab[4]):
                    continue
                h, cy = lab[3] - lab[1], (lab[1] + lab[3]) / 2
                for d in items:
                    if d is lab or not digits.fullmatch(d[4].strip()):
                        continue
                    if abs((d[1] + d[3]) / 2 - cy) < h and lab[0] < d[0] < lab[2] + (lab[2] - lab[0]) * 3:
                        pick = (min(lab[0], d[0]), min(lab[1], d[1]), max(lab[2], d[2]), max(lab[3], d[3]))
                        break
                if pick:
                    break
        if pick is None:
            return None
        x0, y0, x1, y1 = pick
        h = y1 - y0
        x0, y0 = max(0, x0 - h), max(0, y0 - h * 0.6)
        x1, y1 = min(W, x1 + h * 2), min(H, y1 + h * 0.6)  # room on the right for longer IDs
        return [x0 / W, y0 / H, (x1 - x0) / W, (y1 - y0) / H]


# ------------------------------------------------------------------ relay ---

class Relay:
    """Sends what's on stream to the overlay page (via ntfy.sh), and notices when
    LIVE Studio has loaded that page."""

    def __init__(self, state, topic):
        self.state, self.topic = state, topic
        self.error = None
        threading.Thread(target=self._publish_loop, daemon=True).start()
        threading.Thread(target=self._seen_loop, daemon=True).start()

    def _post(self, url, body):
        import urllib.request
        req = urllib.request.Request(url, data=body.encode(), method="POST",
                                     headers={"User-Agent": "RoomIDOverlay"})
        urllib.request.urlopen(req, timeout=10).read()

    def _payload(self):
        s = self.state.snapshot()
        return {"room_id": s["display_room_id"], "password": s["display_password"],
                "visible": s["on_stream"], "style": s["style"]}

    def _publish_loop(self):
        sent, sent_at = None, 0.0
        while True:
            payload = self._payload()
            # send when something changes, and hourly so the relay always holds the latest
            if payload != sent or time.time() - sent_at > 3600:
                # let changes settle (e.g. a slider being dragged) so the relay isn't flooded
                for _ in range(10):
                    time.sleep(0.3)
                    now = self._payload()
                    if now == payload:
                        break
                    payload = now
                try:
                    self._post(RELAY + self.topic, json.dumps(payload))
                    sent, sent_at, self.error = payload, time.time(), None
                except Exception as e:
                    self.error = f"Can't reach the internet relay: {e}"
                    print(self.error)
                    time.sleep(5)
                    continue
            time.sleep(0.3)

    def _seen_loop(self):
        import urllib.request
        while True:
            try:
                req = urllib.request.Request(RELAY + self.topic + "-seen/json",
                                             headers={"User-Agent": "RoomIDOverlay"})
                with urllib.request.urlopen(req, timeout=90) as r:  # ntfy sends keepalives
                    for line in r:
                        try:
                            if json.loads(line).get("event") == "message":
                                self.state.overlay_seen = time.time()
                        except ValueError:
                            pass
            except Exception as e:
                print("Seen listener:", e)
                time.sleep(5)


# -------------------------------------------------------------------- app ---

class App:
    def __init__(self, root):
        self.root = root
        self.settings = load_settings()
        self.state = core.State()
        self.capture = Capture()
        self.devices = list_devices()
        self.page_tick = None
        self.images = []
        self.update, self.update_banner, self.update_status = None, None, None
        self.update_check, self.updating, self.update_dismissed = None, False, False
        self.animate, self.fading = animations_enabled(), False

        if self.settings.get("password"):
            self.state.apply({"manual_password": self.settings["password"]})
        self.state.apply({"visible": self.settings.get("visible", True),
                          "style": {**DEFAULT_STYLE, **self.settings.get("style", {})}})

        self._start_server()
        try:
            watcher.refresh()
        except OSError as e:
            print("Couldn't restart the LIVE Studio watcher:", e)
        self.scanner = Scanner(self.capture, self.state, self.settings)
        if not self.settings.get("topic"):  # this install's private channel
            import secrets
            self._save(topic="roomid-" + secrets.token_hex(10))
        self.relay = Relay(self.state, self.settings["topic"])
        base = overlay_base()
        self.overlay_url = f"{base}?t={self.settings['topic']}" if base else LOCAL_URL
        self._setup_window()

        src = self.settings.get("source")
        if self.settings.get("setup_done"):
            self._use_saved_source()
            self.page_dashboard()
        else:
            self.page_welcome()
        self._check_for_update()
        self._tick()

    # plumbing ---------------------------------------------------------------
    def _start_server(self):
        try:
            srv = ThreadingHTTPServer(("127.0.0.1", PORT), core.make_handler(self.state))
        except OSError:
            messagebox.showerror(APP_NAME, "Room ID Overlay is already open.\n\n"
                                           "Look for it on your taskbar.")
            self.root.destroy()
            raise SystemExit
        threading.Thread(target=srv.serve_forever, daemon=True).start()

    def _setup_window(self):
        r = self.root
        r.title(APP_NAME)
        r.configure(bg=BG)
        r.geometry(f"{WIDTH}x{HEIGHT}")
        r.resizable(False, False)
        style = ttk.Style(r)
        style.theme_use("clam")
        style.configure("TButton", font=(FONT, 10), padding=(14, 5), background=BUTTON, foreground=TEXT,
                        borderwidth=0, focuscolor=BUTTON)
        style.map("TButton", background=[("active", BUTTON_HOVER), ("disabled", PANEL)],
                  foreground=[("disabled", "#777")])
        style.configure("Big.TButton", font=(FONT_SEMI, 10), padding=(18, 6), background=ACCENT,
                        foreground="#111", focuscolor=ACCENT)
        style.map("Big.TButton", background=[("active", "#ffd040"), ("disabled", "#5a4a10")])
        style.configure("TCombobox", fieldbackground=FIELD, background=BUTTON, foreground=TEXT,
                        arrowcolor=TEXT, padding=4, bordercolor=DIVIDER, lightcolor=FIELD, darkcolor=FIELD)
        style.map("TCombobox", fieldbackground=[("readonly", FIELD)], foreground=[("readonly", TEXT)],
                  selectbackground=[("readonly", FIELD)], selectforeground=[("readonly", TEXT)])
        r.option_add("*TCombobox*Listbox.background", FIELD)
        r.option_add("*TCombobox*Listbox.foreground", TEXT)
        r.option_add("*TCombobox*Listbox.selectBackground", ACCENT)
        r.option_add("*TCombobox*Listbox.selectForeground", "#111")
        r.option_add("*TCombobox*Listbox.font", (FONT, 11))
        style.configure("Style.TNotebook", background=BG, borderwidth=0, tabmargins=0, bordercolor=PANEL,
                        lightcolor=PANEL, darkcolor=PANEL)
        style.configure("Style.TNotebook.Tab", background=SIDEBAR, foreground=MUTED, padding=(9, 5),
                        font=(FONT, 10), borderwidth=0, bordercolor=SIDEBAR, lightcolor=SIDEBAR, darkcolor=SIDEBAR,
                        focuscolor=PANEL)
        style.map("Style.TNotebook.Tab", background=[("selected", PANEL)], foreground=[("selected", TEXT)],
                  bordercolor=[("selected", PANEL)], lightcolor=[("selected", PANEL)],
                  darkcolor=[("selected", PANEL)])
        self._dark_title_bar()
        topbar = tk.Frame(r, bg=TOPBAR, height=44)
        topbar.pack(fill="x")
        topbar.pack_propagate(False)
        tk.Label(topbar, text=APP_NAME, bg=TOPBAR, fg=TEXT, font=(FONT_SEMI, 12)).pack(side="left", padx=(18, 14))
        self.nav = tk.Frame(topbar, bg=TOPBAR)
        # Made once and only recoloured: a new Tk button draws white for a moment, and nothing covers this bar
        self.nav_links = {}
        for key, text, page, side in (("home", "Home", self.page_dashboard, "left"),
                                      ("style", "Customise box", self.page_style, "left"),
                                      ("settings", "⚙  Settings", self.page_settings, "right")):
            self.nav_links[key] = self._flat_button(self.nav, text, page, bg=TOPBAR, selected_bg=TOPBAR)
            self.nav_links[key].pack(side=side, fill="y", padx=(0, 8 if side == "right" else 0))
        self.body = tk.Frame(r, bg=BG)
        self.body.pack(fill="both", expand=True)
        self.cover = tk.Toplevel(r, bg=BG)  # floats over the page during a page change; see _begin_fade
        self.cover.withdraw()
        self.cover.overrideredirect(True)
        self.cover.transient(r)
        self.cover_image = tk.Label(self.cover, bd=0, bg=BG)
        self.cover_image.pack()

    def _hwnd(self):
        import ctypes
        return ctypes.windll.user32.GetParent(self.root.winfo_id())

    def _dark_title_bar(self):
        try:
            import ctypes
            self.root.update_idletasks()
            on = ctypes.c_int(1)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(self._hwnd(), 20, ctypes.byref(on), ctypes.sizeof(on))
        except Exception as e:  # older Windows: the title bar just stays light
            print("Dark title bar unavailable:", e)

    def _window_in_front(self):
        import ctypes
        return ctypes.windll.user32.GetForegroundWindow() == self._hwnd()

    def _begin_fade(self):
        """Covers the old page with a picture of itself so the new page gets built out of sight, then
        fades the picture away. Tk can't fade its own widgets, but Windows can fade a whole window."""
        if self.fading or not self.animate or not self.body.winfo_children() or not self._window_in_front():
            return
        root, body = self.root, self.body
        x, y, w, h = body.winfo_rootx(), body.winfo_rooty(), body.winfo_width(), body.winfo_height()
        try:
            frame = _print_window(self._hwnd(), root.winfo_width(), root.winfo_height())
            if is_black(frame):
                return
            left, top = x - root.winfo_rootx(), y - root.winfo_rooty()
            self.cover_photo = ImageTk.PhotoImage(Image.fromarray(frame[top:top + h, left:left + w, ::-1]))
        except Exception as e:  # never let a visual effect get in the way of changing page
            print("Page fade skipped:", e)
            return
        self.fading = True
        self.cover_image.configure(image=self.cover_photo)
        self.cover.geometry(f"{w}x{h}+{x}+{y}")
        self.cover.attributes("-alpha", 0)
        self.cover.deiconify()
        self.cover.update()  # let it draw while still invisible, so it can't flash either
        self.cover.attributes("-alpha", 1)
        self.root.after(30, self._fade_cover)  # by now the new page exists and has drawn underneath

    def _fade_cover(self):
        start = time.perf_counter()

        def step():
            t = (time.perf_counter() - start) / FADE_SECONDS
            if t < 1:
                self.cover.attributes("-alpha", 1 - smooth(t))
                self.root.after(8, step)
                return
            self.cover.withdraw()
            self.cover_image.configure(image="")
            self.cover_photo, self.fading = None, False

        step()

    def _flat_button(self, parent, text, command, selected=False, bg=SIDEBAR, selected_bg=SELECTED):
        b = tk.Button(parent, text=text, command=command, bg=selected_bg if selected else bg,
                      fg=TEXT if selected else MUTED, activebackground=selected_bg if selected else bg,
                      activeforeground=TEXT, relief="flat", bd=0, highlightthickness=0, cursor="hand2",
                      font=(FONT, 11), anchor="w", padx=14, pady=5)
        b.selected = selected
        b.bind("<Enter>", lambda _: b.configure(fg=TEXT))
        b.bind("<Leave>", lambda _: b.configure(fg=TEXT if b.selected else MUTED))
        return b

    def _show_nav(self, current):
        """Highlights the current top bar link. Setup pages pass nothing, so the wizard can't be left halfway."""
        self.page_nav = current
        if current:
            self.nav.pack(side="left", fill="both", expand=True)
        else:
            self.nav.pack_forget()
        for key, link in self.nav_links.items():
            link.selected = key == current
            link.configure(fg=TEXT if link.selected else MUTED)

    def _tick(self):
        if self.page_tick:
            try:
                self.page_tick()
            except tk.TclError:
                pass
        self._update_tick()
        self.root.after(300, self._tick)

    # updates ----------------------------------------------------------------
    def _check_for_update(self):
        self.update_check, self.update_status = "checking", None

        def work():
            started = time.perf_counter()
            try:
                UPDATE_FILE.unlink(missing_ok=True)  # left over from the last update
            except OSError:
                pass
            update, result = self.update, "done"
            try:
                update = updater.find_update()
            except Exception as e:  # offline, or GitHub is down
                print("Update check failed:", e)
                result = "error"
            time.sleep(max(0.0, 0.6 - (time.perf_counter() - started)))  # long enough to see "Checking..."
            self.update, self.update_check = update, result

        threading.Thread(target=work, daemon=True).start()

    def _update_text(self):
        if self.update_status:
            return self.update_status
        if self.update:
            return f"Version {self.update['version']} is available"
        return {"checking": "Checking...", "error": "Couldn't check. Try again later."}.get(
            self.update_check, "You're on the latest version")

    def _update_tick(self):
        """Shows what the background update work left behind (only this thread can touch the window)."""
        if self.update and not self.update_dismissed and self.page_nav == "home" and not self.update_banner:
            self._show_update_banner()

    def _show_update_banner(self):
        """Slides down at the top of Home. Part of the page, so it goes away when the page changes."""
        full_height = 38
        banner = self.update_banner = tk.Frame(self.body, bg=NOTICE, height=1)
        banner.pack_propagate(False)
        banner.pack(fill="x", before=self.body.winfo_children()[0])

        def clickable(text, command, **options):  # labels, as a new Tk button would draw white for a moment
            label = tk.Label(banner, text=text, bg=NOTICE, fg="#111", cursor="hand2", takefocus=1, **options)
            label.bind("<Button-1>", lambda _: command())
            label.bind("<space>", lambda _: command())
            label.bind("<Enter>", lambda _: label.configure(bg=NOTICE_HOVER))
            label.bind("<Leave>", lambda _: label.configure(bg=NOTICE))
            return label

        clickable("✕", self._dismiss_update_banner, font=(FONT, 11), padx=14).pack(side="right", fill="y")
        clickable("New update available. Click to update.", lambda: self.page_settings("Updates"),
                  font=(FONT_SEMI, 11), anchor="w", padx=28).pack(side="left", fill="both", expand=True)

        def slide(height=1):
            if banner.winfo_exists():
                banner.configure(height=height)
                if height < full_height:
                    self.root.after(12, slide, min(height + 4, full_height))

        slide()

    def _dismiss_update_banner(self):
        self.update_dismissed = True  # Settings > Updates still offers it
        self.update_banner.destroy()
        self.update_banner = None

    def _start_update(self):
        self.updating = True
        self.update_status = "Downloading update..."

        def progress(fraction):
            self.update_status = f"Downloading update... {fraction:.0%}"

        def work():
            try:
                updater.download(self.update, UPDATE_FILE, progress)
                self.update_status = "Installing update..."
                updater.install(UPDATE_FILE)
            except Exception as e:
                print("Update failed:", e)
                self.update_status = "Update failed. Check your internet and try again."
                self.updating = False
                return
            os._exit(0)  # the installer replaces these files and reopens the app

        threading.Thread(target=work, daemon=True).start()

    def _save(self, **kw):
        self.settings.update(kw)
        save_settings(self.settings)

    def _use_saved_source(self):
        src, name = self.settings.get("source"), self.settings.get("source_name")
        if src == SCREEN:
            self.capture.set_source(SCREEN)
            return
        if src == WINDOW:
            self.capture.set_source((WINDOW, name, self.settings.get("source_exe") or ""))
            return
        for i, n in self.devices:  # match by name: the number can change after a reboot
            if n == name:
                self.capture.set_source(i)
                return
        if isinstance(src, int):
            self.capture.set_source(src)

    def _overlay_connected(self):
        """LIVE Studio has loaded the overlay since this app started (or since the setup page opened)."""
        return self.state.overlay_seen > 0

    # page building helpers --------------------------------------------------
    def _page(self, step=None, title="", text="", nav=None, bare=False):
        """Clear the window and start a new page. Returns the content frame (bare: the whole page, unpadded)."""
        self.page_tick = None
        self.images = []
        if self.state.data.get("preview"):  # leaving "Customise box": stop the sample on stream
            self.state.apply({"preview": False})
        self._begin_fade()
        for w in self.body.winfo_children():
            w.destroy()
        self.update_banner = None
        self._show_nav(nav)
        if bare:
            page = tk.Frame(self.body, bg=BG)
            page.pack(fill="both", expand=True)
            return page
        if step is not None:
            bar = tk.Frame(self.body, bg=BG)
            bar.pack(fill="x", padx=28, pady=(20, 0))
            for i, name in enumerate(STEPS):
                color = ACCENT if i == step else (GOOD if i < step else "#555")
                mark = "✓" if i < step else str(i + 1)
                tk.Label(bar, text=f"{mark}  {name}", bg=BG, fg=color,
                         font=(FONT, 10, "bold" if i == step else "normal")).pack(side="left", padx=(0, 18))
        tk.Label(self.body, text=title, bg=BG, fg=TEXT, font=(FONT_SEMI, 16), anchor="w",
                 justify="left", wraplength=WIDTH - 56).pack(fill="x", padx=28, pady=(18, 4))
        if text:
            tk.Label(self.body, text=text, bg=BG, fg=MUTED, font=(FONT, 11), anchor="w", justify="left",
                     wraplength=WIDTH - 56).pack(fill="x", padx=28)
        content = tk.Frame(self.body, bg=BG)
        content.pack(fill="both", expand=True, padx=28, pady=14)
        self.buttons = tk.Frame(self.body, bg=BG)
        self.buttons.pack(fill="x", padx=28, pady=(0, 22), side="bottom")
        return content

    def _button(self, text, command, primary=True, side="right"):
        b = ttk.Button(self.buttons, text=text, command=command,
                       style="Big.TButton" if primary else "TButton")
        b.pack(side=side, padx=(10, 0) if side == "right" else (0, 10))
        return b

    def _img(self, parent, photo, **pack):
        self.images.append(photo)
        lbl = tk.Label(parent, image=photo, bg=BG)
        lbl.pack(**pack)
        return lbl

    def _status(self, parent, text="", color=MUTED, size=12):
        lbl = tk.Label(parent, text=text, bg=BG, fg=color, font=(FONT, size), justify="left",
                       anchor="w", wraplength=WIDTH - 56)
        lbl.pack(fill="x", pady=(10, 0))
        return lbl

    def _example(self, name):
        try:
            return Image.open(core.BASE / name)
        except Exception:
            return None

    # ------------------------------------------------------------ wizard ---
    def page_welcome(self):
        c = self._page(None, "Let's set up your Room ID box",
                       "This takes about 2 minutes. Once it's done, your Room ID shows on stream "
                       "by itself every time you make a new room.")
        tk.Label(c, text="Before you start:", bg=BG, fg=TEXT, font=(FONT, 13, "bold"),
                 anchor="w").pack(fill="x", pady=(10, 6))
        for line in ("1.  Turn on your phone and open PUBG Mobile",
                     "2.  Open TikTok LIVE Studio (you don't need to be live)",
                     "3.  Make sure your Elgato is plugged in"):
            tk.Label(c, text=line, bg=BG, fg=TEXT, font=(FONT, 12), anchor="w").pack(fill="x", pady=3)
        self._button("Start", self.page_game_intro)

    def _sources(self):
        """Every source the game could come from, in dropdown order: [(src, label), ...]."""
        devs = sorted(self.devices, key=lambda d: not looks_like_capture_card(d[1]))
        wins = sorted(list_windows(), key=lambda w: not is_live_studio(*w))  # LIVE Studio first
        out = [((WINDOW, t, e), f"Window: {t}" + ("  (TikTok LIVE Studio)" if is_live_studio(t, e) else ""))
               for t, e in wins]
        out += [(i, f"Capture device: {n}") for i, n in devs]
        out.append((SCREEN, "Whole screen (use only if nothing else works)"))
        return out

    @staticmethod
    def _best_source(sources):
        """Auto-pick: TikTok LIVE Studio, else a capture card, else a phone mirroring app, else the first."""
        tests = (lambda s: isinstance(s[0], tuple) and is_live_studio(s[0][1], s[0][2]),
                 lambda s: isinstance(s[0], int) and looks_like_capture_card(s[1].split(": ", 1)[-1]),
                 lambda s: s[0] != SCREEN and any(h in s[1].lower() for h in MIRROR_HINTS))
        for test in tests:
            for i, s in enumerate(sources):
                if test(s):
                    return i
        return 0

    def page_game_intro(self):
        self.page_game()

    def page_game(self, keep=None):
        """Pick the game source from a dropdown. TikTok LIVE Studio is chosen automatically when open.
        keep: a source to stay on after Refresh (if it still exists)."""
        sources = self._sources()
        idx = next((i for i, s in enumerate(sources) if s[0] == keep), None)
        auto = idx is None
        if auto:
            idx = self._best_source(sources)
        auto_ttls = auto and isinstance(sources[idx][0], tuple) and is_live_studio(*sources[idx][0][1:])

        c = self._page(0, "Where's your game picture?",
                       "We picked the best match. If it isn't your game, choose another source from the list.")
        row = tk.Frame(c, bg=BG)
        row.pack(fill="x")
        cur = {"src": None, "name": "", "started": 0.0}
        box = ttk.Combobox(row, state="readonly", font=(FONT, 11), values=[label for _, label in sources])
        box.pack(side="left", fill="x", expand=True, ipady=3)
        ttk.Button(row, text="Refresh", command=lambda: self.page_game(cur["src"])).pack(side="left", padx=(8, 0))
        hint = tk.Label(c, text="", bg=BG, fg=MUTED, font=(FONT, 10), anchor="w", justify="left",
                        wraplength=WIDTH - 56)
        hint.pack(fill="x", pady=(4, 0))
        preview = tk.Label(c, bg="#000", width=60, height=14)
        preview.pack(pady=(6, 0))
        status = self._status(c, "Loading the picture...")
        yes = self._button("Use this source", lambda: self._pick_game(cur["src"], cur["name"]))
        self._button("Back", self.page_welcome, primary=False, side="left")

        def select(i):
            src, label = sources[i]
            name = label.split(": ", 1)[1] if isinstance(src, int) else label
            cur.update(src=src, name=name, started=time.time())
            self.capture.set_source(src)
            yes.state(["disabled"])
            status.configure(fg=MUTED, text="Loading the picture...")
            if auto_ttls and i == idx:
                hint.configure(fg=GOOD, text="✓  Found TikTok LIVE Studio and selected it for you. "
                                             "The Room ID is read from its stream preview.")
            elif isinstance(src, tuple):
                hint.configure(fg=MUTED, text="Keep this window open. It can be behind others, "
                                              "but not minimised.")
            else:
                hint.configure(text="")

        box.bind("<<ComboboxSelected>>", lambda _: select(box.current()))
        box.current(idx)
        select(idx)

        def tick():
            f = self.capture.get()
            if f is not None:
                p = to_photo(f, WIDTH - 56, 300)
                self.images[:] = [p]
                preview.configure(image=p, width=p.width(), height=p.height())
            waited = time.time() - cur["started"]
            if f is not None and not is_black(f):
                yes.state(["!disabled"])
                status.configure(fg=MUTED, text="If you can see your PUBG game, click  Use this source.")
            elif f is not None and waited > 3:
                yes.state(["!disabled"])
                status.configure(fg=BAD, text="The picture is black. Is PUBG showing on your phone? "
                                              "If it stays black, pick another source from the list.")
            elif cur["src"] in self.capture.failed:
                status.configure(fg=BAD, text="This source can't be opened. Pick another from the list.")
            elif waited > 8:
                status.configure(fg=BAD, text="No picture from this source. Pick another from the list.")
        self.page_tick = tick

    def _pick_game(self, src, name):
        self.capture.set_source(src)
        if isinstance(src, tuple):  # a window: remember its title and program
            self._save(source=WINDOW, source_name=src[1], source_exe=src[2])
        else:
            self._save(source=src, source_name=None if src == SCREEN else name, source_exe=None)
        self.page_room()

    def page_room(self):
        c = self._page(1, "Open a custom room in PUBG",
                       "Make a custom room in the game so the room screen with the teams is showing "
                       "(like the picture below). Then click Next.")
        ex = self._example("example_room.png")
        if ex:
            self._img(c, to_photo(ex, WIDTH - 56, 360), pady=(4, 0))
            self._status(c, "The red box is the Room ID we'll read.", size=11)
        self._button("Next", self.page_find)
        self._button("Back", self.page_game_intro, primary=False, side="left")

    def page_find(self):
        c = self._page(2, "Looking for your Room ID...",
                       "Keep the custom room screen open on your phone.")
        status = self._status(c, "Searching the picture. This takes a few seconds.")
        result = {}

        def work():
            time.sleep(0.5)
            f = self.capture.get()
            if f is None or is_black(f):
                result["err"] = "black"
                return
            result["frame"] = f.copy()
            try:
                result["region"] = self.scanner.find_room_id(f)
            except Exception as e:
                result["err"] = str(e)
            result["done"] = True

        threading.Thread(target=work, daemon=True).start()
        dots = [0]

        def tick():
            if result.get("err") == "black":
                self.page_tick = None
                self.page_find_failed("We can't see the game right now. Is the custom room open on your phone?")
            elif result.get("done"):
                self.page_tick = None
                if result.get("region"):
                    self._save(region=result["region"])
                    self.page_confirm()
                else:
                    self.page_find_failed("We couldn't spot the Room ID by ourselves.", result.get("frame"))
            else:
                dots[0] = (dots[0] + 1) % 4
                status.configure(text="Searching the picture" + "." * (dots[0] + 1))
        self.page_tick = tick

    def page_find_failed(self, why, frame=None):
        c = self._page(2, why, "Make sure the custom room screen is showing, then try again. "
                               "Or show us where it is yourself.")
        ex = self._example("example_roomid.png")
        if ex:
            self._status(c, "It looks like this, at the top of the room screen:", TEXT)
            self._img(c, to_photo(ex, 330, 90), pady=(8, 0), anchor="w")
        self._button("Try again", self.page_find)
        self._button("I'll show you where it is", self.page_manual, primary=False)
        self._button("Back", self.page_room, primary=False, side="left")

    def page_manual(self):
        frame = self.capture.get()
        if is_black(frame):
            return self.page_find_failed("We can't see the game right now.")
        frame = frame.copy()
        c = self._page(2, "Draw a box around the Room ID",
                       "Click and drag over the  Room ID: 1234567  text at the top of the room screen.")
        H, W = frame.shape[:2]
        photo = to_photo(frame, WIDTH - 56, 400)
        pw, ph = photo.width(), photo.height()
        self.images.append(photo)
        cv = tk.Canvas(c, width=pw, height=ph, highlightthickness=0, cursor="cross", bg="#000")
        cv.pack()
        cv.create_image(0, 0, image=photo, anchor="nw")
        hint = self._status(c, "Tip: draw the box a little bigger than the text. We'll check it on the next screen.",
                            size=11)
        sel = {}
        save = self._button("Use this box", lambda: done())
        save.state(["disabled"])
        self._button("New picture", self.page_manual, primary=False)
        self._button("Back", self.page_find_failed_back, primary=False, side="left")

        def down(e):
            sel.update(x0=e.x, y0=e.y, x1=e.x, y1=e.y)
            if "r" in sel:
                cv.delete(sel["r"])
            sel["r"] = cv.create_rectangle(e.x, e.y, e.x, e.y, outline=ACCENT, width=3)

        def drag(e):
            if "r" in sel:
                sel.update(x1=min(max(e.x, 0), pw), y1=min(max(e.y, 0), ph))
                cv.coords(sel["r"], sel["x0"], sel["y0"], sel["x1"], sel["y1"])
                ok = abs(sel["x1"] - sel["x0"]) > 8 and abs(sel["y1"] - sel["y0"]) > 4
                save.state(["!disabled"] if ok else ["disabled"])

        def done():
            x0, x1 = sorted((sel["x0"], sel["x1"]))
            y0, y1 = sorted((sel["y0"], sel["y1"]))
            self._save(region=[x0 / pw, y0 / ph, (x1 - x0) / pw, (y1 - y0) / ph])
            self.page_confirm()

        cv.bind("<ButtonPress-1>", down)
        cv.bind("<B1-Motion>", drag)

    def page_find_failed_back(self):
        self.page_find_failed("Let's try that again.")

    def page_confirm(self):
        c = self._page(2, "Is this your Room ID?",
                       "This is the part of the game we'll keep an eye on.")
        view = tk.Label(c, bg=BG)
        view.pack(anchor="w", pady=(6, 0))
        reading = tk.Label(c, text="Reading...", bg=BG, fg=MUTED, font=(FONT, 16, "bold"), anchor="w")
        reading.pack(fill="x", pady=(14, 0))
        yes = self._button("Yes, that's right", self.page_password)
        self._button("No, let me pick it", self.page_manual, primary=False)
        self._button("Back", self.page_room, primary=False, side="left")
        self.scanner.last_text = None

        def tick():
            cr = crop(self.capture.get(), self.settings.get("region"))
            if cr is not None:
                p = to_photo(cr, WIDTH - 56, 130)
                self.images[:] = [p]
                view.configure(image=p)
            if self.scanner.last_text:
                reading.configure(fg=GOOD, text=f"We can read:  {self.scanner.last_text}")
            else:
                reading.configure(fg=MUTED, text="Reading...  (keep the room screen open)")
        self.page_tick = tick

    def page_password(self):
        c = self._page(3, "What's your room password?",
                       "The game doesn't show the password, so type it here. It stays the same for "
                       "every room until you change it (you can change it any time).")
        var = tk.StringVar(value=self.settings.get("password", ""))
        e = tk.Entry(c, textvariable=var, font=(FONT, 22), width=12, bg=FIELD, fg=TEXT,
                     insertbackground=TEXT, relief="flat", justify="center")
        e.pack(anchor="w", pady=(10, 0), ipady=8)
        e.focus_set()

        def nxt():
            pw = var.get().strip()
            self._save(password=pw)
            self.state.apply({"manual_password": pw})
            self.page_ttls()

        e.bind("<Return>", lambda _: nxt())
        self._button("Next", nxt)
        self._button("Back", self.page_confirm, primary=False, side="left")

    def _copy_link(self, button):
        self.root.clipboard_clear()
        self.root.clipboard_append(self.overlay_url)
        button.configure(text="Copied!")

        def restore():
            if button.winfo_exists():  # the page may have changed since
                button.configure(text="Copy link")

        self.root.after(1500, restore)

    def page_ttls(self):
        c = self._page(4, "Add the box to TikTok LIVE Studio",
                       "You only do this once. Follow these steps in LIVE Studio:")
        steps = [
            "Click  Copy link  below.",
            "In LIVE Studio, click  Add source  (bottom left).",
            "Choose  Link.",
            "Paste the link (Ctrl + V) and click  Add source.",
            "Drag the box to where you want it on your stream.",
        ]
        for i, s in enumerate(steps, 1):
            row = tk.Frame(c, bg=BG)
            row.pack(fill="x", pady=4)
            tk.Label(row, text=str(i), bg=ACCENT, fg="#111", font=(FONT, 11, "bold"), width=2).pack(side="left")
            tk.Label(row, text="  " + s, bg=BG, fg=TEXT, font=(FONT, 12), anchor="w").pack(side="left")
        copy_row = tk.Frame(c, bg=BG)
        copy_row.pack(fill="x", pady=(14, 0))
        link = tk.Entry(copy_row, font=(FONT, 10), width=44, bg=FIELD, fg=MUTED,
                        readonlybackground=FIELD, relief="flat")
        link.insert(0, self.overlay_url)
        link.configure(state="readonly")
        link.pack(side="left", ipady=6)
        copy_btn = ttk.Button(copy_row, text="Copy link", style="Big.TButton",
                              command=lambda: self._copy_link(copy_btn))
        copy_btn.pack(side="left", padx=10)
        status = self._status(c, "", size=13)
        tip = self._status(c, "Tip: you can hide your old ID / PW text in LIVE Studio now.", size=11)
        nxt = self._button("Next", self.page_done)
        self._button("Back", self.page_password, primary=False, side="left")
        self.state.overlay_seen = 0

        def tick():
            if self._overlay_connected():
                status.configure(fg=GOOD, text="✓  LIVE Studio is connected! You should see the box there now.")
            else:
                status.configure(fg=MUTED, text="Waiting for LIVE Studio...  (do the steps above)")
        self.page_tick = tick

    def page_done(self):
        self._save(setup_done=True)
        c = self._page(None, "You're all set!",
                       "From now on, just open Room ID Overlay before you go live and leave it open. "
                       "When you make a new room, the Room ID updates on stream by itself.")
        self._status(c, "There's a Room ID Overlay shortcut on your desktop.", TEXT)
        self._button("Finish", self.page_dashboard)

    # --------------------------------------------------------- dashboard ---
    def page_dashboard(self):
        c = self._page(None, "Room ID Overlay is running",
                       "Leave this open while you stream. You can minimise it.", nav="home")
        panel = tk.Frame(c, bg=PANEL, padx=18, pady=16)
        panel.pack(fill="x")
        live_label = tk.Label(panel, text="On stream now", bg=PANEL, fg=MUTED, font=(FONT, 11))
        live_label.pack(anchor="w")
        live = tk.Label(panel, text="", bg=PANEL, fg=TEXT, font=(FONT, 26, "bold"), anchor="w")
        live.pack(fill="x")
        checks = tk.Label(panel, text="", bg=PANEL, fg=MUTED, font=(FONT, 11), justify="left", anchor="w")
        checks.pack(fill="x", pady=(8, 0))
        watch_row = tk.Frame(panel, bg=PANEL)  # the part of the game being read, so problems are easy to spot
        watch_row.pack(fill="x", pady=(10, 0))
        tk.Label(watch_row, text="Watching:", bg=PANEL, fg=MUTED, font=(FONT, 10)).pack(side="left", anchor="n")
        watch = tk.Label(watch_row, bg=PANEL)
        watch.pack(side="left", padx=(8, 0))
        reading = tk.Label(watch_row, text="", bg=PANEL, fg=MUTED, font=(FONT, 10), anchor="w")
        reading.pack(side="left", padx=(10, 0))

        row = tk.Frame(c, bg=BG)
        row.pack(fill="x", pady=(18, 0))
        tk.Label(row, text="Password", bg=BG, fg=TEXT, font=(FONT, 12), width=9, anchor="w").pack(side="left")
        pw_var = tk.StringVar(value=self.settings.get("password", ""))
        pw = tk.Entry(row, textvariable=pw_var, font=(FONT, 14), width=12, bg=FIELD, fg=TEXT,
                      insertbackground=TEXT, relief="flat")
        pw.pack(side="left", ipady=5)

        def save_pw():
            v = pw_var.get().strip()
            self._save(password=v)
            self.state.apply({"manual_password": v})
            saved.configure(text="Saved")
            self.root.after(1500, lambda: saved.configure(text=""))

        pw.bind("<Return>", lambda _: save_pw())
        ttk.Button(row, text="Save", command=save_pw).pack(side="left", padx=8)
        saved = tk.Label(row, text="", bg=BG, fg=GOOD, font=(FONT, 11))
        saved.pack(side="left")

        row2 = tk.Frame(c, bg=BG)
        row2.pack(fill="x", pady=(12, 0))
        tk.Label(row2, text="Wrong ID?", bg=BG, fg=TEXT, font=(FONT, 12), width=9, anchor="w").pack(side="left")
        rid_var = tk.StringVar()
        rid = tk.Entry(row2, textvariable=rid_var, font=(FONT, 14), width=12, bg=FIELD, fg=TEXT,
                       insertbackground=TEXT, relief="flat")
        rid.pack(side="left", ipady=5)
        set_rid = lambda: self.state.apply({"manual_room_id": rid_var.get().strip()})
        rid.bind("<Return>", lambda _: set_rid())
        ttk.Button(row2, text="Use this", command=set_rid).pack(side="left", padx=8)
        tk.Label(c, text="Only if the ID on stream is wrong. A new room replaces it automatically.",
                 bg=BG, fg=MUTED, font=(FONT, 10), anchor="w").pack(fill="x", pady=(4, 0))

        def tick():
            s = self.state.snapshot()
            if s["on_stream"]:
                pwtxt = f"    PW {s['display_password']}" if s["display_password"] else ""
                live.configure(text=f"ID {s['display_room_id']}{pwtxt}", fg=TEXT)
                live_label.configure(text="On stream now")
            else:
                live.configure(text="Hidden", fg=MUTED)
                live_label.configure(text="On stream now (shows when a custom room is open)"
                                     if s["visible"] else "On stream now (turned off in Settings)")
            f = self.capture.get()
            cr = crop(f, self.settings.get("region"))
            if cr is not None:
                p = to_photo(cr, 260, 60)
                self.images[:] = [p]
                watch.configure(image=p)
            reading.configure(text=f"Reads: {self.scanner.last_text}" if self.scanner.last_text
                              else "Reads: no Room ID")
            game = ("✓  Game picture OK", GOOD) if not is_black(f) else ("✗  No game picture: is PUBG on?", BAD)
            ttls = ("✓  Sending to your stream", GOOD) if not self.relay.error else \
                ("✗  Can't reach the internet. Check your connection.", BAD)
            err = f"\n!  {self.scanner.error}" if self.scanner.error else ""
            bad = err or game[1] == BAD or ttls[1] == BAD
            checks.configure(text=f"{game[0]}\n{ttls[0]}{err}", fg=BAD if bad else GOOD)
        self.page_tick = tick

    # ---------------------------------------------------------- settings ---
    def page_settings(self, section="General"):
        page = self._page(nav="settings", bare=True)
        side = tk.Frame(page, bg=SIDEBAR, width=180)
        side.pack(side="left", fill="y")
        side.pack_propagate(False)
        tk.Label(side, text="Settings", bg=SIDEBAR, fg=TEXT, font=(FONT_SEMI, 13), anchor="w").pack(
            fill="x", padx=18, pady=(18, 10))
        sections = {"General": self._settings_general, "Updates": self._settings_updates, "Setup": self._settings_setup}
        for name in sections:
            self._flat_button(side, name, lambda n=name: self.page_settings(n), selected=name == section).pack(
                fill="x", padx=10, pady=1)

        main = tk.Frame(page, bg=BG)
        main.pack(side="left", fill="both", expand=True, padx=22, pady=(20, 14))
        tk.Label(main, text="Changes are saved automatically.", bg=BG, fg=MUTED, font=(FONT, 9), anchor="w").pack(
            side="bottom", fill="x")
        tk.Frame(main, bg=DIVIDER, height=1).pack(side="bottom", fill="x", pady=(0, 8))
        rows = tk.Frame(main, bg=BG)
        rows.pack(fill="x")
        rows.columnconfigure(0, minsize=210)
        sections[section](rows)

    def _setting_row(self, parent, label):
        """Adds the row's label and returns its row number, for placing the control in column 1."""
        row = parent.grid_size()[1]
        tk.Label(parent, text=label, bg=BG, fg=TEXT if label else BG, font=(FONT, 10), anchor="w").grid(
            row=row, column=0, sticky="w", pady=9)
        return row

    def _setting_text(self, parent, row, text=""):
        lbl = tk.Label(parent, text=text, bg=BG, fg=MUTED, font=(FONT, 10), anchor="w", justify="left", wraplength=190)
        lbl.grid(row=row, column=1, sticky="w")
        return lbl

    def _switch(self, parent, row, get, put):
        frame = target = SWITCH_FRAMES if get() else 0
        running = False
        lbl = tk.Label(parent, bg=BG, cursor="hand2", takefocus=1, image=switch_image(frame))

        def step():
            nonlocal frame, running
            if not lbl.winfo_exists():
                return
            if frame == target:
                running = False
                return
            frame += 1 if target > frame else -1
            lbl.configure(image=switch_image(frame))
            self.root.after(16, step)

        def flip(_=None):
            nonlocal frame, target, running
            put(not get())
            target = SWITCH_FRAMES if get() else 0  # read back: the change may have failed
            if not self.animate:
                frame = target
                lbl.configure(image=switch_image(frame))
            elif not running:  # if it's already moving, it simply heads for the new target
                running = True
                step()

        lbl.bind("<Button-1>", flip)
        lbl.bind("<space>", flip)
        lbl.grid(row=row, column=1, sticky="w")

    def _settings_general(self, p):
        def put_auto_open(on):
            try:
                watcher.set_enabled(on)
            except OSError as e:
                messagebox.showerror(APP_NAME, f"Couldn't change that setting.\n\n{e}")

        def put_visible(on):
            self._save(visible=on)
            self.state.apply({"visible": on})

        self._switch(p, self._setting_row(p, "Open with TikTok LIVE Studio"), watcher.is_enabled, put_auto_open)
        self._switch(p, self._setting_row(p, "Show box when a room is open"),
                     lambda: self.settings.get("visible", True), put_visible)

    @staticmethod
    def _set_enabled(button, on):
        """Only touches the button when this is a real change: setting a ttk button's state clears its hover."""
        want = "normal" if on else "disabled"
        if str(button.cget("state")) == want:
            return
        button.configure(state=want)
        if on and button.winfo_containing(*button.winfo_pointerxy()) == button:
            button.state(["active"])  # the mouse was already on it, so no "mouse entered" event will come

    def _settings_updates(self, p):
        self.update_dismissed = True  # they've seen it now: no need for the banner on Home as well
        try:
            version = updater.current_version()
        except OSError:
            version = "Unknown"
        self._setting_text(p, self._setting_row(p, "Installed version"), version)
        status = self._setting_text(p, self._setting_row(p, "Status"))
        def run(action):  # shows the result of the click now, not at the next refresh
            action()
            tick()

        check = ttk.Button(p, text="Check for updates", command=lambda: run(self._check_for_update))
        check.grid(row=self._setting_row(p, ""), column=1, sticky="w", pady=(6, 0))
        now = ttk.Button(p, text="Update now", command=lambda: run(self._start_update), style="Big.TButton")
        now_row = self._setting_row(p, "")
        now.grid(row=now_row, column=1, sticky="w")

        def tick():
            status.configure(text=self._update_text())
            self._set_enabled(check, not self.updating and self.update_check != "checking")
            self._set_enabled(now, not self.updating)
            if bool(self.update) != bool(now.winfo_manager()):
                if self.update:
                    now.grid()
                else:
                    now.grid_remove()

        tick()
        self.page_tick = tick

    def _settings_setup(self, p):
        source = "Your whole screen" if self.settings.get("source") == SCREEN else self.settings.get("source_name")
        self._setting_text(p, self._setting_row(p, "Game source"), source or "Not set")
        copy = ttk.Button(p, text="Copy link", command=lambda: self._copy_link(copy))
        copy.grid(row=self._setting_row(p, "LIVE Studio link"), column=1, sticky="w")
        ttk.Button(p, text="Run setup again", command=self._restart_setup).grid(
            row=self._setting_row(p, "Change game or Room ID area"), column=1, sticky="w")

    # ------------------------------------------------------- box style ---
    # Every setting on the "Customise box" page: tab -> [(setting, label, kind, options)]
    STYLE_TABS = [
        ("Text", [
            ("font", "Font", "font", None),
            ("size", "Text size", "slider", (16, 240)),
            ("weight", "Thickness", "weight", {300: "Light", 400: "Regular", 500: "Medium", 600: "Semibold",
                                               700: "Bold", 800: "Extra bold", 900: "Black"}),
            ("italic", "Italic", "check", None),
            ("text_color", "Number colour", "color", None),
            ("letter_spacing", "Letter spacing", "slider", (-10, 60)),
            ("align", "Number alignment", "choice", {"right": "Right", "center": "Centre", "left": "Left"}),
        ]),
        ("Labels", [
            ("show_labels", "Show labels", "check", None),
            ("id_label", "Room ID label", "text", None),
            ("pw_label", "Password label", "text", None),
            ("label_color", "Label colour", "color", None),
            ("label_size", "Label size (%)", "slider", (20, 200)),
        ]),
        ("Box", [
            ("box_color", "Box colour", "color", None),
            ("box_opacity", "Box opacity (%)", "slider", (0, 100)),
            ("gradient", "Gradient fill", "check", None),
            ("box_color2", "Gradient 2nd colour", "color", None),
            ("gradient_angle", "Gradient angle", "slider", (0, 360)),
            ("border_width", "Border thickness", "slider", (0, 30)),
            ("border_color", "Border colour", "color", None),
        ]),
        ("Shape", [
            ("shape", "Shape", "choice", {"rounded": "Rounded", "pill": "Pill", "square": "Square",
                                          "cut": "Cut corners", "slant": "Slanted"}),
            ("radius", "Corner size", "slider", (0, 120)),
            ("layout", "Layout", "choice", {"stacked": "ID above PW", "row": "All on one line"}),
        ]),
        ("Spacing", [
            ("pad_x", "Side padding", "slider", (0, 300)),
            ("pad_y", "Top / bottom padding", "slider", (0, 200)),
            ("col_gap", "Gap: label to number", "slider", (0, 400)),
            ("row_gap", "Gap between rows", "slider", (0, 150)),
            ("line_height", "Line height", "slider", (60, 250)),
        ]),
        ("Text effects", [
            ("shadow", "Text shadow", "check", None),
            ("shadow_color", "Shadow colour", "color", None),
            ("shadow_opacity", "Shadow strength (%)", "slider", (0, 100)),
            ("shadow_blur", "Shadow blur", "slider", (0, 60)),
            ("shadow_y", "Shadow drop", "slider", (-30, 30)),
            ("outline_width", "Text outline", "slider", (0, 20)),
            ("outline_color", "Outline colour", "color", None),
        ]),
        ("Box effects", [
            ("glow_size", "Glow size", "slider", (0, 80)),
            ("glow_color", "Glow colour", "color", None),
            ("anim", "Appear animation", "choice", {"zoom": "Zoom", "fade": "Fade", "slide": "Slide down",
                                                    "pop": "Pop", "none": "None"}),
            ("flash", "Flash on new room", "check", None),
            ("flash_color", "Flash colour", "color", None),
        ]),
    ]

    def page_style(self):
        """Change how the Room ID box looks on stream, with a live preview."""
        from tkinter import colorchooser
        from tkinter import font as tkfont

        c = self._page(None, "Customise your Room ID box",
                       "Changes go on stream straight away, with a sample box shown while this page is open.",
                       nav="style")
        self.state.apply({"preview": True})
        st = {**DEFAULT_STYLE, **self.settings.get("style", {})}
        pw_, ph_ = WIDTH - 56, 170
        preview = tk.Label(c, bg="#000", width=pw_, height=ph_)
        preview.pack()
        yy, xx = np.indices((ph_, pw_))
        checker = np.empty((ph_, pw_, 4), np.uint8)  # "stream" behind the box, so see-through parts show
        checker[:] = (58, 63, 75, 255)
        checker[(yy // 14 + xx // 14) % 2 == 1, :3] = (66, 71, 84)
        jobs = {}

        def later(name, ms, fn):  # run fn once things stop changing for a moment
            if jobs.get(name):
                self.root.after_cancel(jobs[name])
            jobs[name] = self.root.after(ms, fn)

        def draw():
            jobs["draw"] = None
            if not preview.winfo_exists():
                return
            k = min(1.0, 90 / st["size"])  # big text: draw a scaled-down copy so sliders stay smooth
            small = {**st, **{key: st[key] * k for key in ("size", "radius", "border_width", "glow_size",
                                                           "shadow_blur", "shadow_y", "outline_width")}}
            small["size"] = max(6, round(small["size"]))
            try:
                img = render_box(small)
            except Exception as e:  # a bad font file etc. must never break the page
                print("Preview failed:", e)
                return
            bg = Image.fromarray(checker, "RGBA")
            scale = min(1.0, (pw_ - 10) / img.width, (ph_ - 10) / img.height)
            if scale < 1:
                img = img.resize((max(1, int(img.width * scale)), max(1, int(img.height * scale))), Image.LANCZOS)
            bg.alpha_composite(img, ((pw_ - img.width) // 2, (ph_ - img.height) // 2))
            photo = ImageTk.PhotoImage(bg)
            self.images[:] = [photo]
            preview.configure(image=photo, width=pw_, height=ph_)

        font_shown = st["font"]

        def changed():
            nonlocal font_shown
            if st["font"] != font_shown:  # a different font has different thicknesses to offer
                font_shown = st["font"]
                setters["weight"]()
            if not jobs.get("draw"):  # keeps redrawing during a drag, as often as the drawing can keep up
                jobs["draw"] = self.root.after(16, draw)
            self.state.apply({"style": dict(st)})
            later("save", 400, lambda: self._save(style=dict(st)))

        nb = ttk.Notebook(c, style="Style.TNotebook")
        nb.pack(fill="both", expand=True, pady=(10, 0))
        setters = {}  # setting -> function that puts a value back into its control (for Reset)
        fonts = sorted({f for f in tkfont.families(self.root) if not f.startswith("@")}, key=str.lower)

        for tab, rows in self.STYLE_TABS:
            frame = tk.Frame(nb, bg=PANEL, padx=14, pady=8)
            frame.columnconfigure(1, weight=1)
            nb.add(frame, text=tab)
            for r, (key, text, kind, opt) in enumerate(rows):
                tk.Label(frame, text=text, bg=PANEL, fg=TEXT, font=(FONT, 11), anchor="w").grid(
                    row=r, column=0, sticky="w", padx=(0, 14), pady=3)
                setters[key] = self._style_control(frame, r, key, kind, opt, st, changed, fonts, colorchooser)

        def reset():
            if not messagebox.askyesno(APP_NAME, "Put the box back to how it looked originally?", parent=self.root):
                return
            st.clear()
            st.update(DEFAULT_STYLE)
            for key, put in setters.items():
                put(st[key])
            changed()

        self._button("Done", self.page_dashboard)
        self._button("Reset to default", reset, primary=False, side="left")
        draw()

    def _style_control(self, parent, r, key, kind, opt, st, changed, fonts, colorchooser):
        """One control on the "Customise box" page. Returns a function that sets its value."""
        def store(v):
            st[key] = v
            changed()

        if kind == "weight":
            box = ttk.Combobox(parent, state="readonly", font=(FONT, 10), height=15, width=24)
            box.grid(row=r, column=1, sticky="w", pady=3)
            weights = []

            def show_weights(_=None):
                weights[:] = available_weights(st["font"])
                st["weight"] = closest_weight(st["weight"], weights)
                box.configure(values=[opt[w] for w in weights])
                box.set(opt[st["weight"]])

            show_weights()
            box.bind("<<ComboboxSelected>>", lambda _: store(weights[box.current()]))
            return show_weights
        if kind in ("font", "choice"):
            labels = fonts if kind == "font" else list(opt.values())
            values = fonts if kind == "font" else list(opt.keys())
            box = ttk.Combobox(parent, values=labels, state="readonly", font=(FONT, 10), height=15, width=24)
            box.grid(row=r, column=1, sticky="w", pady=3)

            def put(v):
                box.set(v if kind == "font" else opt.get(v, ""))
            put(st[key])
            box.bind("<<ComboboxSelected>>", lambda _: store(values[box.current()]))
            return put
        if kind == "check":
            var = tk.BooleanVar(value=st[key])
            tk.Checkbutton(parent, variable=var, command=lambda: store(var.get()), bg=PANEL, fg=TEXT,
                           selectcolor=FIELD, activebackground=PANEL, activeforeground=TEXT).grid(
                row=r, column=1, sticky="w", pady=3)
            return var.set
        if kind == "slider":
            var = tk.IntVar(value=st[key])
            tk.Scale(parent, from_=opt[0], to=opt[1], orient="horizontal", variable=var,
                     command=lambda _: store(var.get()), bg=PANEL, fg=TEXT, troughcolor=FIELD,
                     highlightthickness=0, activebackground=ACCENT, sliderrelief="flat", bd=0,
                     font=(FONT, 9), length=300, width=12).grid(row=r, column=1, sticky="w")
            return var.set
        if kind == "color":
            row = tk.Frame(parent, bg=PANEL)
            row.grid(row=r, column=1, sticky="w", pady=3)
            sw = tk.Button(row, width=4, relief="flat", cursor="hand2", bd=0)
            sw.pack(side="left", ipady=1)
            hexlbl = tk.Label(row, bg=PANEL, fg=MUTED, font=(FONT, 10))
            hexlbl.pack(side="left", padx=8)

            def put(v):
                sw.configure(bg=v, activebackground=v)
                hexlbl.configure(text=v.upper())

            def pick():
                got = colorchooser.askcolor(st[key], parent=self.root, title="Pick a colour")[1]
                if got:
                    put(got.lower())
                    store(got.lower())
            sw.configure(command=pick)
            put(st[key])
            return put
        # text
        var = tk.StringVar(value=st[key])
        tk.Entry(parent, textvariable=var, font=(FONT, 11), width=10, bg=FIELD, fg=TEXT,
                 insertbackground=TEXT, relief="flat").grid(row=r, column=1, sticky="w", pady=3, ipady=3)
        var.trace_add("write", lambda *_: store(var.get()[:20]))
        return var.set

    def _restart_setup(self):
        self._save(setup_done=False)
        self.page_welcome()


def main():
    import multiprocessing
    import sys
    multiprocessing.freeze_support()  # lets the packaged .exe start the device helper process
    setup_crash_log("app")
    if "--image" in sys.argv:
        i = sys.argv.index("--image")
        if i + 1 >= len(sys.argv):
            raise SystemExit("Usage: --image picture.png")
        use_test_image(sys.argv[i + 1])
    elif len(sys.argv) > 1 and sys.argv[1].lower().endswith((".png", ".jpg", ".jpeg", ".bmp", ".webp")):
        use_test_image(sys.argv[1])  # a screenshot was dragged onto the app icon
    try:  # crisp text on scaled Windows displays
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    root = tk.Tk()
    import traceback
    root.report_callback_exception = lambda *a: traceback.print_exception(*a)  # button errors go to the log
    App(root)
    # background threads (camera, text reader) can't be stopped politely; exit cleanly
    root.protocol("WM_DELETE_WINDOW", lambda: os._exit(0))
    root.mainloop()


def use_test_image(path):
    """Test mode: pretend a picture is the capture card.  RoomIDOverlay --image shot.png"""
    global list_devices, list_windows, SETTINGS_DIR, SETTINGS_FILE
    import cv2
    img = cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)  # works with any path on Windows
    if img is None:
        raise SystemExit(f"Couldn't open {path}")
    list_devices = lambda: [(0, f"Test picture ({Path(path).name})")]
    list_windows = lambda: []
    # keep test settings away from the real ones
    SETTINGS_DIR = SETTINGS_DIR / "test"
    SETTINGS_FILE = SETTINGS_DIR / "settings.json"
    if SETTINGS_FILE.exists():
        SETTINGS_FILE.unlink()  # always start the wizard fresh in test mode

    def run(self):
        while True:
            with self.lock:
                self.frame = img if self.source == 0 else None
            time.sleep(0.2)

    Capture._run = run


if __name__ == "__main__":
    main()
