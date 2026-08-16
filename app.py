import os
import sys
import webbrowser
import json
import time
import io
import threading
import ctypes
import logging
import base64
import hashlib
import secrets
import socket
import ipaddress
from collections import deque
from ctypes import wintypes
from urllib.parse import urlsplit
from flask import Flask, Response, jsonify, request, redirect, send_file
from cheroot.ssl.builtin import BuiltinSSLAdapter
from cheroot.wsgi import Server as CherootServer
import qrcode
from tls_utils import ensure_local_tls_assets

_log_path = os.environ.get("DROSTE_LOG_PATH", "").strip()
_log_handlers = None
if _log_path:
    _log_handlers = [logging.FileHandler(_log_path, encoding="utf-8")]
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    handlers=_log_handlers,
)
logger = logging.getLogger("droste")
logging.getLogger("cheroot.access").setLevel(logging.WARNING)

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
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024

# 設定・端末情報・証明書は、ソース実行時はプロジェクト直下、
# PyInstaller版ではDroste.exeと同じ書き込み可能なフォルダーに保存する。
BASE_DIRECTORY = (
    os.path.dirname(os.path.abspath(sys.executable))
    if getattr(sys, "frozen", False)
    else os.path.dirname(os.path.abspath(__file__))
)
CONFIG_PATH = os.environ.get(
    "DROSTE_CONFIG_PATH",
    os.path.join(BASE_DIRECTORY, "config.json"),
)
DEVICE_REGISTRY_PATH = os.environ.get(
    "DROSTE_DEVICE_REGISTRY_PATH",
    os.path.join(BASE_DIRECTORY, "devices.json"),
)
CHAT_HISTORY_PATH = os.environ.get(
    "DROSTE_CHAT_HISTORY_PATH",
    os.path.join(BASE_DIRECTORY, "chat.json"),
)
TLS_BASE_DIRECTORY = os.environ.get(
    "DROSTE_TLS_DIRECTORY",
    BASE_DIRECTORY,
)

DEVICE_COOKIE_NAME = "droste_device"
LEGACY_DEVICE_COOKIE_NAME = "room_indicator_device"
DEVICE_COOKIE_MAX_AGE = 180 * 24 * 60 * 60
PAIRING_TTL_SECONDS = 120
PAIRING_CLAIM_TTL_SECONDS = 300
MAX_STREAMS_PER_DEVICE = 2
MAX_TOTAL_GUEST_STREAMS = 16
MAX_CHAT_MESSAGES = 200
MAX_CHAT_MESSAGE_LENGTH = 140
MAX_CHAT_GROUP_NAME_LENGTH = 30
DEFAULT_CHAT_GROUP_NAME = "グループチャット"

_device_lock = threading.RLock()
_devices = {}
_chat_lock = threading.RLock()
_chat_messages = deque(maxlen=MAX_CHAT_MESSAGES)
_chat_next_id = 1
_chat_group_name = DEFAULT_CHAT_GROUP_NAME
_pairing_lock = threading.RLock()
_pairing_sessions = {}
_pairing_requests = {}
_tls_asset_lock = threading.RLock()
_tls_assets = None
_rate_limit_lock = threading.RLock()
_rate_limit_events = {}
_guest_stream_lock = threading.RLock()
_guest_stream_counts = {}
_guest_stream_total = 0

DEFAULT_CONFIG = {
    "target_window_title": "",
    "port": 5000,
    "fps": 5,
    "force_foreground": False,
    "auto_open_browser": True,
    "lan_ip": "",
    "https_port": 5443,
}

PRIVATE_LAN_NETWORKS = tuple(
    ipaddress.ip_network(cidr)
    for cidr in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
)

SELF_WINDOW_MARKERS = ("Droste",)


def is_private_lan_ipv4(value):
    try:
        address = ipaddress.ip_address(str(value or ""))
    except ValueError:
        return False
    return address.version == 4 and any(
        address in network for network in PRIVATE_LAN_NETWORKS
    )


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
    config["auto_open_browser"] = bool(config.get("auto_open_browser", True))
    # v0.5以降はLAN内HTTPS専用。旧版の公開URL・平文設定は読み捨てる。
    config.pop("pairing_base_url", None)
    config.pop("tls_enabled", None)
    try:
        config["port"] = max(1, min(65535, int(config.get("port", 5000))))
    except (TypeError, ValueError):
        config["port"] = 5000
    try:
        config["https_port"] = max(1, min(65535, int(config.get("https_port", 5443))))
    except (TypeError, ValueError):
        config["https_port"] = 5443
    if config["https_port"] == config["port"]:
        config["https_port"] = 5443 if config["port"] != 5443 else 5444
    config["lan_ip"] = str(config.get("lan_ip", "") or "").strip()
    if config["lan_ip"]:
        try:
            configured_address = ipaddress.ip_address(config["lan_ip"])
            if not is_private_lan_ipv4(configured_address):
                config["lan_ip"] = ""
        except ValueError:
            config["lan_ip"] = ""
    return config


