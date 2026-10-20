"""Subtitle matching and fishing state machine, independent of screen/input APIs."""
import unicodedata
from dataclasses import dataclass
from typing import Callable

DEFAULT_TARGET = "낚시찌가 첨벙거림"


def normalize_text(text: str) -> str:
    return "".join(char.lower() for char in unicodedata.normalize("NFC", text) if char.isalnum())


def matches_subtitle(text: str, target: str) -> bool:
    phrase = normalize_text(target)
    if len(phrase) < 4:
        return False
    for line in text.splitlines():
        normalized = normalize_text(line)
        if phrase in normalized:
            return True
        # Observed Windows Korean OCR readings of Minecraft's Unifont glyphs.
        # Restrict two-character corrections to this complete known phrase.
        if phrase == normalize_text(DEFAULT_TARGET):
            if any(variant in normalized for variant in ("낚시찌가침범거림", "낚시찌가점범거림")):
                return True
        # Full long phrases tolerate a single OCR edit (e.g. 첨벙 -> 점벙).
        if len(phrase) < 8:
            continue
        for size in (len(phrase) - 1, len(phrase), len(phrase) + 1):
            for start in range(len(normalized) - size + 1):
                if within_one_edit(normalized[start:start + size], phrase):
                    return True
    return False


def within_one_edit(left: str, right: str) -> bool:
    if abs(len(left) - len(right)) > 1:
        return False
    i = j = errors = 0
    while i < len(left) and j < len(right):
        if left[i] == right[j]:
            i += 1
            j += 1
            continue
        errors += 1
        if errors > 1:
            return False
        if len(left) >= len(right):
            i += 1
        if len(right) >= len(left):
            j += 1
    return errors + (len(left) - i) + (len(right) - j) <= 1


@dataclass(frozen=True)
class FishingOptions:
    recast_delay: float = .8
    settle_time: float = 2.5
    timeout: float = 65.0
    already_cast: bool = False

    def validate(self):
        if not .3 <= self.recast_delay <= 3:
            raise ValueError("다시 던지기 간격은 0.3~3초로 설정하세요.")
        if not 1 <= self.settle_time <= 10:
            raise ValueError("투척 후 무시 시간은 1~10초로 설정하세요.")
        if not 15 <= self.timeout <= 300:
            raise ValueError("입질 대기 제한은 15~300초로 설정하세요.")


class FishingController:
    def __init__(self, click: Callable[[], None], foreground: Callable[[], bool],
                 report: Callable[[str], None]):
        self.click = click
        self.foreground = foreground
        self.report = report
        self.state = "idle"
        self.count = 0
        self.due = 0.0
        self.wait_started = 0.0
        self.clear_since = None
        self.armed = False
        self.options = FishingOptions()

    @property
    def running(self) -> bool:
        return self.state != "idle"

    def start(self, now: float, options: FishingOptions, delay: float):
        options.validate()
        self.options = options
        self.state = "arming"
        self.due = now + delay
        self.armed = False
        self.clear_since = None
        self.report("시작 대기: 낚싯대를 들고 게임 화면으로 돌아가세요.")

    def stop(self, reason: str = "중지됨"):
        self.state = "idle"
        self.armed = False
        self.clear_since = None
        self.report(reason)

    def _wait(self, now: float):
        self.state = "waiting"
        self.wait_started = now
        self.due = now + self.options.settle_time
        self.armed = False
        self.clear_since = None

    def tick(self, now: float, screen_fresh: bool):
        if not self.running:
            return
        if self.state == "arming" and now < self.due:
            return
        if not self.foreground():
            self.stop("게임 창을 벗어나 자동 중지했습니다. 다시 시작하려면 F8.")
            return
        if not screen_fresh:
            self.stop("화면 인식이 지연되거나 끊겨 중지했습니다. 자막 영역을 확인하세요.")
            return
        if self.state == "arming":
            if not self.options.already_cast:
                self.click()
            self._wait(now)
            self.report("입질 자막 대기 중 · F9 중지")
        elif self.state == "recast" and now >= self.due:
            self.click()
            self._wait(now)
            self.report("다시 던짐 · 이전 자막이 사라진 뒤 감지합니다.")
        elif self.state == "waiting" and now - self.wait_started > self.options.timeout:
            self.stop("대기 시간 초과. 찌 상태와 자막 인식을 확인한 뒤 다시 시작하세요.")

    def observe(self, now: float, matched: bool, captured_at: float):
        """Called once per new frame, never repeatedly for a cached OCR result."""
        if self.state != "waiting" or captured_at < self.wait_started or now - captured_at > 1:
            return
        if not matched:
            if self.clear_since is None:
                self.clear_since = captured_at
            if captured_at - self.clear_since >= .4:
                self.armed = True
            return
        self.clear_since = None
        if not self.armed or now < self.due or not self.foreground():
            return
        self.click()
        self.count += 1
        self.state = "recast"
        self.armed = False
        self.due = now + self.options.recast_delay
        self.report(f"입질 자막 감지 · 회수 입력 {self.count}회")
