"""
PUBG Mobile Room ID overlay for TikTok LIVE Studio.

Watches a region of the screen (the TTLS / Elgato preview of the game),
reads the room ID and password with OCR, and serves a live text box that
TikTok LIVE Studio shows through a Link source.

Usage:
  python roomid.py --calibrate        Drag a box over where the room ID appears
  python roomid.py                    Run the scanner + overlay server
  python roomid.py --test shot.png    Test OCR/parsing on a screenshot
"""
import argparse
import ctypes
import json
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import mss
import numpy as np

import sys
# When packaged as an .exe, bundled files live in a temp folder (sys._MEIPASS).
BASE = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
CONFIG_PATH = BASE / "config.json"
DEFAULTS = {
    "region": None,          # {"left", "top", "width", "height"} in screen pixels
    "port": 8080,
    "scan_interval": 0.5,    # seconds between scans
    "id_digits": [6, 10],    # min/max length of a room ID
    "confirm_reads": 2,      # identical reads needed before the overlay updates
    "min_confidence": 0.6,   # ignore OCR text below this score
    "source": "screen",      # "screen" or "device" (read the capture card directly)
    "device_index": 0,
    "device_width": 1920,
    "device_height": 1080,
    "device_region": None,   # [x, y, w, h] inside the capture card frame
}


# ---------------------------------------------------------------- config ---

def dpi_aware():
    """Make screen coordinates match real pixels on scaled Windows displays."""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def load_config():
    cfg = dict(DEFAULTS)
    if CONFIG_PATH.exists():
        cfg.update(json.loads(CONFIG_PATH.read_text()))
    return cfg


def save_config(cfg):
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2))


# ----------------------------------------------------------- calibration ---

def calibrate(cfg):
    import tkinter as tk

    with mss.mss() as sct:
        mon = sct.monitors[0]  # the whole virtual desktop (all monitors)

    root = tk.Tk()
    root.overrideredirect(True)
    root.geometry(f"{mon['width']}x{mon['height']}+{mon['left']}+{mon['top']}")
    root.attributes("-alpha", 0.35)
    root.attributes("-topmost", True)
    canvas = tk.Canvas(root, bg="black", highlightthickness=0, cursor="cross")
    canvas.pack(fill="both", expand=True)
    canvas.create_text(
        mon["width"] // 2, 60, fill="white", font=("Segoe UI", 20, "bold"),
        text="Drag a box around the Room ID and password on the game preview.  Esc to cancel.",
    )

    s = {}

    def down(e):
        s.update(x0=e.x_root, y0=e.y_root, cx=e.x, cy=e.y)
        s["rect"] = canvas.create_rectangle(e.x, e.y, e.x, e.y, outline="red", width=3)

    def move(e):
        if "rect" in s:
            canvas.coords(s["rect"], s["cx"], s["cy"], e.x, e.y)

    def up(e):
        if "x0" not in s:
            return
        left, top = min(s["x0"], e.x_root), min(s["y0"], e.y_root)
        w, h = abs(e.x_root - s["x0"]), abs(e.y_root - s["y0"])
        root.destroy()
        if w > 10 and h > 10:
            cfg["region"] = {"left": left, "top": top, "width": w, "height": h}
            save_config(cfg)
            print(f"Saved region: {cfg['region']}")
        else:
            print("Box too small, nothing saved.")

    canvas.bind("<ButtonPress-1>", down)
    canvas.bind("<B1-Motion>", move)
    canvas.bind("<ButtonRelease-1>", up)
    root.bind("<Escape>", lambda e: root.destroy())
    root.mainloop()


# ---------------------------------------------------------------- parsing ---

PW_LABEL = re.compile(r"pass\s*word|pwd|passcode", re.I)
ID_LABEL = re.compile(r"r[o0]{2}m\s*[il1|][d0o]|\bid\b", re.I)


def join_digit_groups(text):
    """'1234 5678' -> '12345678' (OCR often splits long numbers)."""
    return re.sub(r"(?<=\d)[ \u00a0]+(?=\d)", "", text)


def parse(lines, lo=6, hi=10):
    """Pull (room_id, password) out of OCR lines in reading order."""
    digits = re.compile(rf"(?<!\d)\d{{{lo},{hi}}}(?!\d)")
    lines = [join_digit_groups(l.strip()) for l in lines if l.strip()]
    room_id = password = None

    # Password: text after a "Password" label, or on the next line.
    for i, line in enumerate(lines):
        if PW_LABEL.search(line):
            rest = PW_LABEL.split(line, 1)[-1].strip(" :：-")
            if not rest and i + 1 < len(lines):
                rest = lines[i + 1]
            if rest:
                password = rest.split()[0]
            break

    # Room ID: a number next to a "Room ID" label first...
    for i, line in enumerate(lines):
        if ID_LABEL.search(line) and not PW_LABEL.search(line):
            m = digits.search(line)
            if not m and i + 1 < len(lines):
                m = digits.search(lines[i + 1])
            if m:
                room_id = m.group()
                break

    # ...otherwise any number of the right length that isn't the password.
    if room_id is None:
        for line in lines:
            if PW_LABEL.search(line):
                continue
            m = digits.search(line)
            if m and m.group() != password:
                room_id = m.group()
                break

    return room_id, password