def utc_iso(timestamp=None):
    return time.strftime(
        "%Y-%m-%dT%H:%M:%SZ",
        time.gmtime(time.time() if timestamp is None else timestamp),
    )


def hash_secret(value):
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()


def sanitize_device_name(value):
    name = " ".join(str(value or "").split())[:60]
    return name or "スマートフォン"


def sanitize_chat_text(value):
    if not isinstance(value, str):
        return ""
    return value.replace("\r\n", "\n").replace("\r", "\n").strip()


def sanitize_chat_group_name(value):
    if not isinstance(value, str):
        return ""
    return " ".join(value.split())


def load_device_registry():
    with _device_lock:
        _devices.clear()
        try:
            if not os.path.exists(DEVICE_REGISTRY_PATH):
                return
            with open(DEVICE_REGISTRY_PATH, "r", encoding="utf-8") as file:
                payload = json.load(file)
            entries = payload.get("devices", []) if isinstance(payload, dict) else []
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                device_id = str(entry.get("id", "")).strip()
                token_hash = str(entry.get("token_hash", "")).strip()
                if device_id and token_hash:
                    _devices[device_id] = entry
        except Exception as error:
            logger.error("Failed to load device registry: %s", error, exc_info=True)


def save_device_registry_locked():
    temporary_path = DEVICE_REGISTRY_PATH + ".tmp"
    payload = {"devices": list(_devices.values())}
    with open(temporary_path, "w", encoding="utf-8") as file:
        json.dump(payload, file, indent=2, ensure_ascii=False)
    os.replace(temporary_path, DEVICE_REGISTRY_PATH)


def load_chat_history():
    global _chat_group_name, _chat_next_id
    with _chat_lock:
        _chat_messages.clear()
        _chat_next_id = 1
        _chat_group_name = DEFAULT_CHAT_GROUP_NAME
        try:
            if not os.path.exists(CHAT_HISTORY_PATH):
                return
            with open(CHAT_HISTORY_PATH, "r", encoding="utf-8") as file:
                payload = json.load(file)
            loaded_group_name = sanitize_chat_group_name(
                payload.get("group_name") if isinstance(payload, dict) else None
            )
            if 0 < len(loaded_group_name) <= MAX_CHAT_GROUP_NAME_LENGTH:
                _chat_group_name = loaded_group_name
            entries = payload.get("messages", []) if isinstance(payload, dict) else []
            valid_messages = []
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                try:
                    message_id = int(entry.get("id", 0))
                except (TypeError, ValueError):
                    continue
                sender = entry.get("sender")
                text = sanitize_chat_text(entry.get("text"))
                created_at = str(entry.get("created_at", "")).strip()
                if (
                    message_id < 1
                    or not isinstance(sender, dict)
                    or not str(sender.get("id", "")).strip()
                    or not str(sender.get("name", "")).strip()
                    or not text
                    or len(text) > MAX_CHAT_MESSAGE_LENGTH
                    or not created_at
                ):
                    continue
                valid_messages.append({
                    "id": message_id,
                    "sender": {
                        "id": str(sender["id"]),
                        "name": sanitize_device_name(sender["name"]),
                        "is_host": bool(sender.get("is_host", False)),
                    },
                    "text": text,
                    "created_at": created_at,
                })
            valid_messages.sort(key=lambda message: message["id"])
            for message in valid_messages[-MAX_CHAT_MESSAGES:]:
                _chat_messages.append(message)
            if _chat_messages:
                _chat_next_id = _chat_messages[-1]["id"] + 1
        except Exception as error:
            logger.error("Failed to load chat history: %s", error, exc_info=True)


def save_chat_history_locked():
    temporary_path = CHAT_HISTORY_PATH + ".tmp"
    payload = {
        "group_name": _chat_group_name,
        "messages": list(_chat_messages),
    }
    with open(temporary_path, "w", encoding="utf-8") as file:
        json.dump(payload, file, indent=2, ensure_ascii=False)
    os.replace(temporary_path, CHAT_HISTORY_PATH)


def public_device(device):
    return {
        "id": device["id"],
        "name": device.get("name", "登録端末"),
        "created_at": device.get("created_at"),
        "last_seen_at": device.get("last_seen_at"),
    }


