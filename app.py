import os
import sys
import webbrowser
import json
import time
import io
import threading
import ctypes
import logging
from ctypes import wintypes
from flask import Flask, Response, jsonify, request, send_from_directory, redirect

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("room_indicator")

CAPTURE_ERROR_LOG_INTERVAL = 30.0
_capture_error_times = {}
_capture_error_lock = threading.Lock()


def log_capture_exception(key, message, error):
    """同じキャプチャエラーを連続出力せず、一定間隔で記録する。"""
    now = time.monotonic()
    with _capture_error_lock:
        last_logged = _capture_error_times.get(key, 0.0)
        if now - last_logged < CAPTURE_ERROR_LOG_INTERVAL:
            return
        _capture_error_times[key] = now
    logger.warning("%s: %s", message, error, exc_info=True)

# WindowsのDPIスケーリング対策 (DPI-aware設定)
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2) # PROCESS_PER_MONITOR_DPI_AWARE
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

try:
    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _print_window = _user32.PrintWindow
    _print_window.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
    _print_window.restype = wintypes.BOOL
except (AttributeError, OSError) as error:
    _print_window = None
    logger.error("PrintWindow API is unavailable: %s", error)

import win32gui
import win32ui
import win32con
from PIL import Image, ImageGrab, ImageDraw

app = Flask(__name__, static_folder='static', static_url_path='/static')
app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 0

# 設定ファイルのパス
CONFIG_PATH = os.path.join(os.path.dirname(__file__), 'config.json')

DEFAULT_CONFIG = {
    "target_window_title": "",
    "port": 5000,
    "fps": 5,
    "force_foreground": False,
    "auto_open_browser": False,
}

SELF_WINDOW_MARKERS = ("Room Indicator Stream",)

def load_config():
    config = DEFAULT_CONFIG.copy()
    try:
        if os.path.exists(CONFIG_PATH):
            with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
                loaded = json.load(f)
                if isinstance(loaded, dict):
                    config.update(loaded)
    except Exception as e:
        print(f"Error loading config.json: {e}")

    config["target_window_title"] = str(
        config.get("target_window_title", "") or ""
    ).strip()
    try:
        config["fps"] = max(1, min(30, int(config.get("fps", 5))))
    except (TypeError, ValueError):
        config["fps"] = 5
    config["force_foreground"] = bool(config.get("force_foreground", False))
    config["auto_open_browser"] = bool(config.get("auto_open_browser", False))
    return config


def is_self_window_title(title):
    return any(marker.lower() in title.lower() for marker in SELF_WINDOW_MARKERS)

# ウィンドウハンドルを部分一致で検索
def get_window_hwnd(title_substring):
    title_substring = str(title_substring or "").strip()
    if not title_substring:
        return None

    hwnds = []
    def enum_windows_callback(hwnd, extra):
        if win32gui.IsWindowVisible(hwnd):
            title = win32gui.GetWindowText(hwnd)
            if (
                title
                and not is_self_window_title(title)
                and title_substring.lower() in title.lower()
            ):
                hwnds.append(hwnd)
    win32gui.EnumWindows(enum_windows_callback, None)
    return hwnds[0] if hwnds else None

