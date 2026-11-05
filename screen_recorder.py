"""Screen Recorder Pro - full-featured Windows screen recorder.

Copyright (C) 2026 Cambo Ads. Free software, licensed under AGPL-3.0 (see LICENSE).

Video is captured by FFmpeg (DXGI Desktop Duplication or GDI), audio is captured
in-process through PortAudio/WASAPI (any microphone + system sound loopback),
and everything is muxed together by FFmpeg when recording stops.
"""
import ctypes
import datetime
import hashlib
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import tkinter as tk
import urllib.error
import urllib.request
import wave
import webbrowser
from ctypes import wintypes
from tkinter import filedialog, messagebox

import customtkinter as ctk
import numpy as np
import pyaudiowpatch as pyaudio
from PIL import Image, ImageDraw, ImageFont

APP_NAME = "Screen Recorder Pro"
APP_VERSION = "1.0.1"  # the release tag must be this version, e.g. v1.0.0
COPYRIGHT = "© 2026 Cambo Ads"
REPO = "channaofficial1996/ScreenRecorderPro"
REPO_URL = f"https://github.com/{REPO}"
RELEASES_URL = f"{REPO_URL}/releases/latest"
UPDATE_API = f"https://api.github.com/repos/{REPO}/releases/latest"
UPDATE_ASSET = "ScreenRecorderPro.exe"
CREATE_NO_WINDOW = 0x08000000

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)  # per-monitor DPI aware -> physical pixels
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

user32 = ctypes.windll.user32


def resource_path(name):
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, name)


FFMPEG = resource_path("ffmpeg.exe")
if not os.path.exists(FFMPEG):
    FFMPEG = shutil.which("ffmpeg") or "ffmpeg"

SETTINGS_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "ScreenRecorderPro")
SETTINGS_FILE = os.path.join(SETTINGS_DIR, "settings.json")
DEFAULT_OUT = os.path.join(os.path.expanduser("~"), "Videos", "Screen Recordings")

MODE_MON, MODE_ALL, MODE_REGION, MODE_WIN = "Full screen (one monitor)", "All monitors", "Custom area (resizable frame)", "Window"
PRESETS = {"Fit to monitor": None, "1920 x 1080  (Full HD)": (1920, 1080), "1600 x 900": (1600, 900),
           "1280 x 720  (HD)": (1280, 720), "1024 x 768": (1024, 768), "854 x 480": (854, 480),
           "640 x 360": (640, 360), "1080 x 1920  (Shorts / TikTok)": (1080, 1920),
           "720 x 1280  (vertical HD)": (720, 1280), "1080 x 1080  (square)": (1080, 1080)}
MIN_SIZE = 64
ENGINE_AUTO, ENGINE_GDI = "Auto (DXGI - fast)", "GDI (compatible)"
QUALITIES = ["Low", "Medium", "High", "Very high"]
RESOLUTIONS = {"Original": 0, "1440p": 1440, "1080p": 1080, "720p": 720, "480p": 480}
ENCODERS = [("nvenc", "NVIDIA GPU (NVENC)", "h264_nvenc"),
            ("qsv", "Intel GPU (QuickSync)", "h264_qsv"),
            ("amf", "AMD GPU (AMF)", "h264_amf"),
            ("x264", "CPU (x264)", "libx264")]
ENC_LABEL = {k: label for k, label, _ in ENCODERS}
HOTKEYS = {1: (0x2 | 0x1, 0x52, "rec"),     # Ctrl+Alt+R
           2: (0x2 | 0x1, 0x50, "pause"),   # Ctrl+Alt+P
           3: (0x2 | 0x1, 0x53, "shot"),    # Ctrl+Alt+S
           4: (0x2 | 0x1, 0x5A, "zoom")}    # Ctrl+Alt+Z
ZOOM_LEVELS = {"1.5x": 1.5, "2x": 2.0, "2.5x": 2.5, "3x": 3.0}

C_BG, C_SURFACE, C_FIELD, C_BORDER = "#101217", "#191c23", "#262a33", "#363b47"
C_TEXT, C_MUTED, C_IDLE = "#f2f4f8", "#a0a7b5", "#5b6170"
C_ACCENT, C_ACCENT_DARK = "#2f6fed", "#2459c4"
C_RED, C_RED_DARK, C_AMBER, C_GREEN, C_GREEN_DARK = "#e5383b", "#b92b2e", "#f5b83d", "#2fb170", "#238a57"


# ----------------------------------------------------------------------------- system helpers

def run_quiet(cmd, timeout=30, cwd=None):
    return subprocess.run(cmd, capture_output=True, creationflags=CREATE_NO_WINDOW, timeout=timeout, cwd=cwd)


class GUID(ctypes.Structure):
    _fields_ = [("d1", ctypes.c_ulong), ("d2", ctypes.c_ushort), ("d3", ctypes.c_ushort), ("d4", ctypes.c_ubyte * 8)]


class DXGI_OUTPUT_DESC(ctypes.Structure):
    _fields_ = [("DeviceName", wintypes.WCHAR * 32), ("DesktopCoordinates", wintypes.RECT),
                ("AttachedToDesktop", wintypes.BOOL), ("Rotation", ctypes.c_uint), ("Monitor", wintypes.HMONITOR)]


def _vcall(obj, idx, *argtypes):
    vtbl = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0]
    return ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *argtypes)(vtbl[idx])


def get_dxgi_outputs():
    """Device names of the outputs on the default adapter, in the order ffmpeg's ddagrab uses."""
    names = []
    try:
        iid = GUID(0x7B7166EC, 0x21C7, 0x44AE, (ctypes.c_ubyte * 8)(0xB2, 0x1A, 0xC9, 0xAE, 0x32, 0x1A, 0xE3, 0x69))
        factory = ctypes.c_void_p()
        if ctypes.windll.dxgi.CreateDXGIFactory(ctypes.byref(iid), ctypes.byref(factory)) != 0:
            return names
        adapter = ctypes.c_void_p()
        if _vcall(factory, 7, ctypes.c_uint, ctypes.POINTER(ctypes.c_void_p))(factory, 0, ctypes.byref(adapter)) == 0:
            i = 0
            while True:
                out = ctypes.c_void_p()
                if _vcall(adapter, 7, ctypes.c_uint, ctypes.POINTER(ctypes.c_void_p))(adapter, i, ctypes.byref(out)) != 0:
                    break
                desc = DXGI_OUTPUT_DESC()
                _vcall(out, 7, ctypes.POINTER(DXGI_OUTPUT_DESC))(out, ctypes.byref(desc))
                names.append(desc.DeviceName)
                _vcall(out, 2)(out)
                i += 1
            _vcall(adapter, 2)(adapter)
        _vcall(factory, 2)(factory)
    except Exception:
        pass
    return names


class MONITORINFOEXW(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT), ("rcWork", wintypes.RECT),
                ("dwFlags", wintypes.DWORD), ("szDevice", wintypes.WCHAR * 32)]


def get_monitors():
    mons = []
    proc_t = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC, ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)

    def cb(hmon, hdc, lprc, lparam):
        info = MONITORINFOEXW()
        info.cbSize = ctypes.sizeof(info)
        user32.GetMonitorInfoW(hmon, ctypes.byref(info))
        r = info.rcMonitor
        mons.append({"x": r.left, "y": r.top, "w": r.right - r.left, "h": r.bottom - r.top,
                     "primary": bool(info.dwFlags & 1), "device": info.szDevice})
        return True

    user32.EnumDisplayMonitors(None, None, proc_t(cb), 0)
    dxgi = get_dxgi_outputs()
    for m in mons:
        m["dxgi"] = dxgi.index(m["device"]) if m["device"] in dxgi else None
    mons.sort(key=lambda m: (not m["primary"], m["x"], m["y"]))
    return mons


def get_windows(exclude=""):
    titles = []
    proc_t = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def cb(hwnd, lparam):
        if user32.IsWindowVisible(hwnd) and not user32.IsIconic(hwnd):
            n = user32.GetWindowTextLengthW(hwnd)
            if n:
                buf = ctypes.create_unicode_buffer(n + 1)
                user32.GetWindowTextW(hwnd, buf, n + 1)
                t = buf.value.strip()
                if t and t not in titles and t not in ("Program Manager", exclude):
                    titles.append(t)
        return True

    user32.EnumWindows(proc_t(cb), 0)
    return titles


def virtual_screen():
    return (user32.GetSystemMetrics(76), user32.GetSystemMetrics(77),
            user32.GetSystemMetrics(78), user32.GetSystemMetrics(79))


def clamp_region(region):
    """Keep a region on the virtual desktop with even dimensions (required by H.264)."""
    vx, vy, vw, vh = virtual_screen()
    x, y, w, h = (int(v) for v in region)
    w = max(MIN_SIZE, min(w, vw))
    h = max(MIN_SIZE, min(h, vh))
    w, h = w - w % 2, h - h % 2
    x = min(max(x, vx), vx + vw - w)
    y = min(max(y, vy), vy + vh - h)
    return x, y, w, h


def detect_encoders():
    found = []
    for key, _, codec in ENCODERS[:-1]:
        try:
            r = run_quiet([FFMPEG, "-hide_banner", "-f", "lavfi", "-i", "color=black:s=640x360:r=30",
                           "-frames:v", "5", "-pix_fmt", "nv12", "-c:v", codec, "-f", "null", "-"], 30)
            if r.returncode == 0:
                found.append(key)
        except Exception:
            pass
    return found + ["x264"]


def encoder_args(key, quality, fps):
    q = QUALITIES.index(quality) if quality in QUALITIES else 2
    gop = ["-g", str(fps * 2)]
    if key == "nvenc":
        return ["-c:v", "h264_nvenc", "-preset", "p4", "-rc", "vbr", "-cq", str([32, 26, 21, 17][q]), "-b:v", "0"] + gop, "nv12"
    if key == "qsv":
        return ["-c:v", "h264_qsv", "-preset", "faster", "-global_quality", str([32, 26, 21, 17][q])] + gop, "nv12"
    if key == "amf":
        v = str([32, 26, 21, 17][q])
        return ["-c:v", "h264_amf", "-quality", "speed", "-rc", "cqp", "-qp_i", v, "-qp_p", v] + gop, "nv12"
    return ["-c:v", "libx264", "-preset", "veryfast", "-crf", str([30, 25, 21, 18][q])] + gop, "yuv420p"