def get_authenticated_device():
    raw_token = request.cookies.get(DEVICE_COOKIE_NAME, "")
    if not raw_token:
        # v0.4以前に登録した端末は再登録なしで移行できる。
        raw_token = request.cookies.get(LEGACY_DEVICE_COOKIE_NAME, "")
    if not raw_token:
        return None

    token_hash = hash_secret(raw_token)
    now = time.time()
    with _device_lock:
        for device in _devices.values():
            if not secrets.compare_digest(
                str(device.get("token_hash", "")),
                token_hash,
            ):
                continue
            if device.get("revoked", False):
                return None

            last_seen_timestamp = float(device.get("last_seen_timestamp", 0.0) or 0.0)
            if now - last_seen_timestamp >= 60:
                device["last_seen_timestamp"] = now
                device["last_seen_at"] = utc_iso(now)
                try:
                    save_device_registry_locked()
                except Exception as error:
                    logger.error("Failed to update device activity: %s", error, exc_info=True)
            return public_device(device)
    return None


def get_lan_ip():
    configured_ip = load_config().get("lan_ip", "")
    if configured_ip:
        return configured_ip

    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("8.8.8.8", 80))
        detected_ip = probe.getsockname()[0]
        if is_private_lan_ipv4(detected_ip):
            return detected_ip
    except Exception:
        pass
    finally:
        probe.close()

    try:
        for detected_ip in socket.gethostbyname_ex(socket.gethostname())[2]:
            if is_private_lan_ipv4(detected_ip):
                return detected_ip
    except (OSError, ValueError):
        pass
    return "127.0.0.1"


def guest_base_url():
    config = load_config()
    return f"https://{get_lan_ip()}:{config['https_port']}"


def build_pairing_url(token):
    return f"{guest_base_url()}/pair#{token}"


def build_tls_setup_url():
    return f"{guest_base_url()}/setup"


def make_qr_data_url(value):
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=8,
        border=4,
    )
    qr.add_data(value)
    qr.make(fit=True)
    qr_image = qr.make_image(fill_color="black", back_color="white")
    with io.BytesIO() as output:
        qr_image.save(output, format="PNG")
        return "data:image/png;base64," + base64.b64encode(output.getvalue()).decode("ascii")


def get_tls_assets():
    global _tls_assets
    with _tls_asset_lock:
        if _tls_assets is None:
            lan_ip = get_lan_ip()
            _tls_assets = ensure_local_tls_assets(TLS_BASE_DIRECTORY, lan_ip)
        return _tls_assets


def is_secure_pairing_transport():
    # スマートフォン向けURLは常にローカルCAで署名したHTTPSになる。
    return True


def cleanup_pairings_locked():
    now = time.time()
    for pairing_id, session in list(_pairing_sessions.items()):
        if now > session["expires_at"] + PAIRING_CLAIM_TTL_SECONDS:
            _pairing_sessions.pop(pairing_id, None)
    for request_id, pairing_request in list(_pairing_requests.items()):
        retention_deadline = pairing_request.get(
            "claim_expires_at",
            pairing_request["expires_at"] + PAIRING_CLAIM_TTL_SECONDS,
        )
        if now > retention_deadline:
            _pairing_requests.pop(request_id, None)


load_device_registry()
load_chat_history()


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
        self.paused = False
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
                paused = self.paused

            # 誰も見ていない間は画面キャプチャ自体を止める。
            if active_clients == 0:
                self._set_status("idle_no_clients")
                last_placeholder_key = None
                time.sleep(0.2)
                continue

            if paused:
                placeholder_key = ("paused", "")
                if last_placeholder_key != placeholder_key:
                    self._publish_image(
                        create_dummy_image("Streaming paused on the host PC"),
                        "paused",
                    )
                    last_placeholder_key = placeholder_key
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
                if self.paused and status != "paused":
                    return
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
            self.status = "paused" if self.paused else "switching"

    def set_paused(self, paused):
        paused = bool(paused)
        with self.lock:
            self.paused = paused
            self.status = "paused" if paused else "switching"
        if paused:
            self._publish_image(
                create_dummy_image("Streaming paused on the host PC"),
                "paused",
            )

    def get_state(self):
        with self.lock:
            return {
                "mode": self.mode,
                "status": self.status,
                "paused": self.paused,
                "target_window_title": self.target_title,
                "active_clients": self.active_clients,
                "local_clients": self.local_clients,
                # 旧UIとの互換用。自動フォールバックは廃止済み。
                "fallback_active": False,
            }

streamer = ScreenStreamer()


def request_hostname():
    try:
        return (urlsplit(f"//{request.host}").hostname or "").rstrip(".").lower()
    except ValueError:
        return ""