# 非アクティブ（バックグラウンド）ウィンドウのキャプチャ
def capture_window_background(hwnd):
    hwndDC = None
    mfcDC = None
    saveDC = None
    saveBitMap = None
    previous_bitmap = None
    try:
        if win32gui.IsIconic(hwnd):
            return None

        left, top, right, bot = win32gui.GetWindowRect(hwnd)
        w = right - left
        h = bot - top
        if w <= 0 or h <= 0:
            return None

        hwndDC = win32gui.GetWindowDC(hwnd)
        mfcDC  = win32ui.CreateDCFromHandle(hwndDC)
        saveDC = mfcDC.CreateCompatibleDC()

        saveBitMap = win32ui.CreateBitmap()
        saveBitMap.CreateCompatibleBitmap(mfcDC, w, h)
        previous_bitmap = saveDC.SelectObject(saveBitMap)

        if _print_window is None:
            raise RuntimeError("PrintWindow API is unavailable")

        result = _print_window(hwnd, saveDC.GetSafeHdc(), 2)
        if result != 1:
            result = _print_window(hwnd, saveDC.GetSafeHdc(), 0)

        bmpinfo = saveBitMap.GetInfo()
        bmpstr = saveBitMap.GetBitmapBits(True)

        # GDIビットマップから独立したコピーを作り、GDI解放後も安全に使えるようにする。
        im = Image.frombuffer(
            'RGB',
            (bmpinfo['bmWidth'], bmpinfo['bmHeight']),
            bmpstr, 'raw', 'BGRX', 0, 1).copy()

        if result == 1:
            with im.convert("L") as grayscale:
                extrema = grayscale.getextrema()
            if extrema == (0, 0) or extrema == (255, 255):
                im.close()
                return None
            return im

        im.close()
        return None
    except Exception as error:
        log_capture_exception(
            "background_capture",
            "Background window capture failed",
            error,
        )
    finally:
        # 選択中のGDIオブジェクトは削除できないため、必ず元へ戻してから削除する。
        if saveDC is not None and previous_bitmap is not None:
            try:
                saveDC.SelectObject(previous_bitmap)
            except Exception as error:
                log_capture_exception("restore_bitmap", "Failed to restore GDI bitmap", error)
        if saveBitMap is not None:
            try:
                win32gui.DeleteObject(saveBitMap.GetHandle())
            except Exception as error:
                log_capture_exception("delete_bitmap", "Failed to delete GDI bitmap", error)
        if saveDC is not None:
            try:
                saveDC.DeleteDC()
            except Exception as error:
                log_capture_exception("delete_save_dc", "Failed to delete compatible DC", error)
        if mfcDC is not None:
            try:
                mfcDC.DeleteDC()
            except Exception as error:
                log_capture_exception("delete_mfc_dc", "Failed to delete window DC wrapper", error)
        if hwndDC is not None and hwnd is not None:
            try:
                win32gui.ReleaseDC(hwnd, hwndDC)
            except Exception as error:
                log_capture_exception("release_window_dc", "Failed to release window DC", error)
    return None

# フォアグラウンド領域の切り抜きキャプチャ
def capture_window_foreground_crop(hwnd, force_foreground=False):
    try:
        if win32gui.IsIconic(hwnd) and not force_foreground:
            return None

        if force_foreground:
            if win32gui.IsIconic(hwnd):
                win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            win32gui.SetForegroundWindow(hwnd)
            time.sleep(0.05)

        left, top, right, bot = win32gui.GetWindowRect(hwnd)
        w = right - left
        h = bot - top
        if w <= 0 or h <= 0:
            return None

        im = ImageGrab.grab(bbox=(left, top, right, bot))
        return im
    except Exception as error:
        log_capture_exception(
            "foreground_capture",
            "Foreground window capture failed",
            error,
        )
    return None

# 画面全体キャプチャ
def capture_full_screen():
    try:
        return ImageGrab.grab()
    except Exception as error:
        log_capture_exception("full_screen_capture", "Desktop capture failed", error)
    return None

# ダミー画像の生成 (非対話セッションやエラー時のプレースホルダー)
def create_dummy_image(text="No Screen Input"):
    try:
        img = Image.new('RGB', (800, 600), color='#101726')
        draw = ImageDraw.Draw(img)

        # インジケーター風の円形
        center_x, center_y = 400, 240
        draw.ellipse([center_x - 30, center_y - 30, center_x + 30, center_y + 30], outline='#00f0ff', width=3)
        draw.ellipse([center_x - 10, center_y - 10, center_x + 10, center_y + 10], fill='#00f0ff')

        # ステータステキストの描画 (標準フォントを使用)
        draw.text((400, 320), text, fill='#f3f4f6', anchor="mm")
        draw.text((400, 360), "[Demo Stream Mode]", fill='#9ca3af', anchor="mm")

        return img
    except Exception as e:
        print(f"Error creating dummy image: {e}")
        return Image.new('RGB', (100, 100), color='#101726')

