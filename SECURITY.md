# Security notes

Droste 0.5 is designed only for a trusted private LAN. The Windows management server binds to `127.0.0.1`, while the smartphone server binds to one RFC 1918 IPv4 address and always uses HTTPS. Smartphone access requires both the Droste local CA and an approved device token.

Do not expose either Droste port through router port forwarding, a public reverse proxy, a VPN exit node, or a public Wi-Fi network. Treat `tls/droste-ca-key.pem`, `devices.json`, and `config.json` as private data.

## Dependency audit exception

The runtime dependencies are checked with `pip-audit`. As of 2026-08-16, `cryptography 49.0.0` reports `PYSEC-2026-3552`, whose fixed version is the unreleased `50.0.0`. The advisory affects applications that decrypt attacker-supplied PKCS#7 EnvelopedData with `pkcs7_decrypt_der`, `pkcs7_decrypt_pem`, or `pkcs7_decrypt_smime` and expose distinguishable results.

Droste does not import or call any PKCS#7 decryption API. It uses `cryptography` only to generate, load, sign, and inspect its private-LAN X.509 certificates. The exception is therefore not reachable in Droste's code path. Update to `cryptography 50.x` and remove this exception after a stable compatible release is available.

The two other findings affecting `cryptography 48.0.1` (`PYSEC-2026-3553` and `PYSEC-2026-3554`) are fixed by the 49.0.0 pin.