def request_server_port():
    try:
        return int(request.environ.get("SERVER_PORT", 0))
    except (TypeError, ValueError):
        return 0


def is_loopback_address(value):
    try:
        return ipaddress.ip_address(str(value or "")).is_loopback
    except ValueError:
        return False


def is_loopback_hostname(value):
    return value == "localhost" or is_loopback_address(value)


def is_local_request():
    config = load_config()
    return (
        not request.is_secure
        and is_loopback_address(request.remote_addr)
        and is_loopback_hostname(request_hostname())
        and request_server_port() == config["port"]
    )


def allowed_guest_hostnames():
    allowed = {
        "localhost",
        "127.0.0.1",
        "::1",
        get_lan_ip().lower(),
    }
    hostname = socket.gethostname().strip().rstrip(".").lower()
    if hostname:
        allowed.add(hostname)
        allowed.add(f"{hostname}.local")

    return allowed


def same_origin_request():
    origin = str(request.headers.get("Origin", "") or "").strip()
    fetch_site = str(request.headers.get("Sec-Fetch-Site", "") or "").lower()
    if fetch_site in {"cross-site", "same-site"}:
        return False
    if not origin:
        return True
    try:
        parsed = urlsplit(origin)
        origin_hostname = (parsed.hostname or "").rstrip(".").lower()
        origin_port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except (ValueError, TypeError):
        return False
    return (
        parsed.scheme.lower() == request.scheme.lower()
        and origin_hostname == request_hostname()
        and origin_port == request_server_port()
    )


def security_error(message, status_code):
    response = jsonify({"status": "error", "message": message})
    response.status_code = status_code
    response.headers["Cache-Control"] = "no-store"
    return response


@app.before_request
def enforce_request_boundary():
    config = load_config()
    server_port = request_server_port()
    hostname = request_hostname()

    if server_port == config["port"] and is_loopback_address(request.remote_addr):
        if not is_loopback_hostname(hostname):
            return security_error("Invalid management host", 421)
        if request.method not in {"GET", "HEAD", "OPTIONS"} and not same_origin_request():
            return security_error("Cross-origin management request was blocked", 403)
        return None

    if hostname not in allowed_guest_hostnames():
        return security_error("Invalid guest host", 421)
    return None


@app.after_request
def add_security_headers(response):
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault(
        "Permissions-Policy",
        "camera=(), microphone=(), geolocation=(), payment=(), usb=()",
    )
    response.headers.setdefault("Cross-Origin-Resource-Policy", "same-origin")
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; base-uri 'none'; frame-ancestors 'none'; "
        "form-action 'self'; object-src 'none'; "
        "img-src 'self' data: blob:; "
        "style-src 'self' 'unsafe-inline'; "
        "script-src 'self' 'unsafe-inline'; "
        "connect-src 'self'; manifest-src 'self'; worker-src 'self'",
    )
    if request.is_secure:
        response.headers.setdefault(
            "Strict-Transport-Security",
            "max-age=31536000",
        )
    if request.path.startswith("/api/") or request.path == "/stream":
        response.headers["Cache-Control"] = "no-store"
    return response


def rate_limit_response(bucket, limit, window_seconds):
    now = time.monotonic()
    remote_address = str(request.remote_addr or "unknown")
    key = (bucket, remote_address)
    with _rate_limit_lock:
        events = _rate_limit_events.setdefault(key, deque())
        cutoff = now - window_seconds
        while events and events[0] <= cutoff:
            events.popleft()
        if len(events) >= limit:
            retry_after = max(1, int(window_seconds - (now - events[0])))
            response = security_error("Too many requests", 429)
            response.headers["Retry-After"] = str(retry_after)
            return response
        events.append(now)
    return None


def acquire_guest_stream(device_id):
    global _guest_stream_total
    with _guest_stream_lock:
        device_count = _guest_stream_counts.get(device_id, 0)
        if (
            device_count >= MAX_STREAMS_PER_DEVICE
            or _guest_stream_total >= MAX_TOTAL_GUEST_STREAMS
        ):
            return False
        _guest_stream_counts[device_id] = device_count + 1
        _guest_stream_total += 1
        return True


def release_guest_stream(device_id):
    global _guest_stream_total
    with _guest_stream_lock:
        device_count = _guest_stream_counts.get(device_id, 0)
        if device_count <= 1:
            _guest_stream_counts.pop(device_id, None)
        else:
            _guest_stream_counts[device_id] = device_count - 1
        _guest_stream_total = max(0, _guest_stream_total - 1)


def authentication_required_response():
    return jsonify({
        "status": "unauthorized",
        "requires_pairing": True,
        "message": "この端末はホストPCに登録されていません",
    }), 401