# ストリーミング管理クラス
class ScreenStreamer:
    def __init__(self):
        self.lock = threading.Lock()
        self.latest_frame = None
        self.mode = "app"  # "app" または "full"
        self.status = "starting"
        self.target_title = ""
        self.active_clients = 0
        self.local_clients = 0
        self.running = True

    def start(self):
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def _loop(self):
        last_placeholder_key = None
        while self.running:
            start_time = time.time()
            img = None

            with self.lock:
                active_clients = self.active_clients
                local_clients = self.local_clients
                mode = self.mode

            # 誰も見ていない間は画面キャプチャ自体を止める。
            if active_clients == 0:
                self._set_status("idle_no_clients")
                last_placeholder_key = None
                time.sleep(0.2)
                continue

            # 設定を動的に読み込む
            config = load_config()
            target_title = config.get("target_window_title", "")
            force_foreground = config.get("force_foreground", False)
            fps = config.get("fps", 5)
            self.target_title = target_title

            if mode == "app":
                if not target_title:
                    placeholder_key = ("target_not_configured", "")
                    if last_placeholder_key != placeholder_key:
                        self._publish_image(
                            create_dummy_image("Select a target window in Settings"),
                            "target_not_configured",
                        )
                        last_placeholder_key = placeholder_key
                    time.sleep(0.2)
                    continue

                hwnd = get_window_hwnd(target_title)
                if hwnd:
                    if win32gui.IsIconic(hwnd) and not force_foreground:
                        placeholder_key = ("target_minimized", target_title)
                        if last_placeholder_key != placeholder_key:
                            self._publish_image(
                                create_dummy_image("Restore the target window to resume"),
                                "target_minimized",
                            )
                            last_placeholder_key = placeholder_key
                        time.sleep(0.2)
                        continue

                    # 1. バックグラウンドキャプチャを試行
                    img = capture_window_background(hwnd)
                    # 2. 失敗時はフォアグラウンド領域の切り抜き
                    if img is None:
                        img = capture_window_foreground_crop(hwnd, force_foreground)
                    # 3. 両方失敗した場合はダミー画像 (アプリ配信エラー)
                    if img is None:
                        placeholder_key = ("capture_failed", target_title)
                        if last_placeholder_key != placeholder_key:
                            self._publish_image(
                                create_dummy_image("Target capture failed"),
                                "capture_failed",
                            )
                            last_placeholder_key = placeholder_key
                        time.sleep(0.2)
                        continue
                    status = "streaming_app"
                    last_placeholder_key = None
                else:
                    # 対象が見つからなくても、全画面には絶対に切り替えない。
                    placeholder_key = ("target_not_found", target_title)
                    if last_placeholder_key != placeholder_key:
                        self._publish_image(
                            create_dummy_image("Waiting for the target window"),
                            "target_not_found",
                        )
                        last_placeholder_key = placeholder_key
                    time.sleep(0.2)
                    continue
            else:
                # 同じPCで配信画面を開いている時は自己撮影になるため、全画面撮影を止める。
                if local_clients > 0:
                    placeholder_key = ("local_fullscreen_blocked", "")
                    if last_placeholder_key != placeholder_key:
                        self._publish_image(
                            create_dummy_image("Close the local viewer to share the desktop"),
                            "local_fullscreen_blocked",
                        )
                        last_placeholder_key = placeholder_key
                    time.sleep(0.2)
                    continue

                img = capture_full_screen()
                if img is None:
                    placeholder_key = ("capture_failed", "full")
                    if last_placeholder_key != placeholder_key:
                        self._publish_image(
                            create_dummy_image("Desktop capture failed"),
                            "capture_failed",
                        )
                        last_placeholder_key = placeholder_key
                    time.sleep(0.2)
                    continue
                status = "streaming_full"
                last_placeholder_key = None

            # 画像が取得できたらJPEG化
            if img is not None:
                self._publish_image(img, status)

            # FPS制御
            elapsed = time.time() - start_time
            sleep_time = max(0.01, (1.0 / fps) - elapsed)
            time.sleep(sleep_time)

    def get_frame(self):
        with self.lock:
            return self.latest_frame

    def _set_status(self, status):
        with self.lock:
            self.status = status

    def _publish_image(self, img, status):
        try:
            with io.BytesIO() as img_io:
                img.save(img_io, 'JPEG', quality=75)
                frame = img_io.getvalue()
            with self.lock:
                self.latest_frame = frame
                self.status = status
        finally:
            img.close()

    def add_client(self, is_local=False):
        with self.lock:
            self.active_clients += 1
            if is_local:
                self.local_clients += 1

    def remove_client(self, is_local=False):
        with self.lock:
            self.active_clients = max(0, self.active_clients - 1)
            if is_local:
                self.local_clients = max(0, self.local_clients - 1)

    def set_mode(self, mode):
        with self.lock:
            self.mode = mode
            self.status = "switching"

    def get_state(self):
        with self.lock:
            return {
                "mode": self.mode,
                "status": self.status,
                "target_window_title": self.target_title,
                "active_clients": self.active_clients,
                "local_clients": self.local_clients,
                # 旧UIとの互換用。自動フォールバックは廃止済み。
                "fallback_active": False,
            }

