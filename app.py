"""
Room ID Overlay - desktop app with a step-by-step setup wizard.

First launch walks through setup one screen at a time:
  welcome > find the game feed > open a custom room > auto-find the Room ID >
  password > add to TikTok LIVE Studio (auto-detected) > done
After that it opens to a small everyday screen.
"""
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

APP_NAME = "Room ID Overlay"
PORT = 8080
OVERLAY_URL = f"http://localhost:{PORT}/"
SCREEN = "screen"
SETTINGS_DIR = Path(os.environ.get("APPDATA", Path.home())) / "RoomIDOverlay"
SETTINGS_FILE = SETTINGS_DIR / "settings.json"

BG, PANEL, TEXT, MUTED, ACCENT, GOOD, BAD = (
    "#16181d", "#20232a", "#f1f1f1", "#9aa0a6", "#f5b400", "#4cd07d", "#ff6b6b")
FONT = "Segoe UI"
WIDTH = 640
STEPS = ["Game", "Room", "Room ID", "Password", "LIVE Studio"]


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
        current = object()
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
        last, streak = None, 0
        while True:
            t0 = time.time()
            try:
                img = crop(self.capture.get(), self.settings.get("region"))
                if img is not None:
                    with self.ocr_lock:
                        rid, _ = core.parse(self.reader.lines(img), 6, 10)
                    self.last_text = rid
                    if rid:
                        streak = streak + 1 if rid == last else 1
                        last = rid
                        if streak == 2:
                            self.state.detected(rid, None)
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

        if self.settings.get("password"):
            self.state.apply({"manual_password": self.settings["password"]})
        self.state.apply({"visible": self.settings.get("visible", True)})

        self._start_server()
        self.scanner = Scanner(self.capture, self.state, self.settings)
        self._setup_window()

        src = self.settings.get("source")
        if self.settings.get("setup_done"):
            self._use_saved_source()
            self.page_dashboard()
        else:
            self.page_welcome()
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
        r.geometry(f"{WIDTH}x720")
        r.resizable(False, False)
        style = ttk.Style(r)
        style.theme_use("clam")
        style.configure("TButton", font=(FONT, 11), padding=(14, 8), background="#333", foreground=TEXT,
                        borderwidth=0)
        style.map("TButton", background=[("active", "#444"), ("disabled", "#2a2a2a")],
                  foreground=[("disabled", "#666")])
        style.configure("Big.TButton", font=(FONT, 12, "bold"), padding=(20, 10), background=ACCENT,
                        foreground="#111")
        style.map("Big.TButton", background=[("active", "#ffd040"), ("disabled", "#5a4a10")])
        self.body = tk.Frame(r, bg=BG)
        self.body.pack(fill="both", expand=True)

    def _tick(self):
        if self.page_tick:
            try:
                self.page_tick()
            except tk.TclError:
                pass
        self.root.after(300, self._tick)

    def _save(self, **kw):
        self.settings.update(kw)
        save_settings(self.settings)

    def _use_saved_source(self):
        src, name = self.settings.get("source"), self.settings.get("source_name")
        if src == SCREEN:
            self.capture.set_source(SCREEN)
            return
        for i, n in self.devices:  # match by name: the number can change after a reboot
            if n == name:
                self.capture.set_source(i)
                return
        if isinstance(src, int):
            self.capture.set_source(src)

    def _overlay_connected(self):
        return time.time() - self.state.overlay_seen < 3

    # page building helpers --------------------------------------------------
    def _page(self, step=None, title="", text=""):
        """Clear the window and start a new page. Returns the content frame."""
        self.page_tick = None
        self.images = []
        for w in self.body.winfo_children():
            w.destroy()
        if step is not None:
            bar = tk.Frame(self.body, bg=BG)
            bar.pack(fill="x", padx=28, pady=(20, 0))
            for i, name in enumerate(STEPS):
                color = ACCENT if i == step else (GOOD if i < step else "#555")
                mark = "✓" if i < step else str(i + 1)
                tk.Label(bar, text=f"{mark}  {name}", bg=BG, fg=color,
                         font=(FONT, 10, "bold" if i == step else "normal")).pack(side="left", padx=(0, 18))
        tk.Label(self.body, text=title, bg=BG, fg=TEXT, font=(FONT, 20, "bold"), anchor="w",
                 justify="left", wraplength=WIDTH - 56).pack(fill="x", padx=28, pady=(18, 6))
        if text:
            tk.Label(self.body, text=text, bg=BG, fg=MUTED, font=(FONT, 12), anchor="w", justify="left",
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

    def page_game_intro(self):
        # try likely capture cards first, then the others, then the whole screen
        devs = sorted(self.devices, key=lambda d: not looks_like_capture_card(d[1]))
        self.candidates = devs + [(SCREEN, "Your whole screen")]
        self.page_game(0)

    def page_game(self, n):
        if n >= len(self.candidates):
            return self.page_no_game()
        src, name = self.candidates[n]
        self.capture.set_source(src)
        started = time.time()
        c = self._page(0, "Can you see your game?",
                       f"Showing: {name}" + (" (use this only if nothing else worked)" if src == SCREEN else ""))
        preview = tk.Label(c, bg="#000", width=60, height=16)
        preview.pack(pady=(4, 0))
        status = self._status(c, "Loading the picture...")
        yes = self._button("Yes, that's my game", lambda: self._pick_game(src, name))
        self._button("No, show the next one", lambda: self.page_game(n + 1), primary=False)
        self._button("Back", self.page_welcome, primary=False, side="left")
        yes.state(["disabled"])

        def tick():
            f = self.capture.get()
            if f is not None:
                p = to_photo(f, WIDTH - 56, 330)
                self.images[:] = [p]
                preview.configure(image=p, width=p.width(), height=p.height())
            waited = time.time() - started
            if f is not None and not is_black(f):
                yes.state(["!disabled"])
                status.configure(fg=MUTED, text="If this is your PUBG game, click  Yes, that's my game.")
            elif f is not None and waited > 3:
                yes.state(["!disabled"])
                status.configure(fg=BAD, text="The picture is black. Is PUBG showing on your phone? "
                                              "If it stays black, click  No, show the next one.")
            elif src in self.capture.failed:
                status.configure(fg=BAD, text="This one can't be opened. Click  No, show the next one.")
            elif waited > 8:
                status.configure(fg=BAD, text="No picture from this one. Click  No, show the next one.")
        self.page_tick = tick

    def page_no_game(self):
        c = self._page(0, "We couldn't find your game",
                       "Check that the Elgato is plugged in and PUBG is showing on your phone, then try again.")
        self._button("Try again", self.page_game_intro)

    def _pick_game(self, src, name):
        self.capture.set_source(src)
        self._save(source=src, source_name=None if src == SCREEN else name)
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
        e = tk.Entry(c, textvariable=var, font=(FONT, 22), width=12, bg="#0e0f12", fg=TEXT,
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

    def page_ttls(self):
        c = self._page(4, "Add the box to TikTok LIVE Studio",
                       "You only do this once. Follow these steps in LIVE Studio:")
        steps = [
            "Click  Copy link  below.",
            "In LIVE Studio, click  Add source  (bottom left).",
            "Choose  Link.",
            "Paste the link (Ctrl + V) and click  Add.",
            "Drag the box to where you want it on your stream.",
        ]
        for i, s in enumerate(steps, 1):
            row = tk.Frame(c, bg=BG)
            row.pack(fill="x", pady=4)
            tk.Label(row, text=str(i), bg=ACCENT, fg="#111", font=(FONT, 11, "bold"), width=2).pack(side="left")
            tk.Label(row, text="  " + s, bg=BG, fg=TEXT, font=(FONT, 12), anchor="w").pack(side="left")
        copy_row = tk.Frame(c, bg=BG)
        copy_row.pack(fill="x", pady=(14, 0))
        link = tk.Entry(copy_row, font=(FONT, 13), width=24, bg="#0e0f12", fg=TEXT,
                        readonlybackground="#0e0f12", relief="flat")
        link.insert(0, OVERLAY_URL)
        link.configure(state="readonly")
        link.pack(side="left", ipady=6)
        copy_btn = ttk.Button(copy_row, text="Copy link", style="Big.TButton")
        copy_btn.pack(side="left", padx=10)

        def copy():
            self.root.clipboard_clear()
            self.root.clipboard_append(OVERLAY_URL)
            copy_btn.configure(text="Copied!")
            self.root.after(1500, lambda: copy_btn.configure(text="Copy link"))

        copy_btn.configure(command=copy)
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
                       "Leave this open while you stream. You can minimise it.")
        panel = tk.Frame(c, bg=PANEL, padx=18, pady=16)
        panel.pack(fill="x")
        tk.Label(panel, text="On stream now", bg=PANEL, fg=MUTED, font=(FONT, 11)).pack(anchor="w")
        live = tk.Label(panel, text="", bg=PANEL, fg=TEXT, font=(FONT, 26, "bold"), anchor="w")
        live.pack(fill="x")
        checks = tk.Label(panel, text="", bg=PANEL, fg=MUTED, font=(FONT, 11), justify="left", anchor="w")
        checks.pack(fill="x", pady=(8, 0))

        row = tk.Frame(c, bg=BG)
        row.pack(fill="x", pady=(18, 0))
        tk.Label(row, text="Password", bg=BG, fg=TEXT, font=(FONT, 12), width=9, anchor="w").pack(side="left")
        pw_var = tk.StringVar(value=self.settings.get("password", ""))
        pw = tk.Entry(row, textvariable=pw_var, font=(FONT, 14), width=12, bg="#0e0f12", fg=TEXT,
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
        rid = tk.Entry(row2, textvariable=rid_var, font=(FONT, 14), width=12, bg="#0e0f12", fg=TEXT,
                       insertbackground=TEXT, relief="flat")
        rid.pack(side="left", ipady=5)
        set_rid = lambda: self.state.apply({"manual_room_id": rid_var.get().strip()})
        rid.bind("<Return>", lambda _: set_rid())
        ttk.Button(row2, text="Use this", command=set_rid).pack(side="left", padx=8)
        tk.Label(c, text="Only if the ID on stream is wrong. A new room replaces it automatically.",
                 bg=BG, fg=MUTED, font=(FONT, 10), anchor="w").pack(fill="x", pady=(4, 0))

        vis = tk.BooleanVar(value=self.settings.get("visible", True))

        def toggle():
            self._save(visible=vis.get())
            self.state.apply({"visible": vis.get()})

        tk.Checkbutton(c, text="Show the box on stream", variable=vis, command=toggle, bg=BG, fg=TEXT,
                       selectcolor="#0e0f12", activebackground=BG, activeforeground=TEXT,
                       font=(FONT, 12)).pack(anchor="w", pady=(16, 0))

        self._button("Run setup again", self._restart_setup, primary=False, side="left")

        def tick():
            s = self.state.snapshot()
            if s["display_room_id"]:
                pwtxt = f"    PW {s['display_password']}" if s["display_password"] else ""
                live.configure(text=f"ID {s['display_room_id']}{pwtxt}",
                               fg=TEXT if s["visible"] else MUTED)
            else:
                live.configure(text="Waiting for a room...", fg=MUTED)
            f = self.capture.get()
            game = ("✓  Game picture OK", GOOD) if not is_black(f) else ("✗  No game picture: is PUBG on?", BAD)
            ttls = ("✓  LIVE Studio connected", GOOD) if self._overlay_connected() else \
                ("•  LIVE Studio not connected (open LIVE Studio)", MUTED)
            err = f"\n!  {self.scanner.error}" if self.scanner.error else ""
            checks.configure(text=f"{game[0]}\n{ttls[0]}{err}",
                             fg=BAD if (err or game[1] == BAD) else (GOOD if ttls[1] == GOOD else MUTED))
        self.page_tick = tick

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
    global list_devices, SETTINGS_DIR, SETTINGS_FILE
    import cv2
    img = cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)  # works with any path on Windows
    if img is None:
        raise SystemExit(f"Couldn't open {path}")
    list_devices = lambda: [(0, f"Test picture ({Path(path).name})")]
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