def require_local_request():
    if is_local_request():
        return None
    return jsonify({
        "status": "error",
        "message": "この操作はホストPCでのみ利用できます",
    }), 403


def get_chat_participant():
    if is_local_request():
        return {
            "id": "host",
            "name": "ホストPC",
            "is_host": True,
        }
    device = get_authenticated_device()
    if device is None:
        return None
    return {
        "id": f"device:{device['id']}",
        "name": device["name"],
        "is_host": False,
    }


@app.route('/')
def index():
    return redirect('/static/index.html')


@app.route('/pair')
@app.route('/pair/<token>')
def pairing_page(token=None):
    response = app.send_static_file("pair.html")
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


@app.route('/setup')
def tls_setup_page():
    response = app.send_static_file("setup.html")
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


@app.route('/tls/droste-ca.crt')
def download_tls_ca():
    limited = rate_limit_response("tls-download", 10, 60)
    if limited:
        return limited
    assets = get_tls_assets()
    response = send_file(
        assets["ca_der"],
        mimetype="application/x-x509-ca-cert",
        as_attachment=True,
        download_name="droste-ca.crt",
        max_age=0,
    )
    response.headers["Cache-Control"] = "no-store"
    return response


@app.route('/tls/droste-ca.mobileconfig')
def download_tls_mobileconfig():
    limited = rate_limit_response("tls-download", 10, 60)
    if limited:
        return limited
    assets = get_tls_assets()
    response = send_file(
        assets["mobileconfig"],
        mimetype="application/x-apple-aspen-config",
        as_attachment=True,
        download_name="droste-ca.mobileconfig",
        max_age=0,
    )
    response.headers["Cache-Control"] = "no-store"
    return response


@app.route('/api/tls/status')
def api_tls_status():
    local_error = require_local_request()
    if local_error:
        return local_error
    config = load_config()
    assets = get_tls_assets()
    setup_url = build_tls_setup_url()
    return jsonify({
        "status": "success",
        "tls_enabled": True,
        "https_port": config["https_port"],
        "setup_url": setup_url,
        "setup_qr_data_url": make_qr_data_url(setup_url),
        "ca_fingerprint_sha256": assets["ca_fingerprint_sha256"],
    })


@app.route('/api/tls/fingerprint')
def api_tls_fingerprint():
    limited = rate_limit_response("tls-fingerprint", 30, 60)
    if limited:
        return limited
    assets = get_tls_assets()
    return jsonify({
        "status": "success",
        "ca_fingerprint_sha256": assets["ca_fingerprint_sha256"],
    })


@app.route('/api/auth/status')
def api_auth_status():
    if is_local_request():
        return jsonify({
            "app": "Droste",
            "authorized": True,
            "is_host": True,
            "device": None,
        })
    device = get_authenticated_device()
    return jsonify({
        "app": "Droste",
        "authorized": device is not None,
        "is_host": False,
        "device": device,
        "requires_pairing": device is None,
    }), 200 if device else 401


@app.route('/api/chat/messages', methods=['GET', 'POST'])
def api_chat_messages():
    participant = get_chat_participant()
    if participant is None:
        return authentication_required_response()

    if request.method == 'GET':
        try:
            after_id = int(request.args.get("after", "0"))
        except (TypeError, ValueError):
            return jsonify({
                "status": "error",
                "message": "afterには0以上のメッセージIDを指定してください",
            }), 400
        if after_id < 0:
            return jsonify({
                "status": "error",
                "message": "afterには0以上のメッセージIDを指定してください",
            }), 400
        with _chat_lock:
            messages = [
                message for message in _chat_messages
                if message["id"] > after_id
            ]
            latest_id = _chat_messages[-1]["id"] if _chat_messages else 0
            group_name = _chat_group_name
        return jsonify({
            "status": "success",
            "participant": participant,
            "group": {
                "name": group_name,
                "can_edit": participant["is_host"],
            },
            "messages": messages,
            "latest_id": latest_id,
        })

    if not same_origin_request():
        return security_error("Cross-origin chat request was blocked", 403)
    limited = rate_limit_response(
        f"chat-send:{participant['id']}",
        20,
        60,
    )
    if limited:
        return limited

    data = request.get_json(silent=True) or {}
    text = sanitize_chat_text(data.get("text"))
    if not text:
        return jsonify({
            "status": "error",
            "message": "メッセージを入力してください",
        }), 400
    if len(text) > MAX_CHAT_MESSAGE_LENGTH:
        return jsonify({
            "status": "error",
            "message": f"メッセージは{MAX_CHAT_MESSAGE_LENGTH}文字以内で入力してください",
        }), 400

    global _chat_next_id
    with _chat_lock:
        previous_messages = list(_chat_messages)
        message = {
            "id": _chat_next_id,
            "sender": participant,
            "text": text,
            "created_at": utc_iso(),
        }
        _chat_messages.append(message)
        try:
            save_chat_history_locked()
        except Exception as error:
            _chat_messages.clear()
            _chat_messages.extend(previous_messages)
            logger.error("Failed to save chat message: %s", error, exc_info=True)
            return jsonify({
                "status": "error",
                "message": "メッセージを保存できませんでした",
            }), 500
        _chat_next_id += 1
    return jsonify({
        "status": "success",
        "message": message,
    }), 201


