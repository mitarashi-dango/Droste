# Security notes

Droste 0.6.1 is designed only for a trusted private LAN. The Windows management server binds to `127.0.0.1`, while the smartphone server binds to one RFC 1918 IPv4 address and always uses HTTPS. Smartphone access requires both the Droste local CA and an approved device token.

Do not expose either Droste port through router port forwarding, a public reverse proxy, a VPN exit node, or a public Wi-Fi network. Treat `tls/droste-ca-key.pem`, `devices.json`, `chat.json`, and `config.json` as private data. Chat access is limited to the localhost host UI and registered device tokens; messages are capped at 140 characters, rate-limited, and retained as the latest 200 entries. Only the localhost host UI can rename the chat group.

## Dependency audit

The pinned runtime and build dependencies are checked with `pip-audit` before release. As of 2026-08-16, no known vulnerabilities are reported for the v0.6.1 dependency locks.
