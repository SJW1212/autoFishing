"""Offline diagnostic: synthetic subtitles only, never sends mouse input."""
import asyncio
import json
import time

import mss
from PIL import Image, ImageDraw, ImageFont
from automation import DEFAULT_TARGET, matches_subtitle
from screen_ocr import WindowsOcr
from windows_io import Hotkeys, Input, user32
import ctypes


def run_diagnostics(directory):
    report = {"synthetic_ocr": [], "mouse_input_sent": False}
    try:
        hotkeys = Hotkeys()
        user32.PostThreadMessageW.argtypes = [ctypes.wintypes.DWORD, ctypes.wintypes.UINT,
                                            ctypes.wintypes.WPARAM, ctypes.wintypes.LPARAM]
        posted = user32.PostThreadMessageW(hotkeys.thread_id, 0x0312, 4, 0)
        time.sleep(.05)
        routed = hotkeys.poll() == [4]
        hotkeys.close()
        report["hotkeys"] = "PASS" if posted and routed else "FAIL"
        report["win32_input_structure_bytes"] = ctypes.sizeof(Input)
        with mss.MSS() as capture:
            report["screen_capture"] = list(capture.grab({"left": 0, "top": 0, "width": 4, "height": 4}).size)
        async def check():
            ocr = WindowsOcr()
            font = ImageFont.truetype("C:/Windows/Fonts/malgun.ttf", 25)
            for phrase, expected in ((DEFAULT_TARGET, True), ("무언가가 튀는 소리", False),
                                     ("낚시찌를 던짐", False), ("낚시찌를 회수함", False)):
                image = Image.new("RGB", (540, 65), "#262626")
                ImageDraw.Draw(image).text((12, 10), phrase + " >", font=font, fill="white")
                start = time.monotonic()
                text = await ocr.read(image)
                actual = matches_subtitle(text, DEFAULT_TARGET)
                report["synthetic_ocr"].append({"input": phrase, "recognized": text,
                    "match": actual, "expected": expected, "pass": actual == expected,
                    "milliseconds": round((time.monotonic() - start) * 1000)})
        asyncio.run(check())
        report["pass"] = report["hotkeys"] == "PASS" and all(item["pass"] for item in report["synthetic_ocr"])
    except Exception as exc:
        report["pass"] = False
        report["error"] = str(exc)
    report["real_minecraft_acceptance"] = "NOT_RUN"
    (directory / "diagnostics.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