def build_capture_cmd(cap, engine, frames=None):
    """FFmpeg input arguments + filters for the capture area."""
    cmd = [FFMPEG, "-hide_banner", "-y"]
    vf = []
    if engine == "dxgi":
        idx, ox, oy, w, h = cap["dxgi"]
        src = f"ddagrab=output_idx={idx}:framerate={cap['fps']}:draw_mouse={int(cap['cursor'])}"
        if w:
            src += f":offset_x={ox}:offset_y={oy}:video_size={w}x{h}"
        cmd += ["-f", "lavfi", "-i", src]
        vf += ["hwdownload", "format=bgra"]
    else:
        cmd += ["-thread_queue_size", "512", "-f", "gdigrab", "-framerate", str(cap["fps"]),
                "-draw_mouse", str(int(cap["cursor"]))]
        if cap["rect"]:
            x, y, w, h = cap["rect"]
            cmd += ["-offset_x", str(x), "-offset_y", str(y), "-video_size", f"{w}x{h}"]
        cmd += ["-i", f"title={cap['title']}" if cap["title"] else "desktop"]
    vf.append("crop=trunc(iw/2)*2:trunc(ih/2)*2")
    return cmd, vf


# ----------------------------------------------------------------------------- zoom

class ZoomCamera:
    """The virtual camera of a zoom, in 0..1 fractions of the video.

    Stepped live for the on-screen rectangle and again, frame by frame, when the video is saved.
    The view only pans once the cursor leaves the middle of the view, so small pointer movements
    while explaining something do not shake the picture.
    """
    RAMP, FOLLOW, DEAD = 0.4, 0.2, 0.5  # seconds in/out, pan smoothing seconds, dead-zone share of the view

    def __init__(self):
        self.p, self.level, self.cx, self.cy = 0.0, 2.0, 0.5, 0.5

    def step(self, dt, on, level, tx, ty, follow):
        if on and self.p == 0.0:
            self.level = level
            self.cx, self.cy = self._inside(tx), self._inside(ty)
        elif on and follow:
            self.cx = self._pan(self.cx, tx, dt)
            self.cy = self._pan(self.cy, ty, dt)
        self.p = min(1.0, max(0.0, self.p + (dt if on else -dt) / self.RAMP))

    def _inside(self, v):
        half = 0.5 / self.level
        return min(1.0 - half, max(half, v))

    def _pan(self, c, target, dt):
        dead = 0.5 / self.level * self.DEAD
        off = target - c
        if abs(off) <= dead:
            return c
        goal = target - dead if off > 0 else target + dead
        return self._inside(c + (goal - c) * (1.0 - np.exp(-dt / self.FOLLOW)))

    def view(self):
        """(x, y, w, h) of the visible part of the frame."""
        ease = self.p * self.p * (3.0 - 2.0 * self.p)
        z = 1.0 + (self.level - 1.0) * ease
        size = 1.0 / z
        # slide from the frame centre to the target so the view never leaves the frame
        k = (1.0 - size) / (1.0 - 1.0 / self.level)
        return (0.5 + (self.cx - 0.5) * k - size / 2, 0.5 + (self.cy - 0.5) * k - size / 2, size, size)


def zoom_commands(zooms, fps, width, height):
    """sendcmd script that animates a crop over `zooms` for a width x height video.

    A zoom is {"start", "end", "level", "follow", "pts": [(t, x, y), ...]} on the video timeline.
    """
    cam = ZoomCamera()
    lines, last, dt = [], (0, 0, width, height), 1.0 / fps
    n, i = int(zooms[0]["start"] * fps), 0
    stop = int((zooms[-1]["end"] + ZoomCamera.RAMP) * fps) + 2
    while n < stop:
        t = n * dt
        while i < len(zooms) and t >= zooms[i]["end"]:
            i += 1
        z = zooms[i] if i < len(zooms) and t >= zooms[i]["start"] else None
        if z:
            pts = z["pts"]
            tx = float(np.interp(t, [q[0] for q in pts], [q[1] for q in pts]))
            ty = float(np.interp(t, [q[0] for q in pts], [q[2] for q in pts]))
            cam.step(dt, True, z["level"], tx, ty, z["follow"])
        elif cam.p > 0.0:
            cam.step(dt, False, 0, 0, 0, False)
        else:
            n += 1
            continue
        fx, fy, fw, fh = cam.view()
        w = min(width, max(2, round(fw * width / 2) * 2))
        h = min(height, max(2, round(fh * height / 2) * 2))
        rect = (min(width - w, max(0, round(fx * width))), min(height - h, max(0, round(fy * height))), w, h)
        if rect != last:
            last = rect
            lines.append(f"{t:.4f} crop x {rect[0]}, crop y {rect[1]}, crop w {w}, crop h {h};")
        n += 1
    return lines


def video_size(path):
    err = run_quiet([FFMPEG, "-hide_banner", "-i", path]).stderr.decode("utf-8", "replace")
    m = re.search(r"Video:.*?(\d{2,5})x(\d{2,5})", err)
    if not m:
        raise RuntimeError("Could not read the video size.")
    return int(m.group(1)), int(m.group(2))


def capture_rect(cap):
    """Where the captured picture currently is on the desktop."""
    if cap["title"]:
        hwnd = user32.FindWindowW(None, cap["title"])
        rc, pt = wintypes.RECT(), wintypes.POINT(0, 0)
        if hwnd and user32.GetClientRect(hwnd, ctypes.byref(rc)) and user32.ClientToScreen(hwnd, ctypes.byref(pt)):
            return pt.x, pt.y, max(1, rc.right), max(1, rc.bottom)
    return cap["rect"] or virtual_screen()


def cursor_fraction(rect):
    pt = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(pt))
    x, y, w, h = rect
    return min(1.0, max(0.0, (pt.x - x) / w)), min(1.0, max(0.0, (pt.y - y) / h))


# ----------------------------------------------------------------------------- recording engine

class FFProc:
    """An FFmpeg process whose stderr is drained continuously; stopped gracefully with 'q'."""

    def __init__(self, cmd):
        self.log = bytearray()
        self.started = threading.Event()
        self.p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                  creationflags=CREATE_NO_WINDOW)
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        while True:
            b = self.p.stderr.read1(4096)
            if not b:
                break
            self.log += b
            if not self.started.is_set() and b"Press [q]" in self.log:
                self.started.set()
            if len(self.log) > 40000:
                del self.log[:-20000]

    def wait_started(self, timeout=15):
        end = time.time() + timeout
        while time.time() < end:
            if self.started.wait(0.1):
                return True
            if self.p.poll() is not None:
                return False
        return False

    def stop(self, timeout=20):
        try:
            self.p.stdin.write(b"q")
            self.p.stdin.flush()
        except Exception:
            pass
        try:
            self.p.wait(timeout)
        except subprocess.TimeoutExpired:
            self.kill()

    def kill(self):
        try:
            self.p.kill()
            self.p.wait(5)
        except Exception:
            pass

    def tail(self):
        return self.log.decode("utf-8", "replace")[-1500:]


class AudioSource:
    """A live input stream (microphone or WASAPI loopback) with level metering and wav writing.

    The writer keeps the audio locked to the wall clock: gaps (loopback devices send nothing
    during silence, Bluetooth mics drop out) are filled with silence, and a device clock that
    runs fast is trimmed, so the track stays in sync with the video.
    """

    def __init__(self, pa, info):
        self.info = info
        self.name = info["name"]
        self.peak = 0.0
        self.lock = threading.Lock()
        self.wav = None
        self.paused = False
        self.stream = None
        maxch = int(info["maxInputChannels"])
        rate0 = int(info["defaultSampleRate"])
        tries = []
        for ch in (min(2, maxch), maxch, 1):
            for rate in (rate0, 48000, 44100, 32000, 16000):
                if ch > 0 and (ch, rate) not in tries:
                    tries.append((ch, rate))
        err = None
        for ch, rate in tries:
            try:
                self.ch, self.rate = ch, rate
                self.stream = pa.open(format=pyaudio.paInt16, channels=ch, rate=rate, input=True,
                                      input_device_index=info["index"], frames_per_buffer=1024,
                                      stream_callback=self._cb)
                break
            except Exception as e:
                self.stream, err = None, e
        if self.stream is None:
            raise RuntimeError(f"Cannot open '{self.name}': {err}")

    def _cb(self, data, frames, time_info, status):
        try:
            a = np.frombuffer(data, dtype=np.int16)
            if a.size:
                p = float(np.abs(a.astype(np.int32)).max()) / 32768.0
                if p > self.peak:
                    self.peak = p
            with self.lock:
                if self.wav is not None and not self.paused:
                    expected = int((self.active + time.perf_counter() - self.seg_start) * self.rate)
                    gap = expected - frames - self.written
                    if gap > self.rate * 0.15:
                        self._silence(gap)
                    elif -gap > self.rate * 0.3:
                        return None, pyaudio.paContinue
                    self.wav.writeframes(data)
                    self.written += frames
        except Exception:
            pass
        return None, pyaudio.paContinue

    def _silence(self, n):
        while n > 0:
            k = min(n, self.rate)
            self.wav.writeframes(b"\x00" * (k * 2 * self.ch))
            self.written += k
            n -= k

    def start_writing(self, path):
        with self.lock:
            w = wave.open(path, "wb")
            w.setnchannels(self.ch)
            w.setsampwidth(2)
            w.setframerate(self.rate)
            self.path, self.wav, self.written, self.active = path, w, 0, 0.0
            self.paused = False
            self.seg_start = time.perf_counter()

    def pause(self):
        with self.lock:
            if self.wav is not None and not self.paused:
                self.active += time.perf_counter() - self.seg_start
                self.paused = True

    def resume(self):
        with self.lock:
            if self.wav is not None and self.paused:
                self.seg_start = time.perf_counter()
                self.paused = False

    def stop_writing(self):
        with self.lock:
            if self.wav is None:
                return None
            if not self.paused:
                self.active += time.perf_counter() - self.seg_start
            missing = int(self.active * self.rate) - self.written
            if missing > 0:
                self._silence(missing)
            self.wav.close()
            self.wav = None
            return self.path

    def close(self):
        try:
            self.stream.stop_stream()
            self.stream.close()
        except Exception:
            pass


