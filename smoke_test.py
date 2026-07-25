import argparse
import json
import os
import socket
import ssl
import urllib.request


BASE_DIRECTORY = os.path.dirname(__file__)
CA_CERTIFICATE = os.path.join(
    BASE_DIRECTORY,
    "tls",
    "droste-ca.crt",
)


def fetch(url, ssl_context=None):
    with urllib.request.urlopen(url, context=ssl_context, timeout=5) as response:
        return response.status, response.headers, response.read()


def discover_lan_ip():
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("8.8.8.8", 80))
        return probe.getsockname()[0]
    finally:
        probe.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--http-port", type=int)
    parser.add_argument("--https-port", type=int)
    parser.add_argument("--https-host")
    parser.add_argument("--ca-certificate", default=CA_CERTIFICATE)
    arguments = parser.parse_args()

    http_port = arguments.http_port or 5000
    status, _headers, body = fetch(
        f"http://127.0.0.1:{http_port}/api/config"
    )
    config = json.loads(body)
    if status != 200 or not config.get("tls_enabled"):
        raise RuntimeError("The local management endpoint is not ready.")

    https_port = arguments.https_port or config["https_port"]
    https_host = (
        arguments.https_host
        or config.get("lan_ip")
        or discover_lan_ip()
    )
    ssl_context = ssl.create_default_context(cafile=arguments.ca_certificate)
    status, _headers, body = fetch(
        f"https://{https_host}:{https_port}/setup",
        ssl_context,
    )
    if status != 200 or b"Droste HTTPS" not in body:
        raise RuntimeError("The HTTPS setup page is not ready.")

    status, headers, body = fetch(
        (
            f"https://{https_host}:{https_port}"
            "/tls/droste-ca.crt"
        ),
        ssl_context,
    )
    if status != 200 or not body:
        raise RuntimeError("The CA certificate download is not ready.")
    if "application/x-x509-ca-cert" not in headers.get("Content-Type", ""):
        raise RuntimeError("The CA certificate has an unexpected content type.")

    print("Smoke test passed: HTTP management and HTTPS setup endpoints are ready.")


if __name__ == "__main__":
    main()
