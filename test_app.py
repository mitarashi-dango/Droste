import os
import json
import tempfile
import unittest
from unittest import mock

import app
from cryptography import x509
from cryptography.hazmat.primitives import serialization
from PIL import Image
from tls_utils import ensure_local_tls_assets


class AppRouteTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.original_config_path = app.CONFIG_PATH
        self.original_registry_path = app.DEVICE_REGISTRY_PATH
        app.CONFIG_PATH = os.path.join(self.temporary_directory.name, "config.json")
        app.DEVICE_REGISTRY_PATH = os.path.join(
            self.temporary_directory.name,
            "devices.json",
        )
        app._devices.clear()
        app._pairing_sessions.clear()
        app._pairing_requests.clear()
        app._rate_limit_events.clear()
        app._guest_stream_counts.clear()
        app._guest_stream_total = 0
        app.streamer.set_paused(False)
        self.client = app.app.test_client()

    def tearDown(self):
        app.CONFIG_PATH = self.original_config_path
        app.DEVICE_REGISTRY_PATH = self.original_registry_path
        app._devices.clear()
        app._pairing_sessions.clear()
        app._pairing_requests.clear()
        app._rate_limit_events.clear()
        app._guest_stream_counts.clear()
        app._guest_stream_total = 0
        app.streamer.set_paused(False)
        self.temporary_directory.cleanup()

    def test_management_api_is_local_only(self):
        local_response = self.client.get(
            "/api/config",
            base_url="http://localhost:5000",
            environ_base={"REMOTE_ADDR": "127.0.0.1"},
        )
        remote_response = self.client.get(
            "/api/config",
            base_url="https://localhost:5443",
            environ_base={"REMOTE_ADDR": "192.168.1.25"},
        )

        self.assertEqual(local_response.status_code, 200)
        self.assertEqual(remote_response.status_code, 403)

    def test_legacy_internet_settings_are_ignored(self):
        with open(app.CONFIG_PATH, "w", encoding="utf-8") as file:
            json.dump(
                {
                    "pairing_base_url": "https://public.example",
                    "tls_enabled": False,
                    "lan_ip": "203.0.113.10",
                },
                file,
            )

        config = app.load_config()

        self.assertNotIn("pairing_base_url", config)
        self.assertNotIn("tls_enabled", config)
        self.assertEqual(config["lan_ip"], "")

    def test_only_rfc1918_addresses_can_be_selected_for_lan(self):
        for address in ("10.0.0.8", "172.16.20.3", "192.168.1.10"):
            with self.subTest(address=address):
                self.assertTrue(app.is_private_lan_ipv4(address))

        for address in ("127.0.0.1", "169.254.1.3", "100.64.0.2", "8.8.8.8"):
            with self.subTest(address=address):
                self.assertFalse(app.is_private_lan_ipv4(address))

    @mock.patch("app.get_lan_ip", return_value="192.168.1.10")
    def test_guest_url_is_always_local_https(self, _get_lan_ip):
        self.assertEqual(app.guest_base_url(), "https://192.168.1.10:5443")

    def test_local_auth_status_identifies_droste_for_the_tray_launcher(self):
        response = self.client.get(
            "/api/auth/status",
            base_url="http://localhost:5000",
            environ_base={"REMOTE_ADDR": "127.0.0.1"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["app"], "Droste")
        self.assertIs(response.get_json()["is_host"], True)

    def test_unregistered_remote_device_cannot_open_stream(self):
        response = self.client.get(
            "/stream",
            base_url="https://localhost:5443",
            environ_base={"REMOTE_ADDR": "192.168.1.25"},
        )

        self.assertEqual(response.status_code, 401)
        self.assertTrue(response.get_json()["requires_pairing"])

    @mock.patch("app.get_lan_ip", return_value="192.168.1.10")
    def test_pairing_flow_sets_secure_cookie(self, _get_lan_ip):
        start_response = self.client.post(
            "/api/pairing/start",
            base_url="http://localhost:5000",
            environ_base={"REMOTE_ADDR": "127.0.0.1"},
        )
        self.assertEqual(start_response.status_code, 200)
        start_data = start_response.get_json()
        token = start_data["pairing_url"].split("#", 1)[1]

        request_response = self.client.post(
            "/api/pairing/request",
            json={"token": token, "device_name": "Test phone"},
            base_url="https://192.168.1.10:5443",
            environ_base={"REMOTE_ADDR": "192.168.1.25"},
        )
        self.assertEqual(request_response.status_code, 200)
        request_data = request_response.get_json()

        approve_response = self.client.post(
            f"/api/pairing/{request_data['request_id']}/approve",
            json={},
            base_url="http://localhost:5000",
            environ_base={"REMOTE_ADDR": "127.0.0.1"},
        )
        self.assertEqual(approve_response.status_code, 200)

        status_response = self.client.post(
            "/api/pairing/status",
            json={
                "request_id": request_data["request_id"],
                "request_secret": request_data["request_secret"],
            },
            base_url="https://192.168.1.10:5443",
            environ_base={"REMOTE_ADDR": "192.168.1.25"},
        )
        self.assertEqual(status_response.status_code, 200)
        cookie_header = status_response.headers["Set-Cookie"]
        self.assertIn("Secure", cookie_header)
        self.assertIn("HttpOnly", cookie_header)
        self.assertIn("SameSite=Strict", cookie_header)

    def test_management_rejects_dns_rebinding_host(self):
        response = self.client.get(
            "/api/config",
            base_url="http://attacker.example:5000",
            environ_base={"REMOTE_ADDR": "127.0.0.1"},
        )

        self.assertEqual(response.status_code, 421)

    def test_management_rejects_cross_origin_post(self):
        response = self.client.post(
            "/api/pairing/start",
            base_url="http://localhost:5000",
            headers={
                "Origin": "https://attacker.example",
                "Sec-Fetch-Site": "cross-site",
            },
            environ_base={"REMOTE_ADDR": "127.0.0.1"},
        )

        self.assertEqual(response.status_code, 403)

    def test_security_headers_are_present(self):
        response = self.client.get(
            "/api/auth/status",
            base_url="https://localhost:5443",
            environ_base={"REMOTE_ADDR": "192.168.1.25"},
        )

        self.assertEqual(response.headers["X-Frame-Options"], "DENY")
        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
        self.assertIn("frame-ancestors 'none'", response.headers["Content-Security-Policy"])
        self.assertIn("max-age=", response.headers["Strict-Transport-Security"])

    def test_pairing_request_is_rate_limited(self):
        responses = [
            self.client.post(
                "/api/pairing/request",
                json={"token": "invalid"},
                base_url="https://localhost:5443",
                environ_base={"REMOTE_ADDR": "192.168.1.25"},
            )
            for _index in range(11)
        ]

        self.assertEqual(responses[-1].status_code, 429)
        self.assertIn("Retry-After", responses[-1].headers)

    def test_guest_stream_slots_are_limited_per_device(self):
        self.assertTrue(app.acquire_guest_stream("device-1"))
        self.assertTrue(app.acquire_guest_stream("device-1"))
        self.assertFalse(app.acquire_guest_stream("device-1"))
        app.release_guest_stream("device-1")
        app.release_guest_stream("device-1")
        self.assertEqual(app._guest_stream_total, 0)

    def test_stream_pause_is_local_only_and_replaces_the_live_frame(self):
        local_response = self.client.post(
            "/api/pause",
            json={"paused": True},
            base_url="http://localhost:5000",
            environ_base={"REMOTE_ADDR": "127.0.0.1"},
        )
        remote_response = self.client.post(
            "/api/pause",
            json={"paused": False},
            base_url="https://localhost:5443",
            environ_base={"REMOTE_ADDR": "192.168.1.25"},
        )

        self.assertEqual(local_response.status_code, 200)
        self.assertTrue(local_response.get_json()["paused"])
        self.assertEqual(app.streamer.get_state()["status"], "paused")
        self.assertIsNotNone(app.streamer.get_frame())
        self.assertEqual(remote_response.status_code, 403)

    def test_stream_pause_rejects_a_late_live_frame(self):
        app.streamer.set_paused(True)
        paused_frame = app.streamer.get_frame()

        app.streamer._publish_image(
            Image.new("RGB", (8, 8), color=(255, 0, 0)),
            "streaming_app",
        )

        self.assertEqual(app.streamer.get_frame(), paused_frame)
        self.assertEqual(app.streamer.get_state()["status"], "paused")


class TlsAssetTests(unittest.TestCase):
    def test_assets_are_reused_and_key_matches_certificate(self):
        with tempfile.TemporaryDirectory() as directory:
            first = ensure_local_tls_assets(directory, "192.168.1.10")

            with open(first["ca_key"], "rb") as source:
                mismatched_key = source.read()
            with open(first["server_key"], "wb") as destination:
                destination.write(mismatched_key)

            second = ensure_local_tls_assets(directory, "192.168.1.10")
            changed_ip = ensure_local_tls_assets(directory, "192.168.1.25")

            self.assertEqual(
                first["ca_fingerprint_sha256"],
                second["ca_fingerprint_sha256"],
            )
            self.assertEqual(
                first["ca_fingerprint_sha256"],
                changed_ip["ca_fingerprint_sha256"],
            )
            for key in (
                "ca_cert",
                "ca_der",
                "ca_key",
                "server_cert",
                "server_key",
                "mobileconfig",
            ):
                self.assertTrue(os.path.isfile(second[key]))

            with open(second["server_key"], "rb") as file:
                server_key = serialization.load_pem_private_key(
                    file.read(),
                    password=None,
                )
            with open(second["server_cert"], "rb") as file:
                server_certificate = x509.load_pem_x509_certificate(file.read())
            self.assertEqual(
                server_key.public_key().public_numbers(),
                server_certificate.public_key().public_numbers(),
            )


class FrontendAssetTests(unittest.TestCase):
    def test_capture_target_apply_button_is_next_to_window_select(self):
        base_directory = os.path.dirname(__file__)
        with open(
            os.path.join(base_directory, "static", "index.html"),
            "r",
            encoding="utf-8",
        ) as file:
            index_html = file.read()

        group_start = index_html.index(
            '<div class="debug-input-group window-selection-group">'
        )
        group_end = index_html.index("</div>", group_start)
        selection_group = index_html[group_start:group_end]

        self.assertIn('id="win-select"', selection_group)
        self.assertIn(
            "saveConfig(this, 'window-config-feedback')",
            selection_group,
        )
        self.assertIn('onclick="loadWindows()">一覧を更新', selection_group)
        self.assertLess(
            selection_group.index('id="win-select"'),
            selection_group.index("この選択を適用"),
        )

    def test_host_controls_have_pause_feedback_and_collapsed_advanced_settings(self):
        base_directory = os.path.dirname(__file__)
        with open(
            os.path.join(base_directory, "static", "index.html"),
            "r",
            encoding="utf-8",
        ) as file:
            index_html = file.read()

        self.assertIn('id="btn-pause"', index_html)
        self.assertIn("fetch('/api/pause'", index_html)
        self.assertIn('id="window-config-feedback"', index_html)
        self.assertIn('aria-live="polite"', index_html)
        self.assertIn('<details class="advanced-settings">', index_html)
        self.assertIn("詳細設定（通常は変更不要）", index_html)
        self.assertIn("詳細設定を適用", index_html)
        self.assertNotIn("alert('設定を適用しました", index_html)

    def test_iphone_and_android_support_one_tap_home_screen_launch(self):
        base_directory = os.path.dirname(__file__)
        with open(
            os.path.join(base_directory, "static", "manifest.json"),
            "r",
            encoding="utf-8",
        ) as file:
            manifest = json.load(file)
        with open(
            os.path.join(base_directory, "static", "index.html"),
            "r",
            encoding="utf-8",
        ) as file:
            index_html = file.read()

        self.assertEqual(manifest["display"], "standalone")
        self.assertEqual(manifest["start_url"], "/static/index.html")
        self.assertEqual(manifest["id"], "/static/droste")
        self.assertEqual(manifest["name"], "Droste")
        self.assertEqual(manifest["short_name"], "Droste")
        self.assertIn("ホーム画面に追加", index_html)
        self.assertIn('apple-touch-icon" sizes="180x180"', index_html)
        self.assertIn("updateHomeScreenCard", index_html)
        self.assertIn("isIosDevice", index_html)
        self.assertIn("isAndroidDevice", index_html)
        self.assertIn("beforeinstallprompt", index_html)
        self.assertIn("Drosteをインストール", index_html)
        self.assertIn("アプリをインストール", index_html)
        self.assertIn("@media (display-mode: standalone)", index_html)
        self.assertNotIn("fonts.googleapis.com", index_html)

    def test_tls_setup_has_platform_specific_certificate_guidance(self):
        base_directory = os.path.dirname(__file__)
        with open(
            os.path.join(base_directory, "static", "setup.html"),
            "r",
            encoding="utf-8",
        ) as file:
            setup_html = file.read()

        self.assertIn('id="setup-ios"', setup_html)
        self.assertIn('id="setup-android"', setup_html)
        self.assertIn('/tls/droste-ca.mobileconfig', setup_html)
        self.assertIn('/tls/droste-ca.crt', setup_html)
        self.assertIn("Android版Chrome", setup_html)
        self.assertIn("CA証明書をインストール", setup_html)

    def test_droste_icons_have_required_sizes(self):
        base_directory = os.path.dirname(__file__)
        with Image.open(
            os.path.join(base_directory, "static", "icon-180.png")
        ) as icon_180:
            self.assertEqual(icon_180.size, (180, 180))
        with Image.open(
            os.path.join(base_directory, "static", "icon-192.png")
        ) as icon_192:
            self.assertEqual(icon_192.size, (192, 192))
        with Image.open(
            os.path.join(base_directory, "static", "icon-512.png")
        ) as icon_512:
            self.assertEqual(icon_512.size, (512, 512))
        with Image.open(os.path.join(base_directory, "droste.ico")) as windows_icon:
            self.assertEqual(windows_icon.format, "ICO")
            self.assertIn((256, 256), windows_icon.info["sizes"])


class DistributionAssetTests(unittest.TestCase):
    def test_source_setup_prefers_python_313_and_verifies_bundled_installer(self):
        base_directory = os.path.dirname(__file__)
        with open(
            os.path.join(base_directory, "setup.bat"),
            "r",
            encoding="ascii",
        ) as file:
            setup_script = file.read()
        with open(
            os.path.join(base_directory, "verify_python_installer.ps1"),
            "r",
            encoding="utf-8",
        ) as file:
            verifier_script = file.read()

        self.assertLess(setup_script.index("py -3.13"), setup_script.index("where python"))
        self.assertLess(
            setup_script.index("-File verify_python_installer.ps1"),
            setup_script.index('start "" /wait "%PYTHON_INSTALLER%"'),
        )
        self.assertIn("InstallLauncherAllUsers=0", setup_script)
        self.assertIn("TargetDir=\"%PYTHON_INSTALL_DIR%\"", setup_script)
        self.assertIn(
            "c54d9b9bbb8a36e6489363ddd01139707fd781d72f1f9e90c7ec65d0061368e0",
            verifier_script,
        )
        self.assertIn("Python Software Foundation", verifier_script)

    def test_distribution_uses_droste_release_name(self):
        base_directory = os.path.dirname(__file__)
        with open(
            os.path.join(base_directory, "build_release.ps1"),
            "r",
            encoding="utf-8",
        ) as file:
            build_script = file.read()

        self.assertIn('$releaseName = "Droste-$version-windows-x64"', build_script)
        self.assertIn("'START-HERE.txt'", build_script)
        self.assertIn("'UNINSTALL.txt'", build_script)
        self.assertIn("'THIRD-PARTY-NOTICES.txt'", build_script)
        self.assertIn("'SECURITY.md'", build_script)
        self.assertIn("'Droste.exe'", build_script)
        self.assertNotIn("'setup.bat'", build_script)
        self.assertNotIn("python-3.13", build_script)
        self.assertNotIn("wheelhouse", build_script)

        with open(
            os.path.join(base_directory, "build_executable.ps1"),
            "r",
            encoding="utf-8",
        ) as file:
            executable_builder = file.read()
        self.assertIn("--onefile", executable_builder)
        self.assertIn("--windowed", executable_builder)
        self.assertIn("--name Droste", executable_builder)
        self.assertIn("droste_tray.py", executable_builder)

        with open(
            os.path.join(base_directory, "droste_tray.py"),
            "r",
            encoding="utf-8",
        ) as file:
            tray_launcher = file.read()
        self.assertIn("subprocess.CREATE_NO_WINDOW", tray_launcher)
        self.assertIn("win32gui.Shell_NotifyIcon", tray_launcher)
        self.assertIn('"Drosteを終了"', tray_launcher)
        self.assertIn('"管理画面を開く"', tray_launcher)
        self.assertIn('[sys.executable, "--server"]', tray_launcher)
        self.assertIn('if "--server" in sys.argv', tray_launcher)

        guide_path = os.path.join(
            base_directory,
            "START-HERE.txt",
        )
        with open(guide_path, "r", encoding="utf-8") as file:
            guide = file.read()
        self.assertIn("Droste.exe", guide)
        self.assertNotIn("setup.batをダブルクリック", guide)
        self.assertIn("ホーム画面に追加", guide)
        self.assertIn("通知領域", guide)

    def test_security_exception_is_not_reachable_from_droste_code(self):
        base_directory = os.path.dirname(__file__)
        for filename in ("app.py", "tls_utils.py"):
            with open(
                os.path.join(base_directory, filename),
                "r",
                encoding="utf-8",
            ) as file:
                source = file.read()
            self.assertNotIn("pkcs7_decrypt_", source)


if __name__ == "__main__":
    unittest.main()