# -------------------------------------------------------------------- OCR ---

class Reader:
    def __init__(self, min_conf):
        from rapidocr_onnxruntime import RapidOCR
        self.engine = RapidOCR()
        self.min_conf = min_conf

    @staticmethod
    def preprocess(img):
        """The in-game room ID text is tiny: upscale, boost contrast, pad."""
        import cv2
        scale = max(1.0, 120 / img.shape[0])  # aim for ~120px tall
        scale = min(scale, 6.0)
        if scale > 1:
            img = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
        g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        g = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(g)
        g = cv2.copyMakeBorder(g, 40, 40, 40, 40, cv2.BORDER_CONSTANT, value=int(np.median(g)))
        return cv2.cvtColor(g, cv2.COLOR_GRAY2BGR)

    def lines(self, img):
        result, _ = self.engine(self.preprocess(np.ascontiguousarray(img)))
        if not result:
            return []
        result.sort(key=lambda r: (min(p[1] for p in r[0]), min(p[0] for p in r[0])))
        return [txt for _, txt, score in result if float(score) >= self.min_conf]


# ------------------------------------------------------------------ state ---

class State:
    def __init__(self):
        self.lock = threading.Lock()
        self.data = {
            "room_id": None, "password": None,   # detected
            "manual_room_id": None, "manual_password": None,  # typed in control panel
            "visible": True, "updated": 0,
        }
        self.overlay_seen = 0.0  # last time the on-stream overlay asked for data

    def snapshot(self):
        with self.lock:
            d = dict(self.data)
        d["display_room_id"] = d["manual_room_id"] or d["room_id"]
        d["display_password"] = d["manual_password"] or d["password"]
        return d

    def detected(self, room_id, password):
        with self.lock:
            changed = False
            if room_id != self.data["room_id"]:
                self.data["room_id"] = room_id
                self.data["password"] = password  # new room: drop the old password
                self.data["manual_room_id"] = None
                changed = True
            elif password and password != self.data["password"]:
                self.data["password"] = password
                changed = True
            if changed:
                self.data["updated"] = time.time()
                print(f"[{time.strftime('%H:%M:%S')}] Room ID {room_id}  Password {password or '-'}")

    def apply(self, body):
        with self.lock:
            if body.get("clear"):
                for k in ("room_id", "password", "manual_room_id", "manual_password"):
                    self.data[k] = None
            for k in ("manual_room_id", "manual_password"):
                if k in body:
                    self.data[k] = (str(body[k]).strip() or None) if body[k] is not None else None
            if "visible" in body:
                self.data["visible"] = bool(body["visible"])
            self.data["updated"] = time.time()


class ScreenSource:
    """Reads a region of the screen."""
    def __init__(self, cfg):
        r = cfg["region"]
        self.box = {"left": r["left"], "top": r["top"], "width": r["width"], "height": r["height"]}
        self.sct = mss.mss()

    def frame(self):
        return np.array(self.sct.grab(self.box))[:, :, :3]  # BGRA -> BGR


def open_device(index, width=1920, height=1080):
    import cv2
    cap = cv2.VideoCapture(index, cv2.CAP_DSHOW) if hasattr(cv2, "CAP_DSHOW") else cv2.VideoCapture(index)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
    return cap


class DeviceSource:
    """Reads frames straight from the capture card (full resolution)."""
    def __init__(self, cfg):
        self.cap = open_device(cfg["device_index"], cfg["device_width"], cfg["device_height"])
        if not self.cap.isOpened():
            raise RuntimeError(f"Couldn't open capture device {cfg['device_index']}")
        self.x, self.y, self.w, self.h = cfg["device_region"]
        self.latest = None
        self.lock = threading.Lock()
        threading.Thread(target=self._pump, daemon=True).start()  # always keep the newest frame

    def _pump(self):
        while True:
            ok, f = self.cap.read()
            if ok:
                with self.lock:
                    self.latest = f
            else:
                time.sleep(0.05)

    def frame(self):
        with self.lock:
            f = self.latest
        if f is None:
            return None
        return f[self.y:self.y + self.h, self.x:self.x + self.w]


def scan_loop(cfg, state):
    reader = Reader(cfg["min_confidence"])
    source = DeviceSource(cfg) if cfg["source"] == "device" else ScreenSource(cfg)
    last, streak = None, 0
    while True:
            t0 = time.time()
            try:
                img = source.frame()
                if img is None or img.size == 0:
                    time.sleep(0.2)
                    continue
                rid, pw = parse(reader.lines(img), *cfg["id_digits"])
                if rid:
                    reading = (rid, pw)
                    streak = streak + 1 if reading == last else 1
                    last = reading
                    if streak == cfg["confirm_reads"]:
                        state.detected(rid, pw)
            except Exception as e:
                print("Scan error:", e)
            time.sleep(max(0.0, cfg["scan_interval"] - (time.time() - t0)))


# ----------------------------------------------------------------- server ---

