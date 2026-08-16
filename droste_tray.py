import ctypes
import json
import os
import subprocess
import sys
import threading
import time
import traceback
import urllib.request
import webbrowser

import win32api
import win32con
import win32gui


APP_NAME = "Droste"
MUTEX_NAME = r"Local\DrosteTrayLauncher"
TRAY_MESSAGE = win32con.WM_USER + 20
CHILD_EXIT_MESSAGE = win32con.WM_APP + 1
MENU_OPEN = 1001
MENU_EXIT = 1002


def project_path(*parts):
    base_directory = (
        os.path.dirname(os.path.abspath(sys.executable))
        if getattr(sys, "frozen", False)
        else os.path.dirname(os.path.abspath(__file__))
    )
    return os.path.join(base_directory, *parts)


def resource_path(*parts):
    base_directory = getattr(
        sys,
        "_MEIPASS",
        os.path.dirname(os.path.abspath(__file__)),
    )
    return os.path.join(base_directory, *parts)


def management_url():
    port = 5000
    try:
        with open(project_path("config.json"), "r", encoding="utf-8") as file:
            config = json.load(file)
        port = max(1, min(65535, int(config.get("port", port))))
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        pass
    return f"http://localhost:{port}/"


def server_is_ready(url):
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(url + "api/auth/status", timeout=0.4) as response:
            payload = json.load(response)
            return (
                response.status == 200
                and payload.get("app") == APP_NAME
                and payload.get("is_host") is True
            )
    except (OSError, ValueError):
        return False


def open_management_url(url):
    if os.environ.get("DROSTE_NO_BROWSER") == "1":
        return
    webbrowser.open(url)


def log_launcher_error():
    try:
        with open(project_path("droste.log"), "a", encoding="utf-8") as file:
            file.write(
                f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] "
                "Droste launcher failed\n"
            )
            file.write(traceback.format_exc())
    except OSError:
        pass


def acquire_single_instance():
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.CreateMutexW(None, False, MUTEX_NAME)
    if not handle:
        raise ctypes.WinError()
    return handle, kernel32.GetLastError() == 183


