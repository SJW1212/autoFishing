"""Minecraft subtitle fishing helper for Windows 10/11."""
import ctypes
import json
import math
import queue
import sys
import time
import tkinter as tk
import traceback
from dataclasses import asdict
from pathlib import Path
from tkinter import ttk, messagebox

# Coordinates must use physical pixels on every monitor, before creating Tk.
ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))

from PIL import ImageTk
from automation import DEFAULT_TARGET, FishingController, FishingOptions, matches_subtitle, normalize_text
from screen_ocr import Region, ScreenReader
from windows_io import GameWindow, Hotkeys, release_right_button, user32

APP_DIR = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
CONFIG_FILE = APP_DIR / "settings.json"


class RegionSelector(tk.Toplevel):
    def __init__(self, parent, client, callback):
        super().__init__(parent)
        self.client = client
        self.callback = callback
        self.start = None
        self.box = None
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.attributes("-alpha", .4)
        x, y, width, height = client
        self.geometry(f"{width}x{height}")
        self.canvas = tk.Canvas(self, bg="#071224", cursor="crosshair", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.update_idletasks()
        hwnd = user32.GetAncestor(self.winfo_id(), 2)
        if not user32.SetWindowPos(hwnd, ctypes.c_void_p(-1), x, y, width, height, 0x0040):
            self.destroy()
            raise RuntimeError("영역 선택 창을 게임 위치에 표시하지 못했습니다.")
        self.canvas.create_text(width // 2, 45, text="자막이 나타나는 오른쪽 아래 영역 전체를 드래그 · Esc 취소",
                                fill="white", font=("맑은 고딕", 17, "bold"))
        self.bind("<ButtonPress-1>", self.press)
        self.bind("<B1-Motion>", self.drag)
        self.bind("<ButtonRelease-1>", self.release)
        self.bind("<Escape>", lambda event: self.destroy())
        self.focus_force()
        self.grab_set()

    def point(self, event):
        return (max(0, min(event.x, self.client[2])), max(0, min(event.y, self.client[3])))

    def press(self, event):
        self.start = self.point(event)
        if self.box:
            self.canvas.delete(self.box)
        self.box = self.canvas.create_rectangle(*self.start, *self.start, outline="#60ffcb", width=3)

    def drag(self, event):
        if self.start:
            self.canvas.coords(self.box, *self.start, *self.point(event))

    def release(self, event):
        if not self.start:
            return
        x, y = self.point(event)
        sx, sy = self.start
        region = Region(min(x, sx), min(y, sy), abs(x - sx), abs(y - sy), self.client[2], self.client[3])
        self.destroy()
        self.callback(region)


class FishingApp:
    def __init__(self, root):
        self.root = root
        self.game = None
        self.region = None
        self.reader = None
        self.generation = 0
        self.results = queue.Queue(maxsize=2)
        self.latest_at = 0.0
        self.pending_up = None
        self.release_job = None
        self.photo = None
        self.selector = None
        self.closed = False
        self.selection_pending = False
        self.active_target = DEFAULT_TARGET
        self.status = tk.StringVar(value="준비 · 게임에서 자막 표시를 켜 주세요.")
        self.target_text = tk.StringVar(value=DEFAULT_TARGET)
        self.region_text = tk.StringVar(value="자막 영역 미설정")
        self.game_text = tk.StringVar(value="게임 창 미등록 · 게임 화면에서 F7")
        self.read_text = tk.StringVar(value="아직 읽은 자막이 없습니다.")
        self.match_text = tk.StringVar(value="인식 대기")
        self.count_text = tk.StringVar(value="회수 입력 0회")
        self.recast_delay = tk.StringVar(value="0.8")
        self.settle_time = tk.StringVar(value="2.5")
        self.timeout = tk.StringVar(value="65")
        self.already_cast = tk.BooleanVar(value=False)
        self.controller = FishingController(self.click, self.is_foreground, self.report)
        self.hotkeys = Hotkeys()
        self.build_ui()
        self.load_config()
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.report_callback_exception = self.callback_error
        self.root.after(20, self.poll)

    def build_ui(self):
        self.root.title("마크 자동 낚시 · 자막 인식")
        self.root.geometry("780x820")
        self.root.minsize(700, 740)
        self.root.configure(bg="#f3f6fa")
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("TFrame", background="#f3f6fa")
        style.configure("TLabel", background="#f3f6fa", font=("맑은 고딕", 10))
        style.configure("TButton", font=("맑은 고딕", 10), padding=7)
        style.configure("TLabelframe", background="#f3f6fa")
        style.configure("TLabelframe.Label", background="#f3f6fa", font=("맑은 고딕", 10, "bold"))
        style.configure("TCheckbutton", background="#f3f6fa", font=("맑은 고딕", 10))
        main = ttk.Frame(self.root, padding=22)
        main.pack(fill="both", expand=True)
        ttk.Label(main, text="마크 자동 낚시", font=("맑은 고딕", 23, "bold")).pack(anchor="w")
        ttk.Label(main, text="화면 자막 감지 → 우클릭 회수 → 다시 던지기", foreground="#45617e").pack(anchor="w", pady=(2, 15))
        guide = "① 게임 설정에서 자막 표시 켜기\n② 게임 화면에서 F6 → 오른쪽 아래 자막 영역 전체 드래그\n③ 낚싯대를 든 상태로 F8 시작 · F9 즉시 중지"
        ttk.Label(main, text=guide, justify="left").pack(anchor="w", pady=(0, 12))
        setup = ttk.LabelFrame(main, text="감지 설정", padding=12)
        setup.pack(fill="x", pady=5)
        ttk.Label(setup, textvariable=self.game_text, wraplength=680).pack(anchor="w")
        ttk.Label(setup, textvariable=self.region_text, foreground="#45617e").pack(anchor="w", pady=(3, 8))
        row = ttk.Frame(setup)
        row.pack(fill="x")
        ttk.Label(row, text="입질 자막").pack(side="left", padx=(0, 12))
        ttk.Entry(row, textvariable=self.target_text).pack(side="left", fill="x", expand=True)
        ttk.Label(setup, text="공백·화살표 무시 · 긴 문구 한 글자 오독 허용 · 기본 자막의 침범/점범 오독 보정",
                  foreground="#64748b", font=("맑은 고딕", 9)).pack(anchor="w", pady=(6, 0))
        timing = ttk.Frame(setup)
        timing.pack(fill="x", pady=(10, 0))
        for label, variable in (("재투척 간격(초)", self.recast_delay), ("투척 후 무시(초)", self.settle_time), ("대기 제한(초)", self.timeout)):
            ttk.Label(timing, text=label).pack(side="left", padx=(0, 5))
            ttk.Entry(timing, textvariable=variable, width=5).pack(side="left", padx=(0, 12))
        ttk.Checkbutton(setup, text="이미 찌를 던져 둠 (시작 시 첫 투척 생략)", variable=self.already_cast).pack(anchor="w", pady=(10, 0))
        preview = ttk.LabelFrame(main, text="최근 자막 인식 · 게임 창이 활성 상태일 때만 읽습니다", padding=12)
        preview.pack(fill="x", pady=9)
        ttk.Label(preview, textvariable=self.read_text, wraplength=670, foreground="#152e50", justify="left").pack(anchor="w")
        ttk.Label(preview, textvariable=self.match_text, foreground="#45617e").pack(anchor="w", pady=5)
        self.preview_label = ttk.Label(preview)
        self.preview_label.pack(anchor="w")
        buttons = ttk.Frame(main)
        buttons.pack(fill="x", pady=5)
        ttk.Button(buttons, text="3초 후 영역 선택", command=self.schedule_selection).pack(side="left", padx=(0, 6))
        ttk.Button(buttons, text="3초 후 시작", command=lambda: self.start(3)).pack(side="left", padx=6)
        ttk.Button(buttons, text="중지 (F9)", command=self.stop).pack(side="left", padx=6)
        ttk.Label(buttons, textvariable=self.count_text).pack(side="right")
        ttk.Label(main, textvariable=self.status, wraplength=710, foreground="#165d55", font=("맑은 고딕", 10, "bold")).pack(anchor="w", pady=(12, 7))
        self.log = tk.Text(main, height=5, wrap="word", state="disabled", bg="white", fg="#43556b",
                           relief="flat", font=("맑은 고딕", 9), padx=10, pady=8)
        self.log.pack(fill="both", expand=True)
        ttk.Label(main, text="F6 영역 선택  |  F7 게임 등록  |  F8 시작/중지  |  F9 중지\n창 크기·GUI 배율을 바꾸면 영역을 다시 선택하세요. 설정 변경은 다음 시작부터 적용됩니다.",
                  foreground="#64748b", font=("맑은 고딕", 9)).pack(anchor="w", pady=(10, 0))

    def report(self, message):
        self.status.set(message)
        self.log.configure(state="normal")
        self.log.insert("end", f"{time.strftime('%H:%M:%S')}  {message}\n")
        if int(self.log.index("end-1c").split(".")[0]) > 100:
            self.log.delete("1.0", "20.0")
        self.log.see("end")
        self.log.configure(state="disabled")

    def bind_game(self):
        game = GameWindow.capture()
        self.stop("게임 창 등록 중")
        self.game = game
        self.game_text.set(f"게임: {game.title}")
        self.restart_reader()
        self.report("게임 창 등록 완료. F6으로 자막 영역을 선택하세요.")

    def is_foreground(self):
        return bool(self.game and self.game.is_foreground())

    def restart_reader(self):
        self.generation += 1
        if self.reader:
            self.reader.stop()
            self.reader = None
        self.latest_at = 0
        if self.region and self.game:
            # Capture immutable binding, so an old worker cannot use a new ROI.
            game, region = self.game, self.region
            def locate_bound():
                if not game.is_foreground():
                    return None
                x, y, width, height = game.client_rect()
                if (width, height) != (region.client_width, region.client_height):
                    raise RuntimeError("게임 창 크기가 바뀌었습니다. F6으로 자막 영역을 다시 선택하세요.")
                return {"left": x + region.x, "top": y + region.y, "width": region.width, "height": region.height}
            self.reader = ScreenReader(locate_bound, self.results, self.generation)
            self.reader.start()

    def schedule_selection(self):
        self.stop()
        self.selection_pending = True
        self.report("3초 안에 게임 화면으로 돌아가세요. 자막 영역 선택이 열립니다.")
        self.root.after(3000, self.delayed_selection)

    def delayed_selection(self):
        if self.selection_pending and not self.closed:
            self.selection_pending = False
            self.select_region()

    def select_region(self):
        self.selection_pending = False
        if self.selector and self.selector.winfo_exists():
            return
        self.bind_game()
        if self.reader:
            self.reader.stop()
            self.reader = None
            self.generation += 1
        self.selector = RegionSelector(self.root, self.game.client_rect(), self.set_region)
        self.report("자막이 표시되는 영역 전체를 드래그하세요. Esc 취소.")

    def set_region(self, region):
        try:
            region.validate()
            self.region = region
            self.region_text.set(f"자막 영역: {region.width} × {region.height}px · 창 이동은 자동 추적")
            self.restart_reader()
            self.save_config()
            self.report("영역 저장 완료. 게임에 돌아가 자막을 띄운 뒤 인식 결과를 확인하세요.")
        except (ValueError, OSError) as exc:
            self.report(str(exc))

    def options(self):
        values = [float(variable.get()) for variable in (self.recast_delay, self.settle_time, self.timeout)]
        if not all(math.isfinite(value) for value in values):
            raise ValueError("시간 설정에 올바른 숫자를 입력하세요.")
        options = FishingOptions(*values, already_cast=self.already_cast.get())
        options.validate()
        return options

    def start(self, delay):
        if self.controller.running:
            self.stop()
            return
        if self.selection_pending or (self.selector and self.selector.winfo_exists()):
            raise ValueError("영역 선택을 먼저 마치세요.")
        if not self.region:
            raise ValueError("게임 화면에서 F6을 눌러 자막 영역을 먼저 선택하세요.")
        if delay < 1:
            candidate = GameWindow.capture()
            if not self.game or candidate.hwnd != self.game.hwnd:
                self.game = candidate
                self.game_text.set(f"게임: {candidate.title}")
                self.restart_reader()
        if not self.game:
            raise ValueError("게임 화면에서 F7로 창을 등록하세요.")
        if not self.reader or not self.reader.thread.is_alive():
            self.restart_reader()
        target = self.target_text.get().strip()
        if not 4 <= len(normalize_text(target)) <= 80:
            raise ValueError("입질 자막 문구를 4~80글자로 입력하세요.")
        self.active_target = target
        self.save_config()
        self.controller.start(time.monotonic(), self.options(), delay)

    def click(self):
        if self.pending_up is not None:
            raise RuntimeError("이전 우클릭이 아직 끝나지 않아 중지했습니다.")
        if not self.game:
            raise RuntimeError("게임 창이 등록되지 않았습니다.")
        self.pending_up = self.game.right_click()
        self.release_job = self.root.after(65, self.release_button)

    def release_button(self):
        self.release_job = None
        if self.pending_up is not None:
            up, self.pending_up = self.pending_up, None
            release_right_button(up)

    def stop(self, reason="중지됨 · F8로 다시 시작"):
        self.selection_pending = False
        self.controller.stop(reason)
        if self.release_job:
            self.root.after_cancel(self.release_job)
            self.release_job = None
        self.release_button()

    def poll(self):
        if self.closed:
            return
        try:
            for key in self.hotkeys.poll():
                if key == 1:
                    self.select_region()
                elif key == 2:
                    self.bind_game()
                elif key == 3:
                    self.start(.6)
                elif key == 4:
                    self.stop()
            now = time.monotonic()
            observations = []
            while True:
                try:
                    observation = self.results.get_nowait()
                except queue.Empty:
                    break
                if observation.generation == self.generation:
                    observations.append(observation)
            if observations:
                observation = observations[-1]
                if observation.error:
                    self.stop(f"화면 인식 오류: {observation.error}")
                    self.latest_at = 0
                else:
                    self.latest_at = observation.captured_at
                    self.read_text.set(observation.text or "(자막 없음)")
                    phrase = self.active_target if self.controller.running else self.target_text.get()
                    matched = matches_subtitle(observation.text, phrase)
                    self.match_text.set(f"{'입질 문구 일치' if matched else '입질 문구 없음'} · OCR {observation.duration * 1000:.0f}ms")
                    self.controller.tick(now, now - self.latest_at < 1.5)
                    self.controller.observe(now, matched, observation.captured_at)
                    if observation.image:
                        thumbnail = observation.image.copy()
                        thumbnail.thumbnail((660, 105))
                        self.photo = ImageTk.PhotoImage(thumbnail)
                        self.preview_label.configure(image=self.photo)
            self.controller.tick(now, bool(self.latest_at and now - self.latest_at < 1.5))
            self.count_text.set(f"회수 입력 {self.controller.count}회")
        except (ValueError, RuntimeError, OSError, tk.TclError) as exc:
            self.stop(str(exc))
        finally:
            if not self.closed:
                self.root.after(20, self.poll)

    def load_config(self):
        if not CONFIG_FILE.exists():
            return
        try:
            saved = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            target = saved.get("target", DEFAULT_TARGET)
            if not isinstance(target, str) or not 4 <= len(normalize_text(target)) <= 80:
                raise ValueError("입질 문구가 올바르지 않습니다.")
            self.target_text.set(target)
            if saved.get("region"):
                region = Region(**saved["region"])
                region.validate()
                self.region = region
                self.region_text.set(f"저장된 자막 영역: {region.width} × {region.height}px · 게임에서 F7 등록")
            for key, variable in (("recast_delay", self.recast_delay), ("settle_time", self.settle_time), ("timeout", self.timeout)):
                variable.set(str(saved.get(key, variable.get())))
            self.options()
        except (ValueError, TypeError, KeyError, AttributeError, OSError) as exc:
            self.region = None
            self.recast_delay.set("0.8")
            self.settle_time.set("2.5")
            self.timeout.set("65")
            self.report(f"저장된 설정을 읽지 못했습니다. 다시 설정하세요: {exc}")

    def save_config(self):
        options = self.options()
        saved = {"target": self.target_text.get().strip(), "region": asdict(self.region) if self.region else None,
                 "recast_delay": options.recast_delay, "settle_time": options.settle_time, "timeout": options.timeout}
        temporary = CONFIG_FILE.with_suffix(".tmp")
        temporary.write_text(json.dumps(saved, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(CONFIG_FILE)

    def callback_error(self, exception, value, tb):
        self.stop(f"오류로 중지했습니다: {value}")
        (APP_DIR / "error.log").write_text("".join(traceback.format_exception(exception, value, tb)), encoding="utf-8")

    def close(self):
        if self.closed:
            return
        self.closed = True
        try:
            self.stop("종료됨")
            if self.reader:
                self.reader.stop()
            self.save_config()
        except (ValueError, RuntimeError, OSError) as exc:
            messagebox.showerror("종료 알림", str(exc))
        finally:
            self.hotkeys.close()
            self.root.destroy()


def main():
    if "--diagnose" in sys.argv:
        from diagnostics import run_diagnostics
        run_diagnostics(APP_DIR)
        return
    root = tk.Tk()
    try:
        app = FishingApp(root)
    except Exception as exc:
        messagebox.showerror("시작 실패", str(exc))
        root.destroy()
        return
    if "--smoke-test" in sys.argv:
        root.attributes("-topmost", True)
        def finish_smoke():
            result = {"pass": True, "window_size": [root.winfo_width(), root.winfo_height()],
                      "hotkeys_registered": len(app.hotkeys.registered), "running": app.controller.running}
            (APP_DIR / "ui_smoke.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
            import mss
            from PIL import Image
            with mss.MSS() as capture:
                frame = capture.grab({"left": root.winfo_rootx(), "top": root.winfo_rooty(),
                                      "width": root.winfo_width(), "height": root.winfo_height()})
                Image.frombytes("RGB", frame.size, frame.rgb).save(APP_DIR / "ui_smoke.png")
            app.close()
        root.after(1000, finish_smoke)
    root.mainloop()


if __name__ == "__main__":
    main()
