"""Windows 全局热键（RegisterHotKey），无第三方依赖。

热键线程把事件写入 queue，由 Qt 主线程的 QTimer 轮询。
"""

from __future__ import annotations

import ctypes
import threading
from ctypes import wintypes

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000

WM_HOTKEY = 0x0312
WM_QUIT = 0x0012

VK_Q = 0x51
VK_S = 0x53
VK_H = 0x48
VK_X = 0x58
VK_A = 0x41

DEFAULT_BINDINGS = {
    1: (MOD_CONTROL | MOD_ALT, VK_Q, "Ctrl+Alt+Q"),
    2: (MOD_CONTROL | MOD_ALT, VK_H, "Ctrl+Alt+H"),
    3: (MOD_CONTROL | MOD_ALT, VK_S, "Ctrl+Alt+S"),
    4: (MOD_CONTROL | MOD_ALT | MOD_SHIFT, VK_X, "Ctrl+Alt+Shift+X"),
    5: (MOD_CONTROL | MOD_ALT | MOD_SHIFT, VK_A, "Ctrl+Alt+Shift+A"),
}

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

_user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
_user32.RegisterHotKey.restype = wintypes.BOOL
_user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
_user32.UnregisterHotKey.restype = wintypes.BOOL
_user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
_user32.GetMessageW.restype = wintypes.BOOL
_user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
_user32.PostThreadMessageW.restype = wintypes.BOOL


class HotkeyManager(threading.Thread):
    """bindings: {id: (modifiers, virtual_key, display_name)}"""

    def __init__(self, bindings: dict[int, tuple[int, int, str]], notify) -> None:
        super().__init__(daemon=True, name="hotkey-manager")
        self.bindings = bindings
        self.notify = notify
        self._thread_id: int | None = None
        self._registered: list[int] = []

    def run(self) -> None:
        self._thread_id = _kernel32.GetCurrentThreadId()
        for hotkey_id, (mods, vk, name) in self.bindings.items():
            candidates = [(mods, name)]
            if not (mods & MOD_SHIFT):
                alt_name = name.replace("Ctrl+Alt+", "Ctrl+Alt+Shift+")
                candidates.append((mods | MOD_SHIFT, alt_name))
            registered = False
            for candidate_mods, candidate_name in candidates:
                if _user32.RegisterHotKey(None, hotkey_id, candidate_mods | MOD_NOREPEAT, vk):
                    self._registered.append(hotkey_id)
                    if candidate_name != name:
                        self.bindings[hotkey_id] = (candidate_mods, vk, candidate_name)
                        self.notify(("hotkey_fallback", name, candidate_name))
                    registered = True
                    break
                err = ctypes.get_last_error()
            if not registered:
                self.notify(("hotkey_error", name, err))
        self.notify(("hotkeys_ready",))

        msg = wintypes.MSG()
        while True:
            ret = _user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if ret <= 0:
                break
            if msg.message == WM_HOTKEY:
                self.notify(("hotkey", int(msg.wParam)))
            _user32.TranslateMessage(ctypes.byref(msg))
            _user32.DispatchMessageW(ctypes.byref(msg))
        self._unregister_all()

    def _unregister_all(self) -> None:
        for hotkey_id in self._registered:
            _user32.UnregisterHotKey(None, hotkey_id)
        self._registered.clear()

    def stop(self) -> None:
        if self._thread_id:
            _user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