def make_handler(state):
    pages = {"/": "overlay.html", "/overlay": "overlay.html", "/control": "control.html"}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def send(self, code, body, ctype):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = self.path.split("?")[0]
            if path == "/state":
                if "overlay=1" in self.path:  # the overlay page inside LIVE Studio is connected
                    state.overlay_seen = time.time()
                self.send(200, json.dumps(state.snapshot()).encode(), "application/json")
            elif path in pages:
                self.send(200, (BASE / pages[path]).read_bytes(), "text/html; charset=utf-8")
            else:
                self.send(404, b"not found", "text/plain")

        def do_POST(self):
            if self.path.split("?")[0] != "/state":
                return self.send(404, b"not found", "text/plain")
            length = int(self.headers.get("Content-Length", 0))
            try:
                state.apply(json.loads(self.rfile.read(length) or b"{}"))
            except ValueError:
                return self.send(400, b"bad json", "text/plain")
            self.send(200, json.dumps(state.snapshot()).encode(), "application/json")

    return Handler


# ------------------------------------------------------------------- main ---

def main():
    ap = argparse.ArgumentParser(description="PUBG Mobile room ID overlay")
    ap.add_argument("--calibrate", action="store_true", help="select the screen area to watch")
    ap.add_argument("--test", metavar="IMAGE", help="run OCR + parsing on a screenshot and exit")
    ap.add_argument("--find-device", action="store_true",
                    help="try capture devices 0-5 and save a snapshot from each")
    ap.add_argument("--calibrate-device", metavar="N", type=int,
                    help="select the room ID area on capture device N")
    args = ap.parse_args()

    dpi_aware()
    cfg = load_config()

    if args.calibrate:
        cfg["source"] = "screen"
        return calibrate(cfg)

    if args.find_device:
        import cv2
        print("Checking capture devices (keep TTLS running to test sharing)...")
        for i in range(6):
            cap = open_device(i, cfg["device_width"], cfg["device_height"])
            ok, f = (cap.read() if cap.isOpened() else (False, None))
            for _ in range(5):  # some cards need a few frames to wake up
                if ok and f is not None and f.mean() > 5:
                    break
                ok, f = cap.read() if cap.isOpened() else (False, None)
            cap.release()
            if ok and f is not None:
                name = BASE / f"device_{i}.png"
                cv2.imwrite(str(name), f)
                note = "  (black frame: device busy or no signal)" if f.mean() < 5 else ""
                print(f"  Device {i}: {f.shape[1]}x{f.shape[0]}  saved {name.name}{note}")
            else:
                print(f"  Device {i}: not available")
        print("Open the device_N.png files to see which one is the Elgato.")
        return

    if args.calibrate_device is not None:
        import cv2
        cap = open_device(args.calibrate_device, cfg["device_width"], cfg["device_height"])
        f = None
        for _ in range(15):
            ok, fr = cap.read()
            if ok:
                f = fr
        cap.release()
        if f is None:
            return print(f"Couldn't read device {args.calibrate_device}.")
        print("Get the custom room screen showing, then drag a box around 'Room ID: ...' "
              "and press Enter (C to cancel).")
        view = f
        scale = min(1.0, 1500 / f.shape[1])  # shrink the window if the frame is huge
        if scale < 1:
            view = cv2.resize(f, None, fx=scale, fy=scale)
        x, y, w, h = cv2.selectROI("Select Room ID area", view, showCrosshair=False)
        cv2.destroyAllWindows()
        if w < 5 or h < 5:
            return print("Nothing selected.")
        x, y, w, h = (round(v / scale) for v in (x, y, w, h))
        cfg.update(source="device", device_index=args.calibrate_device, device_region=[x, y, w, h])
        save_config(cfg)
        crop = f[y:y + h, x:x + w]
        cv2.imwrite(str(BASE / "calibration_crop.png"), crop)
        lines = Reader(cfg["min_confidence"]).lines(crop)
        print(f"Saved. Device {args.calibrate_device}, area {[x, y, w, h]}")
        print("Test read:", lines, "->", parse(lines, *cfg["id_digits"]))
        return

    if args.test:
        import cv2
        img = cv2.imread(args.test)
        if img is None:
            return print("Couldn't open", args.test)
        lines = Reader(cfg["min_confidence"]).lines(img)
        print("OCR lines:", lines)
        print("Parsed (room_id, password):", parse(lines, *cfg["id_digits"]))
        return

    if cfg["source"] == "device" and not cfg["device_region"]:
        return print("No device area set yet. Run:  python roomid.py --calibrate-device N")
    if cfg["source"] == "screen" and not cfg["region"]:
        return print("Nothing calibrated yet. Run:  python roomid.py --find-device  (recommended)\n"
                     "or:                          python roomid.py --calibrate")

    state = State()
    threading.Thread(target=scan_loop, args=(cfg, state), daemon=True).start()
    server = ThreadingHTTPServer(("127.0.0.1", cfg["port"]), make_handler(state))
    print(f"Overlay:        http://localhost:{cfg['port']}/")
    print(f"Control panel:  http://localhost:{cfg['port']}/control")
    print("Watching for room IDs... (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Stopped.")


if __name__ == "__main__":
    main()