@app.route('/api/chat/group', methods=['PATCH'])
def api_chat_group():
    local_error = require_local_request()
    if local_error:
        return local_error
    data = request.get_json(silent=True) or {}
    name = sanitize_chat_group_name(data.get("name"))
    if not name:
        return jsonify({
            "status": "error",
            "message": "グループ名を入力してください",
        }), 400
    if len(name) > MAX_CHAT_GROUP_NAME_LENGTH:
        return jsonify({
            "status": "error",
            "message": f"グループ名は{MAX_CHAT_GROUP_NAME_LENGTH}文字以内で入力してください",
        }), 400

    global _chat_group_name
    with _chat_lock:
        previous_name = _chat_group_name
        _chat_group_name = name
        try:
            save_chat_history_locked()
        except Exception as error:
            _chat_group_name = previous_name
            logger.error("Failed to save chat group name: %s", error, exc_info=True)
            return jsonify({
                "status": "error",
                "message": "グループ名を保存できませんでした",
            }), 500
    return jsonify({
        "status": "success",
        "group": {
            "name": name,
            "can_edit": True,
        },
    })


@app.route('/api/pairing/start', methods=['POST'])
def api_pairing_start():
    local_error = require_local_request()
    if local_error:
        return local_error

    token = secrets.token_urlsafe(32)
    pairing_id = secrets.token_urlsafe(12)
    verification_code = f"{secrets.randbelow(1_000_000):06d}"
    expires_at = time.time() + PAIRING_TTL_SECONDS
    pairing_url = build_pairing_url(token)

    qr_data_url = make_qr_data_url(pairing_url)

    with _pairing_lock:
        cleanup_pairings_locked()
        _pairing_sessions[pairing_id] = {
            "id": pairing_id,
            "token_hash": hash_secret(token),
            "verification_code": verification_code,
            "pairing_url": pairing_url,
            "created_at": time.time(),
            "expires_at": expires_at,
            "used": False,
        }

    return jsonify({
        "status": "success",
        "pairing_id": pairing_id,
        "pairing_url": pairing_url,
        "qr_data_url": qr_data_url,
        "verification_code": verification_code,
        "expires_at": utc_iso(expires_at),
        "expires_in": PAIRING_TTL_SECONDS,
        "transport_secure": is_secure_pairing_transport(),
    })


@app.route('/api/pairing/request', methods=['POST'])
def api_pairing_request():
    limited = rate_limit_response("pairing-request", 10, 60)
    if limited:
        return limited
    data = request.get_json(silent=True) or {}
    token = str(data.get("token", "")).strip()
    if not token:
        return jsonify({"status": "error", "message": "登録用トークンがありません"}), 400

    token_hash = hash_secret(token)
    now = time.time()
    with _pairing_lock:
        cleanup_pairings_locked()
        session = next(
            (
                entry for entry in _pairing_sessions.values()
                if entry.get("token_hash") == token_hash
            ),
            None,
        )
        if session is None or now > session["expires_at"]:
            return jsonify({"status": "expired", "message": "QRコードの有効期限が切れています"}), 410
        if session.get("used"):
            return jsonify({"status": "used", "message": "このQRコードは使用済みです"}), 409

        request_id = secrets.token_urlsafe(16)
        request_secret = secrets.token_urlsafe(32)
        session["used"] = True
        session["request_id"] = request_id
        _pairing_requests[request_id] = {
            "id": request_id,
            "pairing_id": session["id"],
            "secret_hash": hash_secret(request_secret),
            "device_name": sanitize_device_name(data.get("device_name")),
            "verification_code": session["verification_code"],
            "created_at": now,
            "expires_at": session["expires_at"],
            "status": "pending",
        }

    return jsonify({
        "status": "pending",
        "request_id": request_id,
        "request_secret": request_secret,
        "verification_code": session["verification_code"],
        "expires_at": utc_iso(session["expires_at"]),
    })