def mux(segments, lengths, audios, out, tmp, zoom=None):
    """Join video segments and mix audio tracks into the final file.

    FFmpeg keeps capturing for a moment after it is told to stop, so each segment is cut to its
    measured length; otherwise every pause would push the video further from the audio and zooms.
    The video is copied untouched unless `zoom` asks for it to be re-encoded with the zoom applied.
    """
    lst = os.path.join(tmp, "segments.txt")
    with open(lst, "w", encoding="utf-8") as f:
        for s, length in zip(segments, lengths):
            f.write("file '%s'\noutpoint %.3f\n" % (s.replace("\\", "/").replace("'", "'\\''"), length))
    cmd = [FFMPEG, "-hide_banner", "-y", "-f", "concat", "-safe", "0", "-i", lst]
    for a in audios:
        cmd += ["-i", a["path"]]
    parts, vmap, vcodec = [], "0:v", ["-c:v", "copy"]
    if zoom:
        w, h = zoom["size"]
        parts.append(f"[0:v]sendcmd=f={zoom['script']},crop=w={w}:h={h}:x=0:y=0:exact=1,"
                     f"scale={w}:{h}:flags=lanczos,format={zoom['pix']}[vout]")
        vmap, vcodec = "[vout]", zoom["args"]
    if audios:
        labels = []
        for i, a in enumerate(audios, 1):
            chain = f"[{i}:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo"
            if a["denoise"]:
                chain += ",afftdn=nf=-25"
            chain += f",volume={a['gain']:.2f}[a{i}]"
            parts.append(chain)
            labels.append(f"[a{i}]")
        if len(audios) > 1:
            parts.append("".join(labels) + f"amix=inputs={len(audios)}:duration=longest:normalize=0[am]")
            last = "[am]"
        else:
            last = labels[0]
        parts.append(f"{last}alimiter=limit=0.97:level=false,apad[aout]")
        cmd += ["-filter_complex", ";".join(parts), "-map", vmap, "-map", "[aout]"] + vcodec
        cmd += ["-c:a", "aac", "-b:a", "192k", "-shortest"]
    else:
        if parts:
            cmd += ["-filter_complex", ";".join(parts)]
        cmd += ["-map", vmap] + vcodec
    if out.lower().endswith(".mp4"):
        cmd += ["-movflags", "+faststart"]
    cmd.append(out)
    r = run_quiet(cmd, timeout=6 * 3600 if zoom else 3600, cwd=tmp)
    if r.returncode != 0 or not os.path.exists(out):
        raise RuntimeError(r.stderr.decode("utf-8", "replace")[-1500:])


# ----------------------------------------------------------------------------- hotkeys & overlays

class HotkeyThread(threading.Thread):
    def __init__(self, q):
        super().__init__(daemon=True)
        self.q = q
        self.failed = []
        self.tid = None

    def run(self):
        self.tid = ctypes.windll.kernel32.GetCurrentThreadId()
        for hid, (mods, vk, name) in HOTKEYS.items():
            if not user32.RegisterHotKey(None, hid, mods | 0x4000, vk):
                self.failed.append(name)
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == 0x0312 and msg.wParam in HOTKEYS:
                self.q.put(HOTKEYS[msg.wParam][2])
        for hid in HOTKEYS:
            user32.UnregisterHotKey(None, hid)

    def stop(self):
        if self.tid:
            user32.PostThreadMessageW(self.tid, 0x0012, 0, 0)


class RegionSelector(tk.Toplevel):
    def __init__(self, master, done):
        super().__init__(master)
        self.done = done
        self.vx, self.vy, vw, vh = virtual_screen()
        self.overrideredirect(True)
        self.geometry(f"{vw}x{vh}+{self.vx}+{self.vy}")
        self.attributes("-alpha", 0.35)
        self.attributes("-topmost", True)
        self.c = tk.Canvas(self, bg="black", highlightthickness=0, cursor="crosshair")
        self.c.pack(fill="both", expand=True)
        pw = user32.GetSystemMetrics(0)
        self.c.create_text(-self.vx + pw // 2, -self.vy + 60, fill="white", font=("Segoe UI", 20, "bold"),
                           text="Drag to select the recording area  -  Esc to cancel")
        self.rect = self.label = None
        self.c.bind("<ButtonPress-1>", self.press)
        self.c.bind("<B1-Motion>", self.drag)
        self.c.bind("<ButtonRelease-1>", self.release)
        self.bind("<Escape>", lambda e: self.finish(None))
        self.after(50, self.focus_force)
        self.grab_set()

    def press(self, e):
        self.sx, self.sy = e.x, e.y
        self.rect = self.c.create_rectangle(e.x, e.y, e.x, e.y, outline="#ff3b30", width=3, fill="white")
        self.label = self.c.create_text(e.x, e.y - 14, fill="white", font=("Segoe UI", 12, "bold"), text="")

    def drag(self, e):
        if self.rect:
            self.c.coords(self.rect, self.sx, self.sy, e.x, e.y)
            self.c.coords(self.label, min(self.sx, e.x) + 60, min(self.sy, e.y) - 14)
            self.c.itemconfig(self.label, text=f"{abs(e.x - self.sx)} x {abs(e.y - self.sy)}")

    def release(self, e):
        if not self.rect:
            return
        x0, y0 = min(self.sx, e.x), min(self.sy, e.y)
        w, h = abs(e.x - self.sx), abs(e.y - self.sy)
        self.finish((self.vx + x0, self.vy + y0, w - w % 2, h - h % 2) if w >= 32 and h >= 32 else None)

    def finish(self, region):
        self.grab_release()
        self.destroy()
        self.done(region)


