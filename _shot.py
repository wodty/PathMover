# -*- coding: utf-8 -*-
"""开发用：把 PathMover 窗口渲染成 PNG，方便检查布局（不参与发布）。"""

import ctypes
import ctypes.wintypes as wt
import struct
import sys
import time
import tkinter as tk
import zlib

import main as M

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)


def write_png(path, w, h, rows):
    raw = b"".join(b"\x00" + r for r in rows)

    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    png = (b"\x89PNG\r\n\x1a\n"
           + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(raw, 6))
           + chunk(b"IEND", b""))
    open(path, "wb").write(png)


def grab(hwnd, path):
    rect = wt.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(rect))
    w, h = rect.right, rect.bottom
    if w <= 0 or h <= 0:
        raise RuntimeError(f"窗口尺寸异常 {w}x{h}")

    hdc = user32.GetDC(hwnd)
    mdc = gdi32.CreateCompatibleDC(hdc)
    bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
    gdi32.SelectObject(mdc, bmp)
    gdi32.SetBkMode(mdc, 1)
    # PW_CLIENTONLY = 1
    user32.PrintWindow(hwnd, mdc, 1)

    bi = struct.pack("<IiiHHIIiiII", 40, w, -h, 1, 32, 0, w * h * 4, 0, 0, 0, 0)
    buf = ctypes.create_string_buffer(w * h * 4)
    gdi32.GetDIBits(mdc, bmp, 0, h, buf, bi, 0)

    data = buf.raw
    rows = []
    for y in range(h):
        line = data[y * w * 4:(y + 1) * w * 4]
        rows.append(bytes(b for i in range(0, len(line), 4) for b in (line[i + 2], line[i + 1], line[i])))

    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(mdc)
    user32.ReleaseDC(hwnd, hdc)
    write_png(path, w, h, rows)
    return w, h


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else "shot.png"
    tab = sys.argv[2] if len(sys.argv) > 2 else "0"
    root = tk.Tk()
    app = M.App(root)
    root.update_idletasks()
    root.update()
    if tab != "0":
        app.nb.select(int(tab))
    time.sleep(0.4)
    for _ in range(6):
        root.update()
        time.sleep(0.1)
    hwnd = root.winfo_id()
    parent = user32.GetParent(hwnd)
    if parent:
        hwnd = parent
    print("hwnd", hwnd, "->", grab(hwnd, out))
    root.destroy()


if __name__ == "__main__":
    main()