@app.route('/api/pairing/pending')
def api_pairing_pending():
    local_error = require_local_request()
    if local_error:
        return local_error

    now = time.time()
    with _pairing_lock:
        cleanup_pairings_locked()
        pending = [
            {
                "request_id": entry["id"],
                "device_name": entry["device_name"],
                "verification_code": entry["verification_code"],
                "created_at": utc_iso(entry["created_at"]),
                "expires_at": utc_iso(entry["expires_at"]),
            }
            for entry in _pairing_requests.values()
            if entry.get("status") == "pending" and now <= entry["expires_at"]
        ]
    return jsonify({"status": "success", "requests": pending})


@app.route('/api/pairing/<request_id>/approve', methods=['POST'])
def api_pairing_approve(request_id):
    local_error = require_local_request()
    if local_error:
        return local_error

    data = request.get_json(silent=True) or {}
    now = time.time()
    with _pairing_lock:
        cleanup_pairings_locked()
        pairing_request = _pairing_requests.get(request_id)
        if pairing_request is None:
            return jsonify({"status": "error", "message": "登録要求が見つかりません"}), 404
        if pairing_request.get("status") != "pending":
            return jsonify({"status": "error", "message": "この登録要求は処理済みです"}), 409
        if now > pairing_request["expires_at"]:
            pairing_request["status"] = "expired"
            return jsonify({"status": "expired", "message": "登録要求の有効期限が切れています"}), 410

        device_id = secrets.token_urlsafe(12)
        device_token = secrets.token_urlsafe(32)
        device_name = sanitize_device_name(
            data.get("device_name") or pairing_request["device_name"]
        )
        device = {
            "id": device_id,
            "name": device_name,
            "token_hash": hash_secret(device_token),
            "created_at": utc_iso(now),
            "last_seen_at": None,
            "last_seen_timestamp": 0.0,
            "revoked": False,
        }
        with _device_lock:
            _devices[device_id] = device
            try:
                save_device_registry_locked()
            except Exception as error:
                _devices.pop(device_id, None)
                logger.error("Failed to save approved device: %s", error, exc_info=True)
                return jsonify({"status": "error", "message": "端末情報を保存できませんでした"}), 500

        pairing_request["status"] = "approved"
        pairing_request["approved_token"] = device_token
        pairing_request["device_id"] = device_id
        pairing_request["device_name"] = device_name
        pairing_request["claim_expires_at"] = now + PAIRING_CLAIM_TTL_SECONDS

    return jsonify({"status": "approved", "device": public_device(device)})


@app.route('/api/pairing/<request_id>/reject', methods=['POST'])
def api_pairing_reject(request_id):
    local_error = require_local_request()
    if local_error:
        return local_error

    with _pairing_lock:
        pairing_request = _pairing_requests.get(request_id)
        if pairing_request is None:
            return jsonify({"status": "error", "message": "登録要求が見つかりません"}), 404
        if pairing_request.get("status") != "pending":
            return jsonify({"status": "error", "message": "この登録要求は処理済みです"}), 409
        pairing_request["status"] = "rejected"
    return jsonify({"status": "rejected"})


@app.route('/api/pairing/status', methods=['POST'])
def api_pairing_status():
    limited = rate_limit_response("pairing-status", 90, 60)
    if limited:
        return limited
    data = request.get_json(silent=True) or {}
    request_id = str(data.get("request_id", "")).strip()
    request_secret = str(data.get("request_secret", "")).strip()
    if not request_id or not request_secret:
        return jsonify({"status": "error", "message": "登録確認情報が不足しています"}), 400

    now = time.time()
    with _pairing_lock:
        cleanup_pairings_locked()
        pairing_request = _pairing_requests.get(request_id)
        if pairing_request is None or not secrets.compare_digest(
            pairing_request.get("secret_hash", ""),
            hash_secret(request_secret),
        ):
            return jsonify({"status": "error", "message": "登録要求が見つかりません"}), 404

        status = pairing_request.get("status")
        if status == "pending" and now > pairing_request["expires_at"]:
            pairing_request["status"] = "expired"
            status = "expired"

        if status == "approved":
            if now > pairing_request.get("claim_expires_at", 0):
                return jsonify({"status": "expired", "message": "端末登録の受け取り期限が切れています"}), 410
            approved_token = pairing_request.pop("approved_token", "")
            if not approved_token:
                return jsonify({"status": "claimed", "message": "端末登録情報は受け取り済みです"}), 409
            secure_transport = is_secure_pairing_transport()
            pairing_request["status"] = "claimed"
            pairing_request["claimed_at"] = now
            response = jsonify({
                "status": "approved",
                "device_name": pairing_request["device_name"],
                "transport_secure": secure_transport,
            })
            response.set_cookie(
                DEVICE_COOKIE_NAME,
                approved_token,
                max_age=DEVICE_COOKIE_MAX_AGE,
                httponly=True,
                secure=secure_transport,
                samesite="Strict",
                path="/",
            )
            response.headers["Cache-Control"] = "no-store"
            return response

        if status == "rejected":
            return jsonify({"status": "rejected", "message": "ホストが登録要求を拒否しました"}), 403
        if status == "expired":
            return jsonify({"status": "expired", "message": "登録要求の有効期限が切れています"}), 410
        if status == "claimed":
            return jsonify({"status": "claimed", "message": "端末登録情報は受け取り済みです"}), 409
        return jsonify({"status": "pending"})