class DrosteTray:
    def __init__(self, mutex_handle):
        self.mutex_handle = mutex_handle
        self.child = None
        self.log_file = None
        self.closing = False
        self.icon_added = False
        self.management_url = management_url()
        self.hwnd = self._create_window()
        self.icon = self._load_icon()
        self.notify_id = (
            self.hwnd,
            0,
            win32gui.NIF_ICON | win32gui.NIF_MESSAGE | win32gui.NIF_TIP,
            TRAY_MESSAGE,
            self.icon,
            "Droste - 右クリックで操作",
        )
        win32gui.Shell_NotifyIcon(win32gui.NIM_ADD, self.notify_id)
        self.icon_added = True

    def _create_window(self):
        instance = win32api.GetModuleHandle(None)
        window_class = win32gui.WNDCLASS()
        window_class.hInstance = instance
        window_class.lpszClassName = "DrosteTrayWindow"
        window_class.lpfnWndProc = {
            win32con.WM_DESTROY: self._on_destroy,
            win32con.WM_COMMAND: self._on_command,
            win32con.WM_ENDSESSION: self._on_end_session,
            TRAY_MESSAGE: self._on_tray_message,
            CHILD_EXIT_MESSAGE: self._on_child_exit,
        }
        try:
            class_atom = win32gui.RegisterClass(window_class)
        except win32gui.error as error:
            if error.winerror != 1410:
                raise
            class_atom = window_class.lpszClassName
        return win32gui.CreateWindow(
            class_atom,
            "Droste",
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            instance,
            None,
        )

    def _load_icon(self):
        icon_path = resource_path("droste.ico")
        return win32gui.LoadImage(
            0,
            icon_path,
            win32con.IMAGE_ICON,
            0,
            0,
            win32con.LR_LOADFROMFILE | win32con.LR_DEFAULTSIZE,
        )

    def start_server(self):
        if server_is_ready(self.management_url):
            self.open_management_page()
            return False

        if getattr(sys, "frozen", False):
            command = [sys.executable, "--server"]
        else:
            python_executable = project_path(".venv", "Scripts", "python.exe")
            app_path = project_path("app.py")
            if not os.path.isfile(python_executable):
                raise FileNotFoundError("初期設定が終わっていません。setup.batを実行してください。")
            command = [python_executable, app_path]

        environment = os.environ.copy()
        environment["DROSTE_NO_BROWSER"] = "1"
        log_path = project_path("droste.log")
        with open(log_path, "a", encoding="utf-8") as log_file:
            log_file.write(
                f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] Droste starting\n"
            )
        if getattr(sys, "frozen", False):
            environment["DROSTE_LOG_PATH"] = log_path
            child_output = subprocess.DEVNULL
        else:
            self.log_file = open(
                log_path,
                "a",
                encoding="utf-8",
                buffering=1,
            )
            child_output = self.log_file
        self.child = subprocess.Popen(
            command,
            cwd=project_path(),
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=child_output,
            stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        threading.Thread(target=self._wait_for_server, daemon=True).start()
        threading.Thread(target=self._monitor_child, daemon=True).start()
        return True

    def _wait_for_server(self):
        # 初回はウイルス対策ソフトによる単体EXEの展開確認に時間がかかることがある。
        for _index in range(300):
            if self.closing or self.child is None or self.child.poll() is not None:
                return
            if server_is_ready(self.management_url):
                self.open_management_page()
                return
            time.sleep(0.1)

    def _monitor_child(self):
        exit_code = self.child.wait()
        if not self.closing:
            win32gui.PostMessage(
                self.hwnd,
                CHILD_EXIT_MESSAGE,
                int(exit_code),
                0,
            )

    def open_management_page(self):
        open_management_url(self.management_url)

    def show_menu(self):
        menu = win32gui.CreatePopupMenu()
        try:
            win32gui.AppendMenu(menu, win32con.MF_STRING, MENU_OPEN, "管理画面を開く")
            win32gui.AppendMenu(menu, win32con.MF_SEPARATOR, 0, "")
            win32gui.AppendMenu(menu, win32con.MF_STRING, MENU_EXIT, "Drosteを終了")
            x, y = win32gui.GetCursorPos()
            win32gui.SetForegroundWindow(self.hwnd)
            win32gui.TrackPopupMenu(
                menu,
                win32con.TPM_LEFTALIGN | win32con.TPM_RIGHTBUTTON,
                x,
                y,
                0,
                self.hwnd,
                None,
            )
            win32gui.PostMessage(self.hwnd, win32con.WM_NULL, 0, 0)
        finally:
            win32gui.DestroyMenu(menu)

    def stop_server(self):
        self.closing = True
        if self.child is not None and self.child.poll() is None:
            self.child.terminate()
            try:
                self.child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.child.kill()
                self.child.wait(timeout=2)
        if self.log_file is not None:
            self.log_file.close()
            self.log_file = None

    def _on_command(self, _hwnd, _message, wparam, _lparam):
        command = win32api.LOWORD(wparam)
        if command == MENU_OPEN:
            self.open_management_page()
        elif command == MENU_EXIT:
            self.stop_server()
            win32gui.DestroyWindow(self.hwnd)
        return 0

    def _on_tray_message(self, _hwnd, _message, _wparam, lparam):
        if lparam == win32con.WM_LBUTTONDBLCLK:
            self.open_management_page()
        elif lparam in (win32con.WM_RBUTTONUP, win32con.WM_CONTEXTMENU):
            self.show_menu()
        return 0

    def _on_child_exit(self, _hwnd, _message, exit_code, _lparam):
        if not self.closing:
            if getattr(sys, "frozen", False):
                diagnostic_hint = "同じフォルダーのdroste.logを確認してください。"
            else:
                diagnostic_hint = "regain.batを実行すると詳しいエラーを確認できます。"
            win32api.MessageBox(
                self.hwnd,
                (
                    "Drosteを起動できなかったか、予期せず終了しました。\n"
                    f"{diagnostic_hint}\n\n"
                    f"終了コード: {exit_code}"
                ),
                APP_NAME,
                win32con.MB_OK | win32con.MB_ICONERROR,
            )
        win32gui.DestroyWindow(self.hwnd)
        return 0

    def _on_end_session(self, _hwnd, _message, ending, _lparam):
        if ending:
            self.stop_server()
        return 0

    def _on_destroy(self, _hwnd, _message, _wparam, _lparam):
        self.stop_server()
        if self.icon_added:
            try:
                win32gui.Shell_NotifyIcon(win32gui.NIM_DELETE, self.notify_id)
            except win32gui.error:
                pass
            self.icon_added = False
        if self.mutex_handle:
            ctypes.windll.kernel32.CloseHandle(self.mutex_handle)
            self.mutex_handle = None
        win32gui.PostQuitMessage(0)
        return 0


def main():
    if "--server" in sys.argv:
        from app import run_server

        run_server()
        return

    if "--stop" in sys.argv:
        hwnd = win32gui.FindWindow("DrosteTrayWindow", "Droste")
        if hwnd:
            win32gui.SendMessage(hwnd, win32con.WM_COMMAND, MENU_EXIT, 0)
        return

    mutex_handle, already_running = acquire_single_instance()
    if already_running:
        ctypes.windll.kernel32.CloseHandle(mutex_handle)
        open_management_url(management_url())
        return

    tray = None
    try:
        tray = DrosteTray(mutex_handle)
        if tray.start_server():
            win32gui.PumpMessages()
        else:
            win32gui.DestroyWindow(tray.hwnd)
    except Exception as error:
        log_launcher_error()
        if tray is not None:
            tray.stop_server()
        else:
            ctypes.windll.kernel32.CloseHandle(mutex_handle)
        win32api.MessageBox(
            0,
            str(error),
            APP_NAME,
            win32con.MB_OK | win32con.MB_ICONERROR,
        )


if __name__ == "__main__":
    main()
