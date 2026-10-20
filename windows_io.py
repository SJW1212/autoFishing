"""Win32 foreground binding, registered hotkeys and short right clicks."""
import ctypes
import queue
import threading
from ctypes import wintypes
from dataclasses import dataclass

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.IsWindow.argtypes = [wintypes.HWND]
user32.IsIconic.argtypes = [wintypes.HWND]
user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
user32.GetAncestor.restype = wintypes.HWND
user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int, wintypes.UINT]
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                              wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]


class MouseInput(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG),
                ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class KeyboardInput(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.c_size_t)]


class HardwareInput(ctypes.Structure):
    _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD), ("wParamH", wintypes.WORD)]


class InputUnion(ctypes.Union):
    _fields_ = [("mi", MouseInput), ("ki", KeyboardInput), ("hi", HardwareInput)]


class Input(ctypes.Structure):
    _anonymous_ = ("value",)
    _fields_ = [("type", wintypes.DWORD), ("value", InputUnion)]


user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(Input), ctypes.c_int]
user32.SendInput.restype = wintypes.UINT
user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
user32.PeekMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND,
                               wintypes.UINT, wintypes.UINT, wintypes.UINT]


def window_title(hwnd):
    text = ctypes.create_unicode_buffer(user32.GetWindowTextLengthW(hwnd) + 1)
    user32.GetWindowTextW(hwnd, text, len(text))
    return text.value


def window_process(hwnd):
    process_id = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(process_id))
    handle = kernel32.OpenProcess(0x1000, False, process_id.value)
    if not handle:
        return process_id.value, ""
    try:
        path = ctypes.create_unicode_buffer(32768)
        size = wintypes.DWORD(len(path))
        if not kernel32.QueryFullProcessImageNameW(handle, 0, path, ctypes.byref(size)):
            return process_id.value, ""
        return process_id.value, path.value.rsplit("\\", 1)[-1].lower()
    finally:
        kernel32.CloseHandle(handle)


@dataclass
class GameWindow:
    hwnd: int
    process_id: int
    title: str

    @classmethod
    def capture(cls):
        hwnd = user32.GetForegroundWindow()
        title = window_title(hwnd)
        process_id, process = window_process(hwnd)
        known_title = any(name in title.lower() for name in
                          ("minecraft", "마인크래프트", "lunar client", "badlion client", "feather client"))
        known_process = process in ("javaw.exe", "java.exe", "minecraft.windows.exe", "minecraft.exe")
        if not title or not known_process or not known_title:
            raise ValueError("마인크래프트 게임 화면에서 F7을 눌러 주세요. 런처 창은 등록하지 않습니다.")
        return cls(hwnd, process_id, title)

    def is_foreground(self):
        return bool(user32.IsWindow(self.hwnd) and not user32.IsIconic(self.hwnd)
                    and user32.GetForegroundWindow() == self.hwnd
                    and window_process(self.hwnd)[0] == self.process_id)

    def right_click(self):
        if not self.is_foreground():
            raise RuntimeError("게임 창이 활성 상태가 아니어서 클릭을 취소했습니다.")
        down = Input(type=0, mi=MouseInput(dwFlags=0x0008))
        up = Input(type=0, mi=MouseInput(dwFlags=0x0010))
        # A down/up in the same batch can be missed by the game's polling loop.
        # Caller schedules the release separately while still guaranteeing it.
        if user32.SendInput(1, ctypes.byref(down), ctypes.sizeof(Input)) != 1:
            raise RuntimeError("우클릭 입력 실패. 게임과 프로그램의 실행 권한을 동일하게 맞추세요.")
        return up

    def client_rect(self):
        rect = wintypes.RECT()
        origin = wintypes.POINT()
        if not user32.GetClientRect(self.hwnd, ctypes.byref(rect)) or not user32.ClientToScreen(self.hwnd, ctypes.byref(origin)):
            raise RuntimeError("게임 창의 위치를 읽을 수 없습니다.")
        return origin.x, origin.y, rect.right, rect.bottom


def release_right_button(up):
    if user32.SendInput(1, ctypes.byref(up), ctypes.sizeof(Input)) != 1:
        raise RuntimeError("오른쪽 버튼 해제에 실패했습니다. 실제 마우스 버튼을 눌렀다 놓아 주세요.")


class Hotkeys:
    KEYS = {1: 0x75, 2: 0x76, 3: 0x77, 4: 0x78}  # F6-F9

    def __init__(self):
        self.registered = []
        self.events = queue.Queue()
        self.ready = threading.Event()
        self.stopping = threading.Event()
        self.failure = ""
        self.thread_id = 0
        # A separate message thread prevents Tk's event loop consuming WM_HOTKEY.
        self.thread = threading.Thread(target=self._run, daemon=True, name="global-hotkeys")
        self.thread.start()
        if not self.ready.wait(2) or self.failure:
            self.close()
            raise RuntimeError(self.failure or "전역 단축키 스레드를 시작하지 못했습니다.")

    def _run(self):
        try:
            self.thread_id = kernel32.GetCurrentThreadId()
            for identifier, key in self.KEYS.items():
                if not user32.RegisterHotKey(None, identifier, 0x4000, key):
                    self.failure = f"F{key - 0x6F} 단축키를 등록할 수 없습니다. 다른 매크로 또는 중복 실행을 닫으세요."
                    return
                self.registered.append(identifier)
            self.ready.set()
            message = wintypes.MSG()
            while not self.stopping.is_set():
                while user32.PeekMessageW(ctypes.byref(message), None, 0x0312, 0x0312, 1):
                    self.events.put(int(message.wParam))
                self.stopping.wait(.01)
        finally:
            for identifier in self.registered:
                user32.UnregisterHotKey(None, identifier)
            self.registered.clear()
            self.ready.set()

    def poll(self):
        result = []
        while True:
            try:
                result.append(self.events.get_nowait())
            except queue.Empty:
                break
        return result

    def close(self):
        self.stopping.set()
        self.thread.join(timeout=1)