@app.route('/api/devices')
def api_devices():
    local_error = require_local_request()
    if local_error:
        return local_error
    with _device_lock:
        devices = [public_device(device) for device in _devices.values() if not device.get("revoked")]
    return jsonify({"status": "success", "devices": devices})


@app.route('/api/devices/<device_id>', methods=['DELETE'])
def api_device_delete(device_id):
    local_error = require_local_request()
    if local_error:
        return local_error
    with _device_lock:
        device = _devices.get(device_id)
        if device is None:
            return jsonify({"status": "error", "message": "登録端末が見つかりません"}), 404
        _devices.pop(device_id, None)
        try:
            save_device_registry_locked()
        except Exception as error:
            _devices[device_id] = device
            logger.error("Failed to remove device: %s", error, exc_info=True)
            return jsonify({"status": "error", "message": "端末情報を更新できませんでした"}), 500
    return jsonify({"status": "success"})


@app.route('/stream')
def stream():
    is_local = is_local_request()
    device = None if is_local else get_authenticated_device()
    if not is_local:
        if device is None:
            return authentication_required_response()
        if not acquire_guest_stream(device["id"]):
            return security_error("Too many active streams for this device", 429)

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
            if device is not None:
                release_guest_stream(device["id"])
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
        is_local = is_local_request()
        device = None if is_local else get_authenticated_device()
        if not is_local and device is None:
            return authentication_required_response()
        return jsonify({
            "can_configure": is_local,
            "device": device,
            **streamer.get_state(),
        })


@app.route('/api/pause', methods=['POST'])
def api_pause():
    local_error = require_local_request()
    if local_error:
        return local_error
    data = request.get_json(silent=True) or {}
    paused = data.get("paused")
    if not isinstance(paused, bool):
        return jsonify({
            "status": "error",
            "message": "paused must be true or false",
        }), 400
    streamer.set_paused(paused)
    return jsonify({
        "result": "success",
        "can_configure": True,
        **streamer.get_state(),
    })


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

def run_server():
    config = load_config()
    port = config.get("port", 5000)
    https_port = config.get("https_port", 5443)

    streamer.start()

    if (
        config.get("auto_open_browser", False)
        and os.environ.get("DROSTE_NO_BROWSER") != "1"
    ):
        def open_browser():
            time.sleep(1.0)
            try:
                webbrowser.open(f"http://localhost:{port}/")
            except Exception as e:
                print(f"Failed to open browser: {e}")

        threading.Thread(target=open_browser, daemon=True).start()

    local_ip = get_lan_ip()
    assets = get_tls_assets()
    host_server = CherootServer(("127.0.0.1", port), app, numthreads=12)
    guest_server = CherootServer((local_ip, https_port), app, numthreads=32)
    guest_server.ssl_adapter = BuiltinSSLAdapter(
        assets["server_cert"],
        assets["server_key"],
    )

    def start_host_server():
        try:
            host_server.start()
        except Exception as error:
            logger.error("Host management server stopped: %s", error, exc_info=True)

    host_thread = threading.Thread(
        target=start_host_server,
        name="host-http-server",
        daemon=True,
    )
    host_thread.start()

    print("\n" + "="*68)
    print(" 【ホストPCの管理画面】")
    print(f"  --> http://localhost:{port}/")
    print(" 【スマートフォンの証明書初期設定】")
    print(f"  --> https://{local_ip}:{https_port}/setup")
    print(" 【登録済みスマートフォンの映像画面】")
    print(f"  --> https://{local_ip}:{https_port}/static/index.html")
    print("  ※ PCとスマホが同じWi-Fiルーターに接続されている必要があります。")
    print("="*68 + "\n")
    logger.info(
        "Droste server ready: management=http://localhost:%s/ guest=https://%s:%s/",
        port,
        local_ip,
        https_port,
    )

    try:
        guest_server.start()
    finally:
        try:
            guest_server.stop()
        finally:
            host_server.stop()


if __name__ == '__main__':
    run_server()
