"""Run manually on Windows: screen OCR + one right click in our OWN test window."""
import ctypes
import json
import os
import queue
import sys
import time
import tkinter as tk
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
from automation import DEFAULT_TARGET, matches_subtitle
from screen_ocr import ScreenReader
from windows_io import GameWindow, user32, window_process, window_title, release_right_button

root = tk.Tk()
root.title("SubtitleFisher controlled test")
root.geometry("680x220+250+250")
root.attributes("-topmost", True)
canvas = tk.Canvas(root, background="#202020", highlightthickness=0)
canvas.pack(fill="both", expand=True)
canvas.create_text(30, 80, text=DEFAULT_TARGET + " >", anchor="w", fill="white", font=("맑은 고딕", 26))
results = queue.Queue(maxsize=2)
report = {"right_button_down_events": 0, "right_button_up_events": 0, "pass": False}
reader = None
pending_up = None
started = time.monotonic()


def down(event):
    report["right_button_down_events"] += 1


def up(event):
    report["right_button_up_events"] += 1


canvas.bind("<ButtonPress-3>", down)
canvas.bind("<ButtonRelease-3>", up)


def finish(error=None):
    global pending_up
    if pending_up is not None:
        release_right_button(pending_up)
        pending_up = None
    if reader:
        reader.stop()
    if error:
        report["error"] = error
    report["pass"] = not error and report.get("ocr_match", False)
    output = Path(__file__).resolve().parents[1] / "windows_integration.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    root.destroy()


def release():
    global pending_up
    release_right_button(pending_up)
    pending_up = None
    root.after(200, finish)


def poll():
    global pending_up
    try:
        if time.monotonic() - started > 12:
            finish("OCR timeout")
            return
        try:
            observation = results.get_nowait()
        except queue.Empty:
            root.after(20, poll)
            return
        if observation.error:
            finish(observation.error)
            return
        report["recognized"] = observation.text
        report["ocr_match"] = matches_subtitle(observation.text, DEFAULT_TARGET)
        report["ocr_ms"] = round(observation.duration * 1000)
        if not report["ocr_match"]:
            finish("Expected subtitle not recognized")
            return
        # Only click if both process and exact test title are still ours.
        hwnd = user32.GetForegroundWindow()
        if window_process(hwnd)[0] != os.getpid() or window_title(hwnd) != "SubtitleFisher controlled test":
            report["mouse_input_test"] = "NOT_RUN: Windows foreground activation unavailable"
            finish()
            return
        cursor = ctypes.wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(cursor))
        rect = game.client_rect()
        user32.SetCursorPos(rect[0] + 100, rect[1] + 150)
        pending_up = game.right_click()
        report["click_target"] = "own controlled test window only"
        root.after(65, release)
    except Exception as exc:
        finish(str(exc))


def begin():
    global reader, game
    root.lift()
    root.focus_force()
    root.update()
    hwnd = user32.GetForegroundWindow()
    if window_process(hwnd)[0] != os.getpid():
        user32.GetAncestor.argtypes = [ctypes.wintypes.HWND, ctypes.wintypes.UINT]
        user32.GetAncestor.restype = ctypes.wintypes.HWND
        hwnd = user32.GetAncestor(root.winfo_id(), 2)
    game = GameWindow(hwnd, os.getpid(), "SubtitleFisher controlled test")
    x, y, width, height = game.client_rect()
    reader = ScreenReader(lambda: {"left": x, "top": y, "width": width, "height": height}, results, 1)
    reader.start()
    root.after(20, poll)


root.after(200, begin)
root.mainloop()
print(json.dumps(report, ensure_ascii=False, indent=2))
sys.exit(0 if report["pass"] else 1)