streamer = ScreenStreamer()


def is_local_request():
    return (request.remote_addr or "") in {"127.0.0.1", "::1"}

@app.route('/')
def index():
    return redirect('/static/index.html')

@app.route('/stream')
def stream():
    is_local = is_local_request()

    def gen():
        last_frame = None
        last_sent_at = 0.0
        streamer.add_client(is_local=is_local)
        try:
            while True:
                frame = streamer.get_frame()
                now = time.monotonic()
                # 静止画でも定期送信し、切断済みクライアントを確実に検知する。
                if frame and (frame != last_frame or now - last_sent_at >= 1.0):
                    last_frame = frame
                    last_sent_at = now
                    yield (b'--frame\r\n'
                           b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')
                time.sleep(0.03) # クライアント配信レートの調整
        finally:
            streamer.remove_client(is_local=is_local)
    return Response(gen(), mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route('/api/mode', methods=['GET', 'POST'])
def api_mode():
    if request.method == 'POST':
        if not is_local_request():
            return jsonify({"status": "error", "message": "Configuration is available only on this PC"}), 403
        data = request.json or {}
        new_mode = data.get('mode')
        if new_mode in ['app', 'full']:
            streamer.set_mode(new_mode)
            return jsonify({"result": "success", "can_configure": True, **streamer.get_state()})
        return jsonify({"status": "error", "message": "Invalid mode"}), 400
    else:
        return jsonify({"can_configure": is_local_request(), **streamer.get_state()})

@app.route('/api/windows')
def api_windows():
    if not is_local_request():
        return jsonify({"status": "error", "message": "Configuration is available only on this PC"}), 403
    windows = []
    def enum_windows_callback(hwnd, extra):
        if win32gui.IsWindowVisible(hwnd):
            title = win32gui.GetWindowText(hwnd)
            if title.strip() and not is_self_window_title(title):
                windows.append(title)
    win32gui.EnumWindows(enum_windows_callback, None)
    # 重複を排除してソート
    windows = sorted(list(set(windows)))
    return jsonify(windows)

@app.route('/api/config', methods=['GET', 'POST'])
def api_config():
    if not is_local_request():
        return jsonify({"status": "error", "message": "Configuration is available only on this PC"}), 403
    if request.method == 'POST':
        data = request.json or {}
        config = load_config()

        # 更新可能な項目
        if "target_window_title" in data:
            config["target_window_title"] = str(data["target_window_title"] or "").strip()
        if "fps" in data:
            try:
                config["fps"] = max(1, min(30, int(data["fps"])))
            except (TypeError, ValueError):
                pass
        if "force_foreground" in data:
            config["force_foreground"] = bool(data["force_foreground"])

        try:
            with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
                json.dump(config, f, indent=2, ensure_ascii=False)
            return jsonify({"status": "success", "config": config})
        except Exception as e:
            return jsonify({"status": "error", "message": str(e)}), 500
    else:
        return jsonify(load_config())

if __name__ == '__main__':
    # サーバーの起動
    config = load_config()
    port = config.get("port", 5000)

    # ストリーマの開始
    streamer.start()

    # 同じPCで全画面配信を開くと自己撮影になるため、自動起動は既定で無効。
    if config.get("auto_open_browser", False):
        def open_browser():
            time.sleep(1.0)
            try:
                webbrowser.open(f"http://localhost:{port}/")
            except Exception as e:
                print(f"Failed to open browser: {e}")

        threading.Thread(target=open_browser, daemon=True).start()

    # LAN内IPアドレスの自動取得
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(('8.8.8.8', 80))
        local_ip = s.getsockname()[0]
    except Exception:
        local_ip = '127.0.0.1'
    finally:
        s.close()

    print("\n" + "="*60)
    print(f" 【スマートフォン等からテスト接続する場合のURL】")
    print(f"  --> http://{local_ip}:{port}/")
    print(f"  ※ PCとスマホが同じWi-Fiルーターに接続されている必要があります。")
    print("="*60 + "\n")

    # 外部接続を許可するために 0.0.0.0 で起動
    app.run(host='0.0.0.0', port=port, threaded=True)
