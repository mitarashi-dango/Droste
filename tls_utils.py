import datetime
import ipaddress
import os
import plistlib
import socket
import stat
import uuid

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID


CA_COMMON_NAME = "Droste Local CA"
SERVER_COMMON_NAME = "Droste LAN Server"


def _atomic_write(path, data, private=False):
    temporary_path = path + ".tmp"
    with open(temporary_path, "wb") as file:
        file.write(data)
    if private:
        os.chmod(temporary_path, stat.S_IREAD | stat.S_IWRITE)
        _restrict_private_file_windows(temporary_path)
    os.replace(temporary_path, path)


def _restrict_private_file_windows(path):
    if os.name != "nt":
        return

    import ntsecuritycon
    import win32api
    import win32con
    import win32security

    account_name = win32api.GetUserNameEx(win32con.NameSamCompatible)
    user_sid, _domain, _account_type = win32security.LookupAccountName(
        None,
        account_name,
    )
    access_mask = (
        ntsecuritycon.FILE_GENERIC_READ
        | ntsecuritycon.FILE_GENERIC_WRITE
        | ntsecuritycon.DELETE
        | ntsecuritycon.READ_CONTROL
    )
    dacl = win32security.ACL()
    dacl.AddAccessAllowedAceEx(
        win32security.ACL_REVISION_DS,
        0,
        access_mask,
        user_sid,
    )
    win32security.SetNamedSecurityInfo(
        path,
        win32security.SE_FILE_OBJECT,
        (
            win32security.DACL_SECURITY_INFORMATION
            | win32security.PROTECTED_DACL_SECURITY_INFORMATION
        ),
        None,
        None,
        dacl,
        None,
    )


def _new_ca(ca_key_path, ca_cert_path):
    now = datetime.datetime.now(datetime.timezone.utc)
    key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    subject = x509.Name([
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Droste"),
        x509.NameAttribute(NameOID.COMMON_NAME, CA_COMMON_NAME),
    ])
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=3650))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                key_encipherment=False,
                key_cert_sign=True,
                key_agreement=False,
                content_commitment=False,
                data_encipherment=False,
                encipher_only=False,
                decipher_only=False,
                crl_sign=True,
            ),
            critical=True,
        )
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(key.public_key()),
            critical=False,
        )
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(key.public_key()),
            critical=False,
        )
        .add_extension(
            x509.NameConstraints(
                permitted_subtrees=[
                    x509.DNSName("localhost"),
                    x509.DNSName(".local"),
                    x509.IPAddress(ipaddress.ip_network("10.0.0.0/8")),
                    x509.IPAddress(ipaddress.ip_network("127.0.0.0/8")),
                    x509.IPAddress(ipaddress.ip_network("172.16.0.0/12")),
                    x509.IPAddress(ipaddress.ip_network("192.168.0.0/16")),
                    x509.IPAddress(ipaddress.ip_network("::1/128")),
                    x509.IPAddress(ipaddress.ip_network("fc00::/7")),
                    x509.IPAddress(ipaddress.ip_network("fe80::/10")),
                ],
                excluded_subtrees=None,
            ),
            critical=True,
        )
        .sign(key, hashes.SHA256())
    )
    _atomic_write(
        ca_key_path,
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ),
        private=True,
    )
    _atomic_write(ca_cert_path, certificate.public_bytes(serialization.Encoding.PEM))
    return key, certificate


def _load_or_create_ca(ca_key_path, ca_cert_path):
    if os.path.exists(ca_key_path) and os.path.exists(ca_cert_path):
        try:
            with open(ca_key_path, "rb") as file:
                key = serialization.load_pem_private_key(file.read(), password=None)
            with open(ca_cert_path, "rb") as file:
                certificate = x509.load_pem_x509_certificate(file.read())
            basic_constraints = certificate.extensions.get_extension_for_class(
                x509.BasicConstraints
            ).value
            certificate.extensions.get_extension_for_class(x509.AuthorityKeyIdentifier)
            certificate.extensions.get_extension_for_class(x509.NameConstraints)
            public_key_matches = (
                key.public_key().public_numbers()
                == certificate.public_key().public_numbers()
            )
            if (
                basic_constraints.ca
                and public_key_matches
                and certificate.not_valid_after_utc
                > datetime.datetime.now(datetime.timezone.utc)
            ):
                return key, certificate
        except Exception:
            pass
    return _new_ca(ca_key_path, ca_cert_path)


def _server_names(lan_ip):
    hostname = socket.gethostname().strip()
    dns_names = ["localhost"]
    if hostname:
        dns_names.append(f"{hostname}.local")
    dns_names = list(dict.fromkeys(name for name in dns_names if name))

    ip_addresses = [ipaddress.ip_address("127.0.0.1"), ipaddress.ip_address("::1")]
    try:
        ip_addresses.append(ipaddress.ip_address(lan_ip))
    except ValueError:
        pass
    return dns_names, list(dict.fromkeys(ip_addresses))


