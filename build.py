"""Builds dist/ScreenRecorderPro.exe (single file, FFmpeg bundled) and dist/ScreenRecorderPro-Setup.exe."""
import os
import re
import shutil
import struct
import subprocess
import sys
import zlib

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))


def png_bytes(img):
    h, w, _ = img.shape
    raw = b"".join(b"\x00" + img[y].tobytes() for y in range(h))

    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def icon_image(size):
    yy, xx = np.mgrid[0:size, 0:size] + 0.5
    c = size / 2
    r = np.hypot(xx - c, yy - c)
    img = np.zeros((size, size, 4), np.uint8)
    outer = np.clip(c * 0.96 - r + 0.5, 0, 1)          # dark rounded badge
    ring = np.clip(c * 0.80 - r + 0.5, 0, 1) - np.clip(c * 0.68 - r + 0.5, 0, 1)
    dot = np.clip(c * 0.48 - r + 0.5, 0, 1)
    for ch, (bg, fg) in enumerate([(30, 255), (30, 59), (36, 48)]):
        img[..., ch] = (bg * (1 - np.maximum(ring, dot)) + fg * np.maximum(ring, dot)).astype(np.uint8)
    img[..., 3] = (outer * 255).astype(np.uint8)
    return img


def write_ico(path):
    sizes = [16, 24, 32, 48, 64, 128, 256]
    pngs = [png_bytes(icon_image(s)) for s in sizes]
    header = struct.pack("<HHH", 0, 1, len(sizes))
    offset = 6 + 16 * len(sizes)
    entries = b""
    for s, data in zip(sizes, pngs):
        entries += struct.pack("<BBBBHHII", s % 256, s % 256, 0, 0, 1, 32, len(data), offset)
        offset += len(data)
    with open(path, "wb") as f:
        f.write(header + entries + b"".join(pngs))


def app_version():
    with open(os.path.join(HERE, "screen_recorder.py"), encoding="utf-8") as f:
        return re.search(r'^APP_VERSION = "(.+?)"', f.read(), re.M).group(1)


def write_version_info(path):
    """File properties of the exe: version, Cambo Ads as publisher and copyright holder."""
    version = app_version()
    nums = ", ".join(version.split(".") + ["0"])
    strings = {"CompanyName": "Cambo Ads", "FileDescription": "Screen Recorder Pro", "FileVersion": version,
               "InternalName": "ScreenRecorderPro", "LegalCopyright": "© 2026 Cambo Ads. Licensed under AGPL-3.0.",
               "OriginalFilename": "ScreenRecorderPro.exe", "ProductName": "Screen Recorder Pro",
               "ProductVersion": version}
    items = ", ".join(f"StringStruct({k!r}, {v!r})" for k, v in strings.items())
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers=({nums}), prodvers=({nums}), mask=0x3f, flags=0, OS=0x40004, fileType=1,
                    subtype=0, date=(0, 0)),
  kids=[StringFileInfo([StringTable('040904B0', [{items}])]), VarFileInfo([VarStruct('Translation', [1033, 1200])])]
)
""")


def find_iscc():
    roots = [os.environ.get(k, "") for k in ("LOCALAPPDATA", "ProgramFiles(x86)", "ProgramFiles")]
    paths = [os.path.join(r, *sub, "Inno Setup 6", "ISCC.exe")
             for r, sub in zip(roots, (("Programs",), (), ()))]
    return shutil.which("iscc") or next((p for p in paths if os.path.exists(p)), None)


def build_installer(exe, ico):
    iscc = find_iscc()
    if not iscc:
        sys.exit("Inno Setup 6 not found (winget install JRSoftware.InnoSetup)")
    subprocess.check_call([iscc, "/Qp", f"/DAppVersion={app_version()}", f"/DExeSource={exe}", f"/DIconFile={ico}",
                           os.path.join(HERE, "installer.iss")], cwd=HERE)


def main():
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        sys.exit("ffmpeg.exe not found on PATH (winget install Gyan.FFmpeg.Essentials)")
    build = os.path.join(HERE, "build")
    os.makedirs(build, exist_ok=True)
    ico = os.path.join(build, "icon.ico")
    write_ico(ico)
    info = os.path.join(build, "version_info.txt")
    write_version_info(info)
    subprocess.check_call([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onefile", "--windowed",
                           "--name", "ScreenRecorderPro", "--icon", ico, "--version-file", info,
                           "--add-binary", f"{ffmpeg};.", "--add-data", f"{ico};.", "--collect-data", "customtkinter",
                           "--workpath", build, "--specpath", build, "--distpath", os.path.join(HERE, "dist"),
                           os.path.join(HERE, "screen_recorder.py")])
    exe = os.path.join(HERE, "dist", "ScreenRecorderPro.exe")
    build_installer(exe, ico)
    print("\nDone ->", exe, "and", os.path.join(HERE, "dist", "ScreenRecorderPro-Setup.exe"))


if __name__ == "__main__":
    main()