class CaptureFrame(tk.Toplevel):
    """oCam-style frame outlining the capture area.

    The inside is a transparent colour key, so it is see-through and click-through.
    The border and title bar sit just outside the area, so they never show up in the video.
    Drag edges/corners to resize, drag the bar to move, right-click for preset sizes.
    """
    B, BAR, GRIP, KEY = 5, 26, 20, "#010203"
    CURSORS = {"n": "size_ns", "s": "size_ns", "e": "size_we", "w": "size_we", "nw": "size_nw_se",
               "se": "size_nw_se", "ne": "size_ne_sw", "sw": "size_ne_sw", "move": "fleur"}

    def __init__(self, master, on_change, on_menu):
        super().__init__(master)
        self.on_change, self.on_menu = on_change, on_menu
        self.region = None
        self.locked = False
        self.drag = None
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.config(bg=self.KEY)
        self.attributes("-transparentcolor", self.KEY)
        self.c = tk.Canvas(self, bg=self.KEY, highlightthickness=0, bd=0)
        self.c.pack(fill="both", expand=True)
        self.c.bind("<Motion>", self._hover)
        self.c.bind("<ButtonPress-1>", self._press)
        self.c.bind("<B1-Motion>", self._move)
        self.c.bind("<ButtonRelease-1>", lambda e: setattr(self, "drag", None))
        self.c.bind("<Button-3>", lambda e: None if self.locked else self.on_menu(e.x_root, e.y_root))

    def set_region(self, region, locked=None):
        if locked is not None:
            self.locked = locked
        self.region = region
        x, y, w, h = region
        vx, vy, vw, vh = virtual_screen()
        B, BAR = self.B, self.BAR
        if y - B - BAR >= vy:
            self.bar = "top"
        elif y + h + B + BAR <= vy + vh:
            self.bar = "bottom"
        else:
            self.bar = None
        self.ix, self.iy = B, B + (BAR if self.bar == "top" else 0)
        ww, wh = w + 2 * B, h + 2 * B + (BAR if self.bar else 0)
        self.geometry(f"{ww}x{wh}+{x - self.ix}+{y - self.iy}")
        self._draw(ww, wh)

    def _draw(self, ww, wh):
        c = self.c
        c.delete("all")
        _, _, w, h = self.region
        ix, iy, B = self.ix, self.iy, self.B
        col = C_RED if self.locked else C_ACCENT
        for r in ((ix - B, iy - B, ix + w + B, iy), (ix - B, iy + h, ix + w + B, iy + h + B),
                  (ix - B, iy, ix, iy + h), (ix + w, iy, ix + w + B, iy + h)):
            c.create_rectangle(*r, fill=col, width=0)
        if self.bar:
            by = 0 if self.bar == "top" else iy + h + B
            c.create_rectangle(0, by, ww, by + self.BAR, fill=col, width=0)
            text = (f"● REC   {w} x {h}" if self.locked
                    else f"✥  {w} x {h}     drag to move · right-click for sizes")
            c.create_text(8, by + self.BAR // 2, anchor="w", fill="white", font=("Segoe UI", 10, "bold"), text=text)

    def _zone(self, x, y):
        x0, y0 = self.ix, self.iy
        x1, y1 = x0 + self.region[2], y0 + self.region[3]
        if y < y0 - self.B or y >= y1 + self.B:
            return "move"
        n, s, w, e = y < y0, y >= y1, x < x0, x >= x1
        g = self.GRIP
        if n or s:
            w, e = w or x < x0 + g, e or x >= x1 - g
        if w or e:
            n, s = n or y < y0 + g, s or y >= y1 - g
        return (("n" if n else "s" if s else "") + ("w" if w else "e" if e else "")) or "move"

    def _hover(self, e):
        self.c.config(cursor="" if self.locked else self.CURSORS[self._zone(e.x, e.y)])

    def _press(self, e):
        if not self.locked:
            self.drag = (self._zone(e.x, e.y), e.x_root, e.y_root, self.region)

    def _move(self, e):
        if not self.drag:
            return
        zone, sx, sy, (x, y, w, h) = self.drag
        dx, dy = e.x_root - sx, e.y_root - sy
        if zone == "move":
            x, y = x + dx, y + dy
        else:
            if "w" in zone:
                nw = max(MIN_SIZE, w - dx)
                x, w = x + w - nw, nw
            if "e" in zone:
                w = max(MIN_SIZE, w + dx)
            if "n" in zone:
                nh = max(MIN_SIZE, h - dy)
                y, h = y + h - nh, nh
            if "s" in zone:
                h = max(MIN_SIZE, h + dy)
        self.on_change((x, y, w, h))


class ZoomOverlay(tk.Toplevel):
    """Red rectangle showing the presenter what the viewer will see while zoomed.

    It is excluded from screen capture and click-through. If Windows cannot exclude it,
    it is never shown, so it can never end up in a recording.
    """
    B, KEY = 3, "#010203"

    def __init__(self, master):
        super().__init__(master)
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.config(bg=self.KEY)
        self.attributes("-transparentcolor", self.KEY)
        self.c = tk.Canvas(self, bg=self.KEY, highlightthickness=0, bd=0)
        self.c.pack(fill="both", expand=True)
        self.size = None
        self.usable = None
        self.withdraw()

    def _prepare(self):
        """Runs on first use: the native window only exists once Tk has mapped it (off-screen here)."""
        self.geometry("1x1+-32000+-32000")
        self.deiconify()
        self.update_idletasks()
        hwnd = int(self.wm_frame(), 16)
        WS_EX_TRANSPARENT, WS_EX_TOOLWINDOW, WS_EX_NOACTIVATE = 0x20, 0x80, 0x08000000
        user32.SetWindowLongW(hwnd, -20, user32.GetWindowLongW(hwnd, -20)
                              | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE)
        self.usable = bool(user32.SetWindowDisplayAffinity(hwnd, 0x11))  # WDA_EXCLUDEFROMCAPTURE
        self.withdraw()

    def show(self, x, y, w, h):
        if self.usable is None:
            self._prepare()
        if not self.usable:
            return
        x, y, w, h = int(x), int(y), max(16, int(w)), max(16, int(h))
        self.geometry(f"{w}x{h}+{x}+{y}")
        if self.size != (w, h):
            self.size = (w, h)
            self.c.delete("all")
            self.c.create_rectangle(1, 1, w - 2, h - 2, outline="#ff3b30", width=self.B)
        if self.state() != "normal":
            self.deiconify()

    def hide(self):
        if self.state() == "normal":
            self.withdraw()


class Countdown(tk.Toplevel):
    def __init__(self, master, n, done):
        super().__init__(master)
        self.n, self.done = n, done
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.attributes("-alpha", 0.9)
        self.lbl = tk.Label(self, text=str(n), font=("Segoe UI", 90, "bold"), fg="white", bg="#d32f2f", width=3)
        self.lbl.pack()
        self.update_idletasks()
        w, h = self.winfo_reqwidth(), self.winfo_reqheight()
        self.geometry(f"+{(user32.GetSystemMetrics(0) - w) // 2}+{(user32.GetSystemMetrics(1) - h) // 2}")
        self.step()

    def step(self):
        if self.n <= 0:
            self.destroy()
            self.master.after(200, self.done)  # let the overlay vanish before capture starts
            return
        self.lbl.config(text=str(self.n))
        self.n -= 1
        self.after(1000, self.step)


# ----------------------------------------------------------------------------- updates

def parse_version(text):
    nums = [int(n) for n in re.findall(r"\d+", text)[:3]]
    return tuple(nums + [0] * (3 - len(nums)))


def http_open(url):
    req = urllib.request.Request(url, headers={"User-Agent": f"ScreenRecorderPro/{APP_VERSION}"})
    return urllib.request.urlopen(req, timeout=20)


def newer_release():
    """The latest GitHub release if it is newer than this build, otherwise None."""
    try:
        with http_open(UPDATE_API) as r:
            data = json.load(r)
    except urllib.error.HTTPError as e:
        if e.code == 404:  # nothing published yet
            return None
        raise
    tag = data.get("tag_name", "")
    if parse_version(tag) <= parse_version(APP_VERSION):
        return None
    asset = next((a for a in data.get("assets", []) if a.get("name") == UPDATE_ASSET), {})
    return {"version": tag.lstrip("vV"), "notes": (data.get("body") or "").strip(),
            "url": asset.get("browser_download_url"), "size": asset.get("size", 0),
            "digest": asset.get("digest") or ""}


def download_update(info, dest, progress):
    """Downloads the new exe and refuses anything incomplete or altered."""
    sha, got, head = hashlib.sha256(), 0, b""
    try:
        with http_open(info["url"]) as r, open(dest, "wb") as f:
            while True:
                chunk = r.read(1024 * 1024)
                if not chunk:
                    break
                head = head or chunk[:2]
                f.write(chunk)
                sha.update(chunk)
                got += len(chunk)
                progress(got, info["size"])
        if got != info["size"] or head != b"MZ":
            raise RuntimeError("The download is incomplete.")
        if info["digest"].startswith("sha256:") and info["digest"][7:].lower() != sha.hexdigest():
            raise RuntimeError("The download failed its checksum.")
    except Exception:
        try:
            os.remove(dest)
        except OSError:
            pass
        raise


def install_update(exe, new):
    """Swaps in the new exe and starts it; puts the old one back if Windows refuses to run it.

    A running exe cannot be overwritten but it can be renamed, so no helper script is needed.
    """
    old = exe + ".old"
    if os.path.exists(old):
        os.remove(old)
    os.replace(exe, old)
    try:
        os.replace(new, exe)
        # without this the new onefile exe would reuse this process's unpacked files
        subprocess.Popen([exe], env=dict(os.environ, PYINSTALLER_RESET_ENVIRONMENT="1"))
    except Exception:
        if os.path.exists(exe):
            os.remove(exe)
        os.replace(old, exe)
        raise


# ----------------------------------------------------------------------------- GUI

_icons = {}


def icon(glyph, size=18, color=C_TEXT):
    """An icon from Windows' own symbol font; buttons simply go without one if the font is missing."""
    key = (glyph, size, color)
    if key not in _icons:
        try:
            scale = 4  # drawn large so it stays sharp on scaled displays
            font = ImageFont.truetype(os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts", "segmdl2.ttf"),
                                      size * scale)
            img = Image.new("RGBA", (size * scale, size * scale), (0, 0, 0, 0))
            ImageDraw.Draw(img).text((size * scale / 2, size * scale / 2), glyph, font=font, fill=color, anchor="mm")
            _icons[key] = ctk.CTkImage(light_image=img, dark_image=img, size=(size, size))
        except Exception:
            _icons[key] = None
    return _icons[key]


def load_settings():
    try:
        with open(SETTINGS_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def fmt_time(sec):
    sec = int(sec)
    return f"{sec // 3600:02d}:{sec % 3600 // 60:02d}:{sec % 60:02d}"


class App:
    def __init__(self, root):
        self.root = root
        self.s = load_settings()
        self.pa = pyaudio.PyAudio()
        self.monitors = get_monitors()
        self.encoders = ["x264"]
        self.sources = {}
        self.mic_devs, self.loop_devs = {}, {}
        if self.s.get("region"):
            self.region = clamp_region(self.s["region"])
        else:
            m = self.monitors[0]
            w, h = min(1280, m["w"]), min(720, m["h"])
            self.region = clamp_region((m["x"] + (m["w"] - w) // 2, m["y"] + (m["h"] - h) // 2, w, h))
        self.frame = None
        self.state = "idle"
        self.uiq = queue.Queue()
        self.hkq = queue.Queue()
        self.dxgi_failed = False
        self.active = 0.0
        self.seg_t0 = 0.0
        self.levels = {}
        self.zooms = []
        self.zoom_cur = None
        self.zoom_cam = ZoomCamera()
        self.zoom_t = 0.0
        self.update_info = None
        self.updating = False

        root.title(APP_NAME)
        root.geometry("800x650")
        root.resizable(False, False)
        try:
            root.iconbitmap(resource_path("icon.ico"))
        except Exception:
            pass
        self.build_ui()
        self.frame = CaptureFrame(root, lambda r: self.set_region(r, from_frame=True), self.preset_menu)
        self.frame.withdraw()
        self.zoom_overlay = ZoomOverlay(root)
        self.refresh_devices(reinit=False)
        self.set_region(self.region)
        self.on_mode()
        threading.Thread(target=self._detect_encoders, daemon=True).start()
        self.hotkeys = HotkeyThread(self.hkq)
        self.hotkeys.start()
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        root.after(50, self.tick)
        root.after(1500, self._check_hotkeys)
        root.after(3000, self.check_update)

    # ---------------------------------------------------------------- layout
    def build_ui(self):
        root = self.root
        root.configure(fg_color=C_BG)
        root.grid_columnconfigure(1, weight=1)
        root.grid_rowconfigure(0, weight=1)
        self.lock_widgets = []
        self.pages, self.nav = {}, {}
        self.build_sidebar()

        main = ctk.CTkFrame(root, fg_color="transparent")
        main.grid(row=0, column=1, sticky="nsew", padx=(0, 16), pady=(16, 8))
        main.grid_columnconfigure(0, weight=1)
        main.grid_rowconfigure(1, weight=1)
        self.build_header(main)
        holder = ctk.CTkFrame(main, fg_color=C_SURFACE, corner_radius=14)
        holder.grid(row=1, column=0, sticky="nsew", pady=12)
        holder.grid_columnconfigure(0, weight=1)
        holder.grid_rowconfigure(0, weight=1)
        for key, build in (("capture", self.build_capture_page), ("audio", self.build_audio_page),
                           ("quality", self.build_quality_page), ("zoom", self.build_zoom_page),
                           ("save", self.build_save_page)):
            page = ctk.CTkFrame(holder, fg_color="transparent")
            page.grid(row=0, column=0, sticky="nsew", padx=20, pady=16)
            page.grid_columnconfigure(0, weight=1)
            self.pages[key] = page
            build(page)
        self.build_actions(main)
        self.status = ctk.CTkLabel(main, text="", anchor="w", height=20, text_color=C_MUTED, font=self.font(12))
        self.status.grid(row=3, column=0, sticky="we", pady=(6, 0))
        self.show_page("capture")
        self.update_buttons()

    def font(self, size=13, bold=False):
        return ctk.CTkFont(family="Segoe UI", size=size, weight="bold" if bold else "normal")

    def build_sidebar(self):
        side = ctk.CTkFrame(self.root, fg_color=C_SURFACE, corner_radius=0, width=178)
        side.grid(row=0, column=0, sticky="nsew", padx=(0, 16))
        side.grid_propagate(False)
        side.grid_columnconfigure(0, weight=1)
        side.grid_rowconfigure(6, weight=1)
        brand = ctk.CTkFrame(side, fg_color="transparent")
        brand.grid(row=0, column=0, sticky="we", padx=20, pady=(20, 18))
        ctk.CTkLabel(brand, text="Screen Recorder", font=self.font(16, True), text_color=C_TEXT, anchor="w",
                     height=24).pack(fill="x")
        ctk.CTkLabel(brand, text="PRO", font=self.font(12, True), text_color=C_RED, anchor="w",
                     height=18).pack(fill="x")
        for r, (key, title, glyph) in enumerate((("capture", "Capture", ""), ("audio", "Audio", ""),
                                                 ("quality", "Video quality", ""), ("zoom", "Zoom", ""),
                                                 ("save", "Save", "")), 1):
            b = ctk.CTkButton(side, text=title, image=icon(glyph), compound="left", anchor="w", height=42,
                              corner_radius=10, font=self.font(14), text_color=C_MUTED, fg_color="transparent",
                              hover_color=C_FIELD, command=lambda k=key: self.show_page(k))
            b.grid(row=r, column=0, sticky="we", padx=10, pady=2)
            self.nav[key] = b
        self.b_update = ctk.CTkButton(side, text="Update", image=icon("", 16), compound="left", height=34,
                                      corner_radius=8, font=self.font(13, True), text_color=C_TEXT, fg_color=C_FIELD,
                                      hover_color=C_BORDER, command=self.on_update)
        self.b_update.grid(row=7, column=0, sticky="we", padx=14, pady=(0, 10))
        credit = ctk.CTkLabel(side, text=f"{COPYRIGHT}\nv{APP_VERSION}  ·  Free\nLicensed under AGPL-3.0",
                              font=self.font(11), text_color=C_MUTED, justify="center", cursor="hand2")
        credit.grid(row=8, column=0, sticky="we", padx=8, pady=(0, 14))
        credit.bind("<Button-1>", lambda e: webbrowser.open(REPO_URL))

    def build_header(self, parent):
        head = ctk.CTkFrame(parent, fg_color=C_SURFACE, corner_radius=14, height=78)
        head.grid(row=0, column=0, sticky="we")
        head.grid_propagate(False)
        head.grid_columnconfigure(1, weight=1)
        head.grid_rowconfigure(0, weight=1)
        self.dot = ctk.CTkLabel(head, text="●", font=self.font(30), text_color=C_IDLE)
        self.dot.grid(row=0, column=0, padx=(20, 8))
        self.timer = ctk.CTkLabel(head, text="00:00:00", font=ctk.CTkFont(family="Consolas", size=36, weight="bold"),
                                  text_color=C_TEXT, anchor="w")
        self.timer.grid(row=0, column=1, sticky="w")
        self.state_lbl = ctk.CTkLabel(head, text="Ready", font=self.font(13, True), text_color=C_TEXT,
                                      fg_color=C_FIELD, corner_radius=12, height=28, width=120)
        self.state_lbl.grid(row=0, column=2, padx=20)

    def build_actions(self, parent):
        bar = ctk.CTkFrame(parent, fg_color="transparent")
        bar.grid(row=2, column=0, sticky="we")
        bar.grid_columnconfigure(0, weight=2)
        bar.grid_columnconfigure((1, 2), weight=1, uniform="side")
        opts = {"height": 54, "corner_radius": 12, "text_color": "#ffffff", "text_color_disabled": C_MUTED,
                "compound": "left"}
        self.b_rec = ctk.CTkButton(bar, text="Record", image=icon("", 18, "#ffffff"), fg_color=C_RED,
                                   hover_color=C_RED_DARK, font=self.font(17, True), command=self.toggle_record, **opts)
        self.b_pause = ctk.CTkButton(bar, text="Pause", image=icon("", 16, "#ffffff"), fg_color=C_FIELD,
                                     hover_color=C_BORDER, font=self.font(14, True), command=self.toggle_pause, **opts)
        self.b_shot = ctk.CTkButton(bar, text="Screenshot", image=icon("", 16, "#ffffff"), fg_color=C_FIELD,
                                    hover_color=C_BORDER, font=self.font(14, True), command=self.screenshot, **opts)
        for i, (b, keys) in enumerate(((self.b_rec, "Ctrl + Alt + R"), (self.b_pause, "Ctrl + Alt + P"),
                                       (self.b_shot, "Ctrl + Alt + S"))):
            b.grid(row=0, column=i, sticky="we", padx=(0 if i == 0 else 10, 0))
            ctk.CTkLabel(bar, text=keys, font=self.font(11), text_color=C_MUTED, height=18).grid(
                row=1, column=i, padx=(0 if i == 0 else 10, 0), pady=(4, 0))

    # small building blocks for the pages
    def page_title(self, page, title, hint):
        ctk.CTkLabel(page, text=title, font=self.font(18, True), text_color=C_TEXT, anchor="w",
                     height=26).grid(row=0, column=0, sticky="we")
        ctk.CTkLabel(page, text=hint, font=self.font(12), text_color=C_MUTED, anchor="w",
                     height=20).grid(row=1, column=0, sticky="we", pady=(0, 12))

    def dropdown(self, parent, var, values, command=None):
        return ctk.CTkOptionMenu(parent, variable=var, values=values, command=command, height=34, corner_radius=8,
                                 fg_color=C_FIELD, button_color=C_FIELD, button_hover_color=C_BORDER,
                                 text_color=C_TEXT, dropdown_fg_color=C_FIELD, dropdown_text_color=C_TEXT,
                                 dropdown_hover_color=C_ACCENT, font=self.font(13), dropdown_font=self.font(13),
                                 dynamic_resizing=False)

    def segmented(self, parent, var, values, command=None):
        return ctk.CTkSegmentedButton(parent, variable=var, values=values, command=command, height=34,
                                      corner_radius=8, fg_color=C_FIELD, selected_color=C_ACCENT,
                                      selected_hover_color=C_ACCENT_DARK, unselected_color=C_FIELD,
                                      unselected_hover_color=C_BORDER, text_color=C_TEXT, font=self.font(13))

    def switch(self, parent, text, var, command=None):
        return ctk.CTkSwitch(parent, text=text, variable=var, command=command, progress_color=C_ACCENT,
                             fg_color=C_BORDER, text_color=C_TEXT, font=self.font(13))

    def button(self, parent, text, command, **kw):
        return ctk.CTkButton(parent, text=text, command=command, height=34, corner_radius=8, fg_color=C_FIELD,
                             hover_color=C_BORDER, text_color=C_TEXT, text_color_disabled=C_MUTED,
                             font=self.font(13), **kw)

    def field(self, parent, row, label, widget):
        ctk.CTkLabel(parent, text=label, font=self.font(13), text_color=C_MUTED, anchor="w",
                     width=92).grid(row=row, column=0, sticky="w", pady=6)
        widget.grid(row=row, column=1, sticky="we", pady=6)

    def build_capture_page(self, page):
        s = self.s
        self.page_title(page, "What do you want to record?", "Pick one, then press Record.")
        mode = s.get("capture_mode", MODE_REGION)
        self.mode = tk.StringVar(value=mode if mode in (MODE_REGION, MODE_MON, MODE_ALL, MODE_WIN) else MODE_REGION)
        cards = ctk.CTkFrame(page, fg_color="transparent")
        cards.grid(row=2, column=0, sticky="we")
        self.mode_cards = {}
        for i, (key, title, glyph) in enumerate(((MODE_REGION, "Area", ""), (MODE_MON, "Full screen", ""),
                                                 (MODE_WIN, "Window", ""), (MODE_ALL, "All screens", ""))):
            cards.grid_columnconfigure(i, weight=1, uniform="card")
            b = ctk.CTkButton(cards, text=title, image=icon(glyph, 26), compound="top", height=84, corner_radius=12,
                              font=self.font(13, True), text_color=C_TEXT, text_color_disabled=C_MUTED,
                              fg_color=C_FIELD, hover_color=C_BORDER, border_width=2, border_color=C_FIELD,
                              command=lambda k=key: self.pick_mode(k))
            b.grid(row=0, column=i, sticky="we", padx=(0 if i == 0 else 10, 0))
            self.mode_cards[key] = b
            self.lock_widgets.append(b)

        self.target = ctk.CTkFrame(page, fg_color="transparent")
        self.target.grid(row=3, column=0, sticky="we", pady=(14, 0))
        self.target.grid_columnconfigure(0, weight=1)

        self.mon_var = tk.StringVar()
        labels = self.monitor_labels()
        mi = s.get("monitor", 0)
        self.mon_var.set(labels[mi if mi < len(labels) else 0])
        self.mon_cb = self.dropdown(self.target, self.mon_var, labels)

        self.region_frame = ctk.CTkFrame(self.target, fg_color="transparent")
        self.region_frame.grid_columnconfigure(3, weight=1)
        self.rw, self.rh = tk.StringVar(), tk.StringVar()
        self.size_widgets = []
        for col, var in ((0, self.rw), (2, self.rh)):
            e = ctk.CTkEntry(self.region_frame, textvariable=var, width=74, height=34, corner_radius=8, justify="center",
                             fg_color=C_FIELD, border_color=C_BORDER, text_color=C_TEXT, font=self.font(13))
            e.grid(row=0, column=col, sticky="w")
            e.bind("<Return>", lambda ev: self.apply_size())
            e.bind("<FocusOut>", lambda ev: self.apply_size())
            self.size_widgets.append(e)
        ctk.CTkLabel(self.region_frame, text="×", font=self.font(14), text_color=C_MUTED, width=22).grid(row=0, column=1)
        self.preset = tk.StringVar(value="Size presets...")
        pc = self.dropdown(self.region_frame, self.preset, list(PRESETS), self.apply_preset)
        pc.grid(row=0, column=3, sticky="we", padx=(10, 0))
        self.size_widgets.append(pc)
        b = self.button(self.region_frame, "Drag to select area", self.select_region)
        b.grid(row=1, column=0, columnspan=3, sticky="we", pady=(10, 0))
        self.size_widgets.append(b)
        self.show_frame = tk.BooleanVar(value=s.get("show_frame", True))
        self.switch(self.region_frame, "Show frame on screen", self.show_frame, self.on_mode).grid(
            row=1, column=3, sticky="w", padx=(14, 0), pady=(10, 0))

        self.win_frame = ctk.CTkFrame(self.target, fg_color="transparent")
        self.win_frame.grid_columnconfigure(0, weight=1)
        self.win_var = tk.StringVar(value=s.get("window", ""))
        self.win_cb = ctk.CTkComboBox(self.win_frame, variable=self.win_var, values=[], height=34, corner_radius=8,
                                      fg_color=C_FIELD, border_color=C_BORDER, button_color=C_FIELD,
                                      button_hover_color=C_BORDER, text_color=C_TEXT, dropdown_fg_color=C_FIELD,
                                      dropdown_text_color=C_TEXT, dropdown_hover_color=C_ACCENT, font=self.font(13))
        self.win_cb.grid(row=0, column=0, sticky="we")
        self.button(self.win_frame, "", self.refresh_windows, width=38, image=icon("", 16)).grid(
            row=0, column=1, padx=(8, 0))
        self.all_lbl = ctk.CTkLabel(self.target, text="Records every monitor together.", font=self.font(13),
                                    text_color=C_MUTED, anchor="w", height=34)

        self.cursor = tk.BooleanVar(value=s.get("cursor", True))
        self.countdown = tk.StringVar(value=str(s.get("countdown", 3)))
        extra = ctk.CTkFrame(page, fg_color="transparent")
        extra.grid(row=4, column=0, sticky="we", pady=(16, 0))
        extra.grid_columnconfigure(1, weight=1)
        self.switch(extra, "Show mouse cursor", self.cursor).grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(extra, text="Countdown", font=self.font(13), text_color=C_MUTED).grid(row=0, column=2, padx=(0, 10))
        self.segmented(extra, self.countdown, ["0", "3", "5", "10"]).grid(row=0, column=3)

    def build_audio_page(self, page):
        s = self.s
        self.page_title(page, "Sound", "Turn on what you want in the recording. The bars move when sound is coming in.")
        self.rows = {}
        for r, (key, title, default_on, glyph) in enumerate((("mic1", "Microphone", True, ""),
                                                             ("mic2", "Microphone 2", False, ""),
                                                             ("sys", "System sound", True, "")), 2):
            cfg = s.get("audio", {}).get(key, {})
            row = {"en": tk.BooleanVar(value=cfg.get("enabled", default_on)), "dev": tk.StringVar(),
                   "gain": tk.IntVar(value=cfg.get("gain", 100)), "saved": cfg.get("device")}
            box = ctk.CTkFrame(page, fg_color=C_BG, corner_radius=10)
            box.grid(row=r, column=0, sticky="we", pady=(0, 8))
            box.grid_columnconfigure(1, weight=1)
            sw = self.switch(box, title, row["en"], lambda k=key: self.update_source(k))
            sw.configure(width=150)
            sw.grid(row=0, column=0, sticky="w", padx=12, pady=(10, 4))
            row["cb"] = self.dropdown(box, row["dev"], [], lambda v, k=key: self.update_source(k))
            row["cb"].grid(row=0, column=1, columnspan=2, sticky="we", padx=(0, 12), pady=(10, 4))
            row["meter"] = tk.Canvas(box, width=130, height=8, bg=C_FIELD, highlightthickness=0)
            row["meter"].grid(row=1, column=0, sticky="we", padx=12, pady=(4, 12))
            row["bar"] = row["meter"].create_rectangle(0, 0, 0, 8, fill=C_GREEN, width=0)
            ctk.CTkSlider(box, from_=0, to=300, number_of_steps=60, variable=row["gain"], height=16, progress_color=C_ACCENT,
                          button_color=C_TEXT, button_hover_color=C_MUTED, fg_color=C_FIELD,
                          command=lambda v, rw=row: rw["gl"].configure(text=f"{int(v)}%")).grid(
                row=1, column=1, sticky="we", pady=(4, 12))
            row["gl"] = ctk.CTkLabel(box, text=f"{row['gain'].get()}%", width=48, height=18, font=self.font(12),
                                     text_color=C_MUTED)
            row["gl"].grid(row=1, column=2, padx=(4, 8), pady=(4, 12))
            self.rows[key] = row
        opt = ctk.CTkFrame(page, fg_color="transparent")
        opt.grid(row=5, column=0, sticky="we", pady=(4, 0))
        opt.grid_columnconfigure(2, weight=1)
        self.denoise = tk.BooleanVar(value=s.get("denoise", False))
        self.all_apis = tk.BooleanVar(value=s.get("all_apis", False))
        self.switch(opt, "Reduce mic noise", self.denoise).grid(row=0, column=0, sticky="w", padx=(0, 14))
        self.switch(opt, "All audio drivers", self.all_apis, lambda: self.refresh_devices(reinit=False)).grid(
            row=0, column=1, sticky="w")
        self.button(opt, "Refresh", lambda: self.refresh_devices(reinit=True), width=90).grid(row=0, column=3, sticky="e")

    def build_quality_page(self, page):
        s = self.s
        self.page_title(page, "Video quality", "The defaults work well for lessons.")
        self.fps = tk.StringVar(value=str(s.get("fps", 30)))
        self.quality = tk.StringVar(value=s.get("quality", "High"))
        self.res = tk.StringVar(value=s.get("res", "Original"))
        self.fmt = tk.StringVar(value=s.get("fmt", "mp4"))
        self.enc = tk.StringVar(value="Auto (best available)")
        self.engine = tk.StringVar(value=s.get("engine", ENGINE_AUTO))
        form = ctk.CTkFrame(page, fg_color="transparent")
        form.grid(row=2, column=0, sticky="we")
        form.grid_columnconfigure(1, weight=1)
        self.field(form, 0, "Quality", self.segmented(form, self.quality, QUALITIES))
        self.field(form, 1, "FPS", self.segmented(form, self.fps, ["10", "15", "24", "30", "50", "60"]))
        self.field(form, 2, "Resolution", self.segmented(form, self.res, list(RESOLUTIONS)))
        self.field(form, 3, "Format", self.segmented(form, self.fmt, ["mp4", "mkv"]))
        self.enc_cb = self.dropdown(form, self.enc, ["Auto (best available)", "Detecting GPU encoders..."])
        self.field(form, 4, "Encoder", self.enc_cb)
        self.field(form, 5, "Engine", self.dropdown(form, self.engine, [ENGINE_AUTO, ENGINE_GDI]))

    def build_zoom_page(self, page):
        s = self.s
        self.page_title(page, "Zoom on key points", "Press the shortcut while recording. The zoom appears in the saved video.")
        zoom = s.get("zoom_level", "2x")
        self.zoom_level = tk.StringVar(value=zoom if zoom in ZOOM_LEVELS else "2x")
        self.zoom_follow = tk.BooleanVar(value=s.get("zoom_follow", True))
        form = ctk.CTkFrame(page, fg_color="transparent")
        form.grid(row=2, column=0, sticky="we")
        form.grid_columnconfigure(1, weight=1)
        self.field(form, 0, "Zoom level", self.segmented(form, self.zoom_level, list(ZOOM_LEVELS)))
        self.switch(page, "Follow the mouse while zoomed", self.zoom_follow).grid(row=3, column=0, sticky="w", pady=(10, 16))
        key = ctk.CTkFrame(page, fg_color=C_BG, corner_radius=10)
        key.grid(row=4, column=0, sticky="we")
        ctk.CTkLabel(key, text="Ctrl + Alt + Z", font=self.font(20, True), text_color=C_TEXT).pack(pady=(14, 2))
        ctk.CTkLabel(key, text="Press once on a point to zoom in, press again to zoom out.\n"
                               "A red box shows what viewers will see. It is not recorded.",
                     font=self.font(12), text_color=C_MUTED, justify="center").pack(pady=(0, 14))

    def build_save_page(self, page):
        s = self.s
        self.page_title(page, "Save", "Where your recordings go and what happens around them.")
        self.folder = tk.StringVar(value=s.get("folder", DEFAULT_OUT))
        self.minimize = tk.BooleanVar(value=s.get("minimize", True))
        self.open_after = tk.BooleanVar(value=s.get("open_after", True))
        row = ctk.CTkFrame(page, fg_color="transparent")
        row.grid(row=2, column=0, sticky="we")
        row.grid_columnconfigure(0, weight=1)
        ctk.CTkEntry(row, textvariable=self.folder, height=34, corner_radius=8, fg_color=C_FIELD, border_color=C_BORDER,
                     text_color=C_TEXT, font=self.font(13)).grid(row=0, column=0, sticky="we")
        self.button(row, "Browse", self.browse, width=84).grid(row=0, column=1, padx=(8, 0))
        self.button(row, "Open", self.open_folder, width=70).grid(row=0, column=2, padx=(8, 0))
        self.switch(page, "Minimize this window while recording", self.minimize).grid(row=3, column=0, sticky="w", pady=(18, 10))
        self.switch(page, "Show the file when saved", self.open_after).grid(row=4, column=0, sticky="w")

    def show_page(self, key):
        for name, page in self.pages.items():
            if name == key:
                page.tkraise()
            on = name == key
            self.nav[name].configure(fg_color=C_FIELD if on else "transparent", text_color=C_TEXT if on else C_MUTED)

    def pick_mode(self, key):
        self.mode.set(key)
        self.on_mode()

    def monitor_index(self):
        labels = self.monitor_labels()
        return labels.index(self.mon_var.get()) if self.mon_var.get() in labels else 0

    def monitor_labels(self):
        return [f"Monitor {i + 1}:  {m['w']}x{m['h']}" + ("  (primary)" if m["primary"] else "")
                for i, m in enumerate(self.monitors)]

    def on_mode(self):
        for w in (self.mon_cb, self.region_frame, self.win_frame, self.all_lbl):
            w.pack_forget()
        mode = self.mode.get()
        if mode == MODE_MON:
            self.mon_cb.pack(fill="x")
        elif mode == MODE_REGION:
            self.region_frame.pack(fill="x")
        elif mode == MODE_WIN:
            self.refresh_windows()
            self.win_frame.pack(fill="x")
        else:
            self.all_lbl.pack(fill="x")
        for key, card in self.mode_cards.items():
            card.configure(border_color=C_ACCENT if key == mode else C_FIELD)
        if self.frame:
            if mode == MODE_REGION and self.show_frame.get():
                self.frame.deiconify()
                self.frame.set_region(self.region)
            else:
                self.frame.withdraw()

    def set_region(self, region, from_frame=False):
        if self.state != "idle":
            return
        self.region = clamp_region(region)
        self.rw.set(str(self.region[2]))
        self.rh.set(str(self.region[3]))
        if from_frame:
            self.preset.set("Size presets...")
        if self.frame:
            self.frame.set_region(self.region)

    def apply_size(self):
        try:
            w, h = int(self.rw.get()), int(self.rh.get())
        except ValueError:
            w, h = self.region[2], self.region[3]
        x, y = self.region[:2]
        self.set_region((x, y, w, h))

    def monitor_at(self, region):
        cx, cy = region[0] + region[2] // 2, region[1] + region[3] // 2
        for m in self.monitors:
            if m["x"] <= cx < m["x"] + m["w"] and m["y"] <= cy < m["y"] + m["h"]:
                return m
        return self.monitors[0]

    def apply_preset(self, label):
        if label not in PRESETS:
            return
        m = self.monitor_at(self.region)
        size = PRESETS[label]
        if size is None:
            self.set_region((m["x"], m["y"], m["w"], m["h"]))
            return
        w, h = min(size[0], m["w"]), min(size[1], m["h"])
        x, y, cw, ch = self.region
        x, y = x + (cw - w) // 2, y + (ch - h) // 2
        # keep the area on the same monitor when possible (enables fast DXGI capture)
        x = min(max(x, m["x"]), m["x"] + m["w"] - w)
        y = min(max(y, m["y"]), m["y"] + m["h"] - h)
        self.set_region((x, y, w, h))

    def preset_menu(self, x, y):
        menu = tk.Menu(self.root, tearoff=0)
        for label in PRESETS:
            menu.add_command(label=label, command=lambda l=label: (self.preset.set(l), self.apply_preset(l)))
        menu.add_separator()
        menu.add_command(label="Center on monitor", command=self.center_region)
        menu.add_command(label="Hide frame", command=lambda: (self.show_frame.set(False), self.on_mode()))
        menu.tk_popup(x, y)

    def center_region(self):
        m = self.monitor_at(self.region)
        _, _, w, h = self.region
        self.set_region((m["x"] + (m["w"] - w) // 2, m["y"] + (m["h"] - h) // 2, w, h))

    def select_region(self):
        self.root.withdraw()
        self.frame.withdraw()

        def done(region):
            self.root.deiconify()
            if region:
                self.set_region(region)
            self.on_mode()

        self.root.after(200, lambda: RegionSelector(self.root, done))

    def refresh_windows(self):
        titles = get_windows(exclude=APP_NAME)
        self.win_cb.configure(values=titles)
        if titles and self.win_var.get() not in titles:
            self.win_var.set(titles[0])

    def browse(self):
        d = filedialog.askdirectory(initialdir=self.folder.get() or DEFAULT_OUT)
        if d:
            self.folder.set(os.path.normpath(d))

    def open_folder(self):
        os.makedirs(self.folder.get(), exist_ok=True)
        os.startfile(self.folder.get())

    def set_status(self, text, error=False):
        self.status.configure(text=text, text_color=C_RED if error else C_MUTED)

    def post(self, fn):
        self.uiq.put(fn)

    # ---------------------------------------------------------------- encoders
    def _detect_encoders(self):
        found = detect_encoders()

        def apply():
            self.encoders = found
            self.enc_cb.configure(values=["Auto (best available)"] + [ENC_LABEL[k] for k in found])
            saved = self.s.get("encoder")
            if saved in found:
                self.enc.set(ENC_LABEL[saved])
            gpu = [ENC_LABEL[k] for k in found if k != "x264"]
            self.set_status("GPU encoder: " + (", ".join(gpu) if gpu else "none (using CPU)"))
        self.post(apply)

    def chosen_encoder(self):
        for k, label, _ in ENCODERS:
            if self.enc.get() == label and k in self.encoders:
                return k
        return self.encoders[0]

    # ---------------------------------------------------------------- audio devices
    def refresh_devices(self, reinit=True):
        if self.state != "idle":
            return
        for k in list(self.sources):
            self.sources.pop(k).close()
        if reinit:
            for row in self.rows.values():
                row["saved"] = row["dev"].get() or row["saved"]
            self.pa.terminate()
            self.pa = pyaudio.PyAudio()
        pa = self.pa
        try:
            wasapi = pa.get_host_api_info_by_type(pyaudio.paWASAPI)
        except Exception:
            wasapi = None
        self.mic_devs, self.loop_devs = {}, {}
        default_mic = default_loop = None
        for d in pa.get_device_info_generator():
            if d["maxInputChannels"] <= 0:
                continue
            api = pa.get_host_api_info_by_index(d["hostApi"])["name"]
            if d.get("isLoopbackDevice"):
                self.loop_devs[d["name"].replace(" [Loopback]", "")] = d
                continue
            is_wasapi = wasapi is not None and d["hostApi"] == wasapi["index"]
            if self.all_apis.get() or is_wasapi or wasapi is None:
                label = d["name"] if (is_wasapi and not self.all_apis.get()) else f"{d['name']}  [{api}]"
                self.mic_devs[label] = d
                if wasapi is not None and d["index"] == wasapi["defaultInputDevice"]:
                    default_mic = label
        try:
            default_loop = pa.get_default_wasapi_loopback()["name"].replace(" [Loopback]", "")
        except Exception:
            pass
        mics, loops = list(self.mic_devs), list(self.loop_devs)
        for key, row in self.rows.items():
            options = loops if key == "sys" else mics
            row["cb"].configure(values=options)
            default = default_loop if key == "sys" else default_mic
            pick = row["saved"] if row["saved"] in options else (default if default in options else (options[0] if options else ""))
            if key == "mic2" and pick == self.rows["mic1"]["dev"].get() and len(options) > 1:
                pick = next(o for o in options if o != pick)
            row["dev"].set(pick)
            self.update_source(key)
        self.set_status(f"Found {len(mics)} microphone(s) and {len(loops)} playback device(s)")

    def update_source(self, key):
        if self.state != "idle":
            return
        row = self.rows[key]
        old = self.sources.pop(key, None)
        if old:
            old.close()
        self.levels[key] = 0.0
        if not row["en"].get():
            return
        info = (self.loop_devs if key == "sys" else self.mic_devs).get(row["dev"].get())
        if not info:
            return
        try:
            self.sources[key] = AudioSource(self.pa, info)
        except Exception as e:
            self.set_status(str(e), error=True)

    # ---------------------------------------------------------------- main loop
    def tick(self):
        while not self.uiq.empty():
            try:
                self.uiq.get_nowait()()
            except Exception as e:
                self.set_status(f"Error: {e}", error=True)
        while not self.hkq.empty():
            action = self.hkq.get_nowait()
            {"rec": self.toggle_record, "pause": self.toggle_pause, "shot": self.screenshot,
             "zoom": self.toggle_zoom}[action]()
        self.zoom_tick()
        for key, row in self.rows.items():
            src = self.sources.get(key)
            lvl = 0.0
            if src:
                lvl = min(1.0, src.peak * row["gain"].get() / 100.0)
                src.peak = 0.0
            lvl = max(lvl, self.levels.get(key, 0.0) * 0.85)
            self.levels[key] = lvl
            color = C_GREEN if lvl < 0.7 else (C_AMBER if lvl < 0.95 else C_RED)
            row["meter"].coords(row["bar"], 0, 0, int(row["meter"].winfo_width() * lvl), 10)
            row["meter"].itemconfig(row["bar"], fill=color)
        if self.state in ("recording", "paused", "busy"):
            el = self.active + (time.perf_counter() - self.seg_t0 if self.state == "recording" else 0)
            self.timer.configure(text=fmt_time(el))
            if self.state == "recording":
                self.dot.configure(text_color=C_RED if int(time.time() * 2) % 2 else C_RED_DARK)
        self.root.after(50, self.tick)

    def _check_hotkeys(self):
        if self.hotkeys.failed:
            self.set_status("Some hotkeys are used by another app: " + ", ".join(self.hotkeys.failed), error=True)

    def update_buttons(self):
        st = self.state
        active = st in ("recording", "paused", "busy")
        self.b_rec.configure(text="Stop" if active else "Record", image=icon("\ue71a" if active else "\ue7c8", 18, "#ffffff"),
                             fg_color=C_FIELD if active else C_RED, hover_color=C_BORDER if active else C_RED_DARK,
                             state="disabled" if st in ("starting", "saving", "busy") else "normal")
        self.b_pause.configure(text="Resume" if st == "paused" else "Pause",
                               image=icon("\ue768" if st == "paused" else "\ue769", 16, "#ffffff"),
                               state="normal" if st in ("recording", "paused") else "disabled")
        labels = {"idle": "Ready", "starting": "Starting...", "recording": "Recording",
                  "paused": "Paused", "busy": "Please wait...", "saving": "Saving..."}
        self.state_lbl.configure(text=labels.get(st, st), fg_color=C_RED if st == "recording" else C_FIELD)
        dot = {"paused": C_AMBER, "saving": C_ACCENT, "idle": C_IDLE}.get(st)
        if dot:
            self.dot.configure(text_color=dot)
        lock = "disabled" if st != "idle" else "normal"
        for row in self.rows.values():
            row["cb"].configure(state=lock)
        for w in self.size_widgets + self.lock_widgets:
            w.configure(state=lock)
        if self.frame and self.frame.region:
            self.frame.set_region(self.region, locked=st != "idle")

    # ---------------------------------------------------------------- capture config
    def capture_config(self):
        mode = self.mode.get()
        cap = {"fps": int(self.fps.get()), "cursor": self.cursor.get(), "title": None, "rect": None, "dxgi": None}
        if mode == MODE_MON:
            m = self.monitors[self.monitor_index()]
            cap["rect"] = (m["x"], m["y"], m["w"], m["h"])
            if m["dxgi"] is not None:
                cap["dxgi"] = (m["dxgi"], 0, 0, 0, 0)
        elif mode == MODE_REGION:
            self.apply_size()
            x, y, w, h = self.region
            cap["rect"] = self.region
            for m in self.monitors:
                if m["dxgi"] is not None and m["x"] <= x and m["y"] <= y and x + w <= m["x"] + m["w"] and y + h <= m["y"] + m["h"]:
                    cap["dxgi"] = (m["dxgi"], x - m["x"], y - m["y"], w, h)
        elif mode == MODE_WIN:
            title = self.win_var.get().strip()
            if not title:
                raise ValueError("Please choose a window to record.")
            cap["title"] = title
        if self.engine.get() == ENGINE_GDI:
            cap["dxgi"] = None
        return cap

    def snapshot(self):
        folder = self.folder.get().strip() or DEFAULT_OUT
        os.makedirs(folder, exist_ok=True)
        return {"cap": self.capture_config(), "folder": folder, "fmt": self.fmt.get(),
                "quality": self.quality.get(), "res": RESOLUTIONS.get(self.res.get(), 0),
                "encoder": self.chosen_encoder()}

    def video_cmd(self, cfg, path, engine, enc):
        cmd, vf = build_capture_cmd(cfg["cap"], engine)
        if cfg["res"]:
            vf.append(f"scale=-2:'min({cfg['res']},ih)':flags=bicubic")
        args, pix = encoder_args(enc, cfg["quality"], cfg["cap"]["fps"])
        vf.append(f"format={pix}")
        return cmd + ["-vf", ",".join(vf)] + args + ["-an", path]

    # ---------------------------------------------------------------- recording control
    def toggle_record(self):
        if self.state == "idle":
            self.start()
        elif self.state in ("recording", "paused"):
            self.stop()

    def start(self):
        try:
            cfg = self.snapshot()
        except Exception as e:
            messagebox.showwarning(APP_NAME, str(e))
            return
        self.save_settings()
        self.state = "starting"
        self.update_buttons()
        self.timer.configure(text="00:00:00")
        if self.minimize.get():
            self.root.iconify()
        n = int(self.countdown.get() or 0)
        go = lambda: threading.Thread(target=self._start_worker, args=(cfg,), daemon=True).start()
        if n:
            Countdown(self.root, n, go)
        else:
            self.root.after(300 if self.minimize.get() else 0, go)

    def _start_worker(self, cfg):
        try:
            self.cfg = cfg
            self.tmp = tempfile.mkdtemp(prefix="screenrec_")
            self.segs = []
            self.seg_lens = []
            self.active = 0.0
            self.zooms, self.zoom_cur, self.zoom_cam = [], None, ZoomCamera()
            self._start_segment()
            for key, src in self.sources.items():
                src.start_writing(os.path.join(self.tmp, f"{key}.wav"))
            self.seg_t0 = time.perf_counter()
            self.state = "recording"
            self.post(self.update_buttons)
            self.post(lambda: self.set_status(f"Recording with {ENC_LABEL[self.cfg['encoder']]}"
                                              f" · {len(self.sources)} audio source(s)"))
        except Exception as e:
            self.state = "idle"
            msg = str(e)
            self.post(lambda: self._fail(msg))

    def _start_segment(self):
        cfg = self.cfg
        path = os.path.join(self.tmp, f"seg{len(self.segs):03d}.mkv")
        engines = ["dxgi", "gdi"] if cfg["cap"]["dxgi"] and not self.dxgi_failed else ["gdi"]
        encs = list(dict.fromkeys([cfg["encoder"], "x264"]))
        log = ""
        for eng in engines:
            for enc in encs:
                proc = FFProc(self.video_cmd(cfg, path, eng, enc))
                if proc.wait_started(15):
                    self.seg = proc
                    self.segs.append(path)
                    cfg["encoder"] = enc
                    if eng == "gdi" and cfg["cap"]["dxgi"]:
                        self.dxgi_failed = True
                    return
                proc.kill()
                log = proc.tail()
        raise RuntimeError("FFmpeg could not start the screen capture.\n\n" + log[-900:])

    # ---------------------------------------------------------------- zoom
    def video_time(self):
        return self.active + (time.perf_counter() - self.seg_t0 if self.state == "recording" else 0.0)

    def toggle_zoom(self):
        if self.state != "recording":
            self.set_status("Zoom works while recording" + (" - resume first" if self.state == "paused" else ""))
        elif self.zoom_cur:
            self.end_zoom()
            self.set_status("Zoom out")
        else:
            x, y = cursor_fraction(capture_rect(self.cfg["cap"]))
            t = self.video_time()
            self.zoom_cur = {"start": t, "end": None, "level": ZOOM_LEVELS[self.zoom_level.get()],
                             "follow": self.zoom_follow.get(), "pts": [(t, x, y)]}
            self.zooms.append(self.zoom_cur)
            self.zoom_t = time.perf_counter()
            self.set_status(f"Zoom in {self.zoom_level.get()} - press Ctrl+Alt+Z to zoom out")

    def end_zoom(self):
        self.zoom_cur["end"] = self.video_time()
        self.zoom_cur = None

    def zoom_tick(self):
        """Follows the cursor while zoomed and keeps the on-screen rectangle up to date."""
        cam, z = self.zoom_cam, self.zoom_cur
        if self.state != "recording" or not (z or cam.p):
            self.zoom_overlay.hide()
            return
        now = time.perf_counter()
        dt, self.zoom_t = min(0.2, now - self.zoom_t), now  # a pause must not jump the camera
        rect = capture_rect(self.cfg["cap"])
        if z:
            x, y = cursor_fraction(rect)
            z["pts"].append((self.video_time(), x, y))
            cam.step(dt, True, z["level"], x, y, z["follow"])
        else:
            cam.step(dt, False, 0, 0, 0, False)
        if cam.p > 0.0:
            fx, fy, fw, fh = cam.view()
            self.zoom_overlay.show(rect[0] + fx * rect[2], rect[1] + fy * rect[3], fw * rect[2], fh * rect[3])
        else:
            self.zoom_overlay.hide()

    def save_video(self, audios, out):
        """Writes the final file; returns a warning if the zoom could not be applied."""
        cfg = self.cfg
        if not self.zooms:
            mux(self.segs, self.seg_lens, audios, out, self.tmp)
            return None
        self.post(lambda: self.state_lbl.configure(text="Applying zoom..."))
        err = ""
        try:
            size = video_size(self.segs[0])
            lines = zoom_commands(self.zooms, cfg["cap"]["fps"], *size)
            with open(os.path.join(self.tmp, "zoom.cmd"), "w", encoding="ascii") as f:
                f.write("\n".join(lines) + "\n")
            for enc in dict.fromkeys([cfg["encoder"], "x264"]):
                args, pix = encoder_args(enc, cfg["quality"], cfg["cap"]["fps"])
                try:
                    mux(self.segs, self.seg_lens, audios, out, self.tmp,
                        {"script": "zoom.cmd", "size": size, "args": args, "pix": pix})
                    return None
                except Exception as e:
                    err = str(e)
        except Exception as e:
            err = str(e)
        mux(self.segs, self.seg_lens, audios, out, self.tmp)
        return "Saved without zoom: " + (err.strip().splitlines() or ["unknown error"])[-1][:120]

    def end_segment(self):
        length = time.perf_counter() - self.seg_t0
        self.seg_lens.append(length)
        self.active += length

    def _fail(self, msg):
        self.update_buttons()
        self.root.deiconify()
        self.set_status("Recording failed", error=True)
        messagebox.showerror(APP_NAME, msg)

    def toggle_pause(self):
        if self.state == "recording":
            self.state = "busy"
            for src in self.sources.values():
                src.pause()
            self.end_segment()
            self.update_buttons()

            def work():
                self.seg.stop()
                self.state = "paused"
                self.post(self.update_buttons)
            threading.Thread(target=work, daemon=True).start()
        elif self.state == "paused":
            self.state = "busy"
            self.update_buttons()

            def work():
                try:
                    self._start_segment()
                except Exception as e:
                    self.state = "paused"
                    msg = str(e)
                    self.post(lambda: messagebox.showerror(APP_NAME, msg))
                    self.post(self.update_buttons)
                    return
                for src in self.sources.values():
                    src.resume()
                self.seg_t0 = time.perf_counter()
                self.state = "recording"
                self.post(self.update_buttons)
            threading.Thread(target=work, daemon=True).start()

    def stop(self):
        was = self.state
        self.state = "saving"
        if was == "recording":
            self.end_segment()
        if self.zoom_cur:
            self.end_zoom()
        self.update_buttons()
        self.timer.configure(text=fmt_time(self.active))
        gains = {k: row["gain"].get() / 100.0 for k, row in self.rows.items()}
        denoise = self.denoise.get()
        cfg = self.cfg
        name = datetime.datetime.now().strftime("Recording_%Y-%m-%d_%H-%M-%S") + "." + cfg["fmt"]
        out = os.path.join(cfg["folder"], name)

        def work():
            audios = []
            for key, src in self.sources.items():
                path = src.stop_writing()
                if path:
                    audios.append({"path": path, "gain": gains[key], "denoise": denoise and key != "sys"})
            if was == "recording":
                self.seg.stop()
            try:
                warning = self.save_video(audios, out)
                shutil.rmtree(self.tmp, ignore_errors=True)
                self.post(lambda: self._saved(out, warning))
            except Exception as e:
                msg = f"Could not save the video.\nRaw files kept in:\n{self.tmp}\n\n{e}"
                self.post(lambda: self._fail(msg))
            self.state = "idle"
            self.post(self.update_buttons)
        threading.Thread(target=work, daemon=True).start()

    def _saved(self, out, warning=None):
        self.root.deiconify()
        size = os.path.getsize(out) / (1024 * 1024)
        if warning:
            self.set_status(warning, error=True)
        else:
            self.set_status(f"Saved: {os.path.basename(out)}  ({size:.1f} MB)")
        if self.open_after.get():
            subprocess.Popen(["explorer", "/select,", out])

    # ---------------------------------------------------------------- screenshot
    def screenshot(self):
        try:
            cap = self.capture_config()
            folder = self.folder.get().strip() or DEFAULT_OUT
            os.makedirs(folder, exist_ok=True)
        except Exception as e:
            messagebox.showwarning(APP_NAME, str(e))
            return
        out = os.path.join(folder, datetime.datetime.now().strftime("Screenshot_%Y-%m-%d_%H-%M-%S.png"))
        hide = self.state == "idle" and self.root.state() == "normal"

        def take():
            c, vf = build_capture_cmd(dict(cap, fps=10), "gdi")
            r = run_quiet(c + ["-vf", ",".join(vf), "-frames:v", "1", out])
            ok = r.returncode == 0 and os.path.exists(out)

            def done():
                if hide:
                    self.root.deiconify()
                if ok:
                    self.set_status(f"Screenshot saved: {os.path.basename(out)}")
                else:
                    self.set_status("Screenshot failed", error=True)
            self.post(done)

        if hide:
            self.root.withdraw()
            self.root.after(300, lambda: threading.Thread(target=take, daemon=True).start())
        else:
            threading.Thread(target=take, daemon=True).start()

    # ---------------------------------------------------------------- updates
    def check_update(self, manual=False):
        def work():
            exe = sys.executable + ".old"  # left behind by the previous update
            if getattr(sys, "frozen", False) and os.path.exists(exe):
                try:
                    os.remove(exe)
                except OSError:
                    pass
            try:
                info = newer_release()
            except Exception:
                if manual:
                    self.post(lambda: self.set_status("Could not check for updates", error=True))
                return
            self.post(lambda: self._update_checked(info, manual))
        threading.Thread(target=work, daemon=True).start()

    def _update_checked(self, info, manual):
        if info:
            self.update_info = info
            self.b_update.configure(text=f"Update to v{info['version']}", fg_color=C_GREEN, hover_color=C_GREEN_DARK)
            if self.state == "idle":
                self.set_status(f"New version v{info['version']} is available")
        elif manual:
            self.set_status(f"You have the latest version (v{APP_VERSION})")

    def on_update(self):
        info = self.update_info
        if self.updating:
            return
        if not info:
            self.set_status("Checking for updates...")
            self.check_update(manual=True)
        elif self.state != "idle":
            self.set_status("Finish the recording first, then update", error=True)
        elif not (getattr(sys, "frozen", False) and info["url"]):
            webbrowser.open(RELEASES_URL)
        elif messagebox.askyesno(APP_NAME, f"Update to v{info['version']}?\n\n{info['notes'][:500]}".strip()):
            self.updating = True
            self.b_update.configure(state="disabled")
            threading.Thread(target=self._download_update, args=(info,), daemon=True).start()

    def _download_update(self, info):
        new = sys.executable + ".new"

        def progress(got, total):
            text = f"Downloading update... {got * 100 // max(1, total)}%"
            self.post(lambda: self.set_status(text))
        try:
            download_update(info, new, progress)
        except Exception as e:
            msg = f"Update failed: {e}"
            self.post(lambda: self._update_failed(msg))
            return
        self.post(lambda: self._install_when_idle(new))

    def _install_when_idle(self, new):
        if self.state != "idle":  # a recording was started during the download
            self.root.after(1000, lambda: self._install_when_idle(new))
            return
        try:
            install_update(sys.executable, new)
        except Exception as e:
            try:
                os.remove(new)
            except OSError:
                pass
            self._update_failed(f"Could not install the update automatically ({e}).\n\n"
                                "The download page will open so you can install it by hand.")
            webbrowser.open(RELEASES_URL)
            return
        self.root.after_idle(self._exit)  # this runs inside tick(), which must finish first

    def _update_failed(self, msg):
        self.updating = False
        self.b_update.configure(state="normal")
        self.set_status("Update failed", error=True)
        messagebox.showerror(APP_NAME, msg)

    # ---------------------------------------------------------------- settings / exit
    def save_settings(self):
        s = {"capture_mode": self.mode.get(), "show_frame": self.show_frame.get(), "monitor": self.monitor_index(), "window": self.win_var.get(),
             "region": list(self.region) if self.region else None, "fps": int(self.fps.get()),
             "quality": self.quality.get(), "res": self.res.get(), "fmt": self.fmt.get(),
             "encoder": self.chosen_encoder() if self.enc.get() != "Auto (best available)" else None,
             "engine": self.engine.get(), "cursor": self.cursor.get(), "denoise": self.denoise.get(),
             "all_apis": self.all_apis.get(), "folder": self.folder.get(), "countdown": int(self.countdown.get()),
             "minimize": self.minimize.get(), "open_after": self.open_after.get(),
             "zoom_level": self.zoom_level.get(), "zoom_follow": self.zoom_follow.get(),
             "audio": {k: {"enabled": r["en"].get(), "device": r["dev"].get(), "gain": r["gain"].get()}
                       for k, r in self.rows.items()}}
        try:
            os.makedirs(SETTINGS_DIR, exist_ok=True)
            with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
                json.dump(s, f, indent=2)
        except Exception:
            pass

    def on_close(self):
        if self.state in ("recording", "paused"):
            if not messagebox.askyesno(APP_NAME, "A recording is in progress. Stop and save it, then exit?"):
                return
            self.stop()
            self._exit_when_idle()
            return
        if self.state in ("saving", "busy", "starting"):
            self._exit_when_idle()
            return
        self._exit()

    def _exit_when_idle(self):
        if self.state == "idle":
            self._exit()
        else:
            self.root.after(300, self._exit_when_idle)

    def _exit(self):
        self.save_settings()
        self.hotkeys.stop()
        for src in self.sources.values():
            src.close()
        try:
            self.pa.terminate()
        except Exception:
            pass
        self.root.destroy()


def main():
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("ScreenRecorderPro")
    except Exception:
        pass
    ctk.set_appearance_mode("dark")
    root = ctk.CTk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
