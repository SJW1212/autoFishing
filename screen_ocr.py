"""Region-only capture and offline Windows OCR on a dedicated worker thread."""
import asyncio
import queue
import threading
import time
from dataclasses import dataclass
from typing import Callable

import mss
from PIL import Image, ImageOps
from winrt.windows.globalization import Language
from winrt.windows.graphics.imaging import SoftwareBitmap, BitmapPixelFormat, BitmapAlphaMode
from winrt.windows.media.ocr import OcrEngine
from winrt.windows.storage.streams import DataWriter
import winrt.windows.foundation.collections  # WinRT resolves this dynamically.
from winrt.runtime import ApartmentType, init_apartment, uninit_apartment


@dataclass(frozen=True)
class Region:
    x: int
    y: int
    width: int
    height: int
    client_width: int
    client_height: int

    def validate(self):
        values = (self.x, self.y, self.width, self.height, self.client_width, self.client_height)
        if any(type(value) is not int for value in values):
            raise ValueError("자막 영역 좌표는 정수여야 합니다.")
        if self.x < 0 or self.y < 0 or self.width < 40 or self.height < 16:
            raise ValueError("자막 문구 전체를 포함하도록 조금 더 큰 영역을 선택하세요.")
        if self.width > 1600 or self.height > 1000:
            raise ValueError("자막 영역이 너무 큽니다. 오른쪽 아래 자막 부분만 선택하세요.")
        if self.x + self.width > self.client_width or self.y + self.height > self.client_height:
            raise ValueError("자막 영역을 게임 화면 안에서 선택하세요.")


def preprocess(image: Image.Image) -> Image.Image:
    gray = ImageOps.grayscale(image)
    binary = gray.point(lambda value: 0 if value >= 145 else 255)
    scale = 2 if image.width <= 1000 else 1
    return ImageOps.expand(binary.resize((image.width * scale, image.height * scale),
                                        Image.Resampling.NEAREST).convert("RGBA"), border=12, fill="white")


class WindowsOcr:
    def __init__(self, language="ko"):
        self.engine = OcrEngine.try_create_from_language(Language(language))
        if self.engine is None:
            raise RuntimeError("한국어 OCR이 없습니다. Windows 설정 → 시간 및 언어 → 한국어 → 언어 옵션에서 광학 문자 인식을 설치하세요.")

    async def read(self, image: Image.Image) -> str:
        prepared = preprocess(image)
        writer = DataWriter()
        bitmap = SoftwareBitmap(BitmapPixelFormat.BGRA8, prepared.width, prepared.height,
                                BitmapAlphaMode.IGNORE)
        try:
            writer.write_bytes(prepared.tobytes("raw", "BGRA"))
            bitmap.copy_from_buffer(writer.detach_buffer())
            result = await self.engine.recognize_async(bitmap)
            return "\n".join(line.text for line in result.lines)
        finally:
            bitmap.close()
            writer.close()


@dataclass
class Observation:
    generation: int
    captured_at: float
    text: str
    image: Image.Image | None
    duration: float
    error: str = ""


class ScreenReader:
    def __init__(self, locate: Callable, results: queue.Queue, generation: int):
        self.locate = locate
        self.results = results
        self.generation = generation
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True, name="subtitle-ocr")

    def start(self):
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        self.thread.join(timeout=.2)

    def _publish(self, observation):
        try:
            self.results.put_nowait(observation)
        except queue.Full:
            try:
                self.results.get_nowait()
            except queue.Empty:
                pass  # The UI already consumed the old frame.
            self.results.put_nowait(observation)

    def _run(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        initialized = False
        try:
            init_apartment(ApartmentType.MULTI_THREADED)
            initialized = True
            ocr = WindowsOcr()
            with mss.MSS() as capture:
                while not self.stop_event.is_set():
                    start = time.monotonic()
                    bounds = self.locate()
                    if bounds is None:
                        self.stop_event.wait(.1)
                        continue
                    frame = capture.grab(bounds)
                    image = Image.frombytes("RGB", frame.size, frame.rgb)
                    text = loop.run_until_complete(ocr.read(image))
                    observation = Observation(self.generation, start, text, image,
                                              time.monotonic() - start)
                    self._publish(observation)
                    self.stop_event.wait(max(0, .12 - observation.duration))
        except Exception as exc:
            self._publish(Observation(self.generation, time.monotonic(), "", None, 0, str(exc)))
        finally:
            loop.close()
            if initialized:
                uninit_apartment()