def _server_certificate_is_current(path, lan_ip, ca_certificate):
    if not os.path.exists(path):
        return False
    try:
        with open(path, "rb") as file:
            certificate = x509.load_pem_x509_certificate(file.read())
        now = datetime.datetime.now(datetime.timezone.utc)
        if certificate.not_valid_after_utc <= now + datetime.timedelta(days=7):
            return False
        san = certificate.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        required_dns, required_ips = _server_names(lan_ip)
        actual_dns = set(san.get_values_for_type(x509.DNSName))
        actual_ips = set(san.get_values_for_type(x509.IPAddress))
        authority_key = certificate.extensions.get_extension_for_class(
            x509.AuthorityKeyIdentifier
        ).value.key_identifier
        ca_subject_key = ca_certificate.extensions.get_extension_for_class(
            x509.SubjectKeyIdentifier
        ).value.digest
        if authority_key != ca_subject_key or certificate.issuer != ca_certificate.subject:
            return False
        ca_certificate.public_key().verify(
            certificate.signature,
            certificate.tbs_certificate_bytes,
            padding.PKCS1v15(),
            certificate.signature_hash_algorithm,
        )
        return set(required_dns).issubset(actual_dns) and set(required_ips).issubset(actual_ips)
    except Exception:
        return False


def _private_key_matches_certificate(key_path, certificate_path):
    try:
        with open(key_path, "rb") as file:
            key = serialization.load_pem_private_key(file.read(), password=None)
        with open(certificate_path, "rb") as file:
            certificate = x509.load_pem_x509_certificate(file.read())
        return (
            key.public_key().public_numbers()
            == certificate.public_key().public_numbers()
        )
    except Exception:
        return False


def _new_server_certificate(ca_key, ca_certificate, lan_ip, key_path, cert_path):
    now = datetime.datetime.now(datetime.timezone.utc)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Droste"),
        x509.NameAttribute(NameOID.COMMON_NAME, SERVER_COMMON_NAME),
    ])
    dns_names, ip_addresses = _server_names(lan_ip)
    san_entries = [x509.DNSName(name) for name in dns_names]
    san_entries.extend(x509.IPAddress(address) for address in ip_addresses)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(ca_certificate.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=825))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.SubjectAlternativeName(san_entries), critical=False)
        .add_extension(
            x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]),
            critical=False,
        )
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(key.public_key()),
            critical=False,
        )
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()),
            critical=False,
        )
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                key_encipherment=True,
                key_cert_sign=False,
                key_agreement=False,
                content_commitment=False,
                data_encipherment=False,
                encipher_only=False,
                decipher_only=False,
                crl_sign=False,
            ),
            critical=True,
        )
        .sign(ca_key, hashes.SHA256())
    )
    _atomic_write(
        key_path,
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ),
        private=True,
    )
    _atomic_write(cert_path, certificate.public_bytes(serialization.Encoding.PEM))


def _write_mobileconfig(ca_certificate, path):
    der_certificate = ca_certificate.public_bytes(serialization.Encoding.DER)
    fingerprint = ca_certificate.fingerprint(hashes.SHA256()).hex()
    profile_uuid = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"droste-profile-{fingerprint}"))
    cert_uuid = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"droste-cert-{fingerprint}"))
    payload = {
        "PayloadContent": [{
            "PayloadCertificateFileName": "droste-ca.cer",
            "PayloadContent": der_certificate,
            "PayloadDescription": "DrosteのLAN内HTTPS証明書を信頼します。",
            "PayloadDisplayName": CA_COMMON_NAME,
            "PayloadIdentifier": "local.droste.ca",
            "PayloadType": "com.apple.security.root",
            "PayloadUUID": cert_uuid,
            "PayloadVersion": 1,
        }],
        "PayloadDescription": "同じWi-Fi内のDrosteへ安全に接続するための証明書です。",
        "PayloadDisplayName": "Droste HTTPS",
        "PayloadIdentifier": "local.droste.profile",
        "PayloadOrganization": "Droste",
        "PayloadRemovalDisallowed": False,
        "PayloadType": "Configuration",
        "PayloadUUID": profile_uuid,
        "PayloadVersion": 1,
    }
    _atomic_write(path, plistlib.dumps(payload, fmt=plistlib.FMT_XML, sort_keys=False))


def ensure_local_tls_assets(base_directory, lan_ip):
    tls_directory = os.path.join(base_directory, "tls")
    os.makedirs(tls_directory, exist_ok=True)
    ca_key_path = os.path.join(tls_directory, "droste-ca-key.pem")
    ca_cert_path = os.path.join(tls_directory, "droste-ca.crt")
    ca_der_path = os.path.join(tls_directory, "droste-ca-android.crt")
    server_key_path = os.path.join(tls_directory, "droste-server-key.pem")
    server_cert_path = os.path.join(tls_directory, "droste-server.crt")
    mobileconfig_path = os.path.join(tls_directory, "droste-ca.mobileconfig")

    ca_key, ca_certificate = _load_or_create_ca(ca_key_path, ca_cert_path)
    _atomic_write(ca_der_path, ca_certificate.public_bytes(serialization.Encoding.DER))
    if (
        not _server_certificate_is_current(
            server_cert_path,
            lan_ip,
            ca_certificate,
        )
        or not _private_key_matches_certificate(
            server_key_path,
            server_cert_path,
        )
    ):
        _new_server_certificate(
            ca_key,
            ca_certificate,
            lan_ip,
            server_key_path,
            server_cert_path,
        )
    _write_mobileconfig(ca_certificate, mobileconfig_path)

    fingerprint = ca_certificate.fingerprint(hashes.SHA256()).hex().upper()
    formatted_fingerprint = ":".join(
        fingerprint[index:index + 2] for index in range(0, len(fingerprint), 2)
    )
    return {
        "directory": tls_directory,
        "ca_cert": ca_cert_path,
        "ca_der": ca_der_path,
        "ca_key": ca_key_path,
        "server_cert": server_cert_path,
        "server_key": server_key_path,
        "mobileconfig": mobileconfig_path,
        "ca_fingerprint_sha256": formatted_fingerprint,
        "lan_ip": lan_ip,
    }
