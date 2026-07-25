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


class TlsAssetTests(unittest.TestCase):
    def test_assets_are_reused_and_key_matches_certificate(self):
        with tempfile.TemporaryDirectory() as directory:
            first = ensure_local_tls_assets(directory, "192.168.1.10")

            with open(first["ca_key"], "rb") as source:
                mismatched_key = source.read()
            with open(first["server_key"], "wb") as destination:
                destination.write(mismatched_key)

            second = ensure_local_tls_assets(directory, "192.168.1.10")

            self.assertEqual(
                first["ca_fingerprint_sha256"],
                second["ca_fingerprint_sha256"],
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
    def test_iphone_home_screen_supports_one_tap_launch(self):
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
        self.assertNotIn("beforeinstallprompt", index_html)
        self.assertNotIn("Drosteをインストール", index_html)
        self.assertIn("@media (display-mode: standalone)", index_html)

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
    def test_setup_prefers_python_312_and_verifies_bundled_installer(self):
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

        self.assertLess(setup_script.index("py -3.12"), setup_script.index("where python"))
        self.assertLess(
            setup_script.index("-File verify_python_installer.ps1"),
            setup_script.index('start "" /wait "%PYTHON_INSTALLER%"'),
        )
        self.assertIn("InstallLauncherAllUsers=0", setup_script)
        self.assertIn("TargetDir=\"%PYTHON_INSTALL_DIR%\"", setup_script)
        self.assertIn(
            "67b5635e80ea51072b87941312d00ec8927c4db9ba18938f7ad2d27b328b95fb",
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
        self.assertIn("'regain.bat'", build_script)
        self.assertNotIn("'run_test.bat'", build_script)
        self.assertIn("'create_shortcut.ps1'", build_script)
        self.assertIn("'droste.ico'", build_script)

        with open(
            os.path.join(base_directory, "create_shortcut.ps1"),
            "r",
            encoding="utf-8",
        ) as file:
            shortcut_script = file.read()
        self.assertIn("'Droste.lnk'", shortcut_script)
        self.assertIn("'regain.bat'", shortcut_script)
        self.assertIn("'droste.ico'", shortcut_script)

        guide_path = os.path.join(
            base_directory,
            "START-HERE.txt",
        )
        with open(guide_path, "r", encoding="utf-8") as file:
            guide = file.read()
        self.assertIn("setup.bat", guide)
        self.assertIn("regain.bat", guide)
        self.assertIn("ホーム画面に追加", guide)


if __name__ == "__main__":
    unittest.main()
