"""Network-environment guards: TLS trust and the SDK's hardcoded client IP.

These cover the two environment failures that look like application bugs:

* a TLS-inspecting proxy whose root CA is in the OS store but not certifi,
  which makes every HTTPS call fail with CERTIFICATE_VERIFY_FAILED;
* smartapi-python sending a hardcoded ``X-ClientPublicIP``, which makes an
  IP-whitelisted Angel One key get rejected.
"""

from __future__ import annotations

import banknifty_trading_app.tls as tls
from banknifty_trading_app.angelone import client_ip
from banknifty_trading_app.config import Settings


# ------------------------------------------------------------------ TLS trust
def test_looks_like_tls_inspection_recognises_vendors():
    assert tls.looks_like_tls_inspection("Zscaler Inc.")
    assert tls.looks_like_tls_inspection("Netskope")
    assert tls.looks_like_tls_inspection("Fortinet")
    assert tls.looks_like_tls_inspection("Palo Alto Networks")


def test_looks_like_tls_inspection_rejects_real_cas():
    assert not tls.looks_like_tls_inspection("DigiCert Inc")
    assert not tls.looks_like_tls_inspection("Let's Encrypt")
    assert not tls.looks_like_tls_inspection("")


def test_enable_system_trust_store_is_idempotent_and_never_raises(monkeypatch):
    monkeypatch.setattr(tls, "_enabled", None)
    first = tls.enable_system_trust_store()
    second = tls.enable_system_trust_store()
    assert first == second


def test_system_trust_store_can_be_disabled_by_env(monkeypatch):
    monkeypatch.setattr(tls, "_enabled", None)
    monkeypatch.setenv("BANKNIFTY_NO_SYSTEM_TRUST", "1")
    assert tls.enable_system_trust_store(force=True) is False
    monkeypatch.setattr(tls, "_enabled", None)


def test_tls_diagnosis_shape():
    diag = tls.tls_diagnosis("apiconnect.angelone.in")
    assert {"host", "ok", "issuer", "error", "system_trust"} <= set(diag)
    # offline is fine - the shape must still be usable by preflight
    if not diag["ok"]:
        assert diag["error"]


# ------------------------------------------------------------ client IP patch
def test_local_ip_detection_returns_something():
    ip = client_ip.detect_local_ip()
    assert ip
    assert ip.count(".") == 3


def test_sdk_hardcoded_ip_is_recognised():
    assert client_ip._SDK_HARDCODED_IP == "106.193.147.98"


def test_resolve_client_ips_prefers_explicit_config(tmp_path):
    s = Settings(client_public_ip="203.0.113.9", client_local_ip="10.0.0.4")
    public, local = client_ip.resolve_client_ips(s)
    assert public == "203.0.113.9"
    assert local == "10.0.0.4"


def test_patch_overwrites_the_sdk_hardcoded_ip(monkeypatch):
    """The whole point: after patching, the SDK must not send its built-in IP."""

    class FakeSDK:
        clientPublicIp = client_ip._SDK_HARDCODED_IP
        clientLocalIp = "127.0.0.1"

    monkeypatch.setattr(client_ip, "_patched", False)
    monkeypatch.setattr(
        client_ip, "resolve_client_ips", lambda settings: ("203.0.113.7", "10.0.0.9")
    )
    import banknifty_trading_app.angelone.auth as auth

    monkeypatch.setattr(auth, "SmartConnect", FakeSDK)

    public, local = client_ip.patch_sdk_client_ip(Settings(), force=True)
    assert public == "203.0.113.7"
    assert local == "10.0.0.9"
    assert FakeSDK.clientPublicIp == "203.0.113.7"
    assert FakeSDK.clientLocalIp == "10.0.0.9"
    assert client_ip.is_using_sdk_hardcoded_ip() is False


def test_patch_leaves_hardcoded_value_when_detection_fails(monkeypatch):
    """If we cannot detect an IP, do not invent one - just warn and continue."""
    import banknifty_trading_app.angelone.auth as auth

    class FakeSDK:
        clientPublicIp = client_ip._SDK_HARDCODED_IP
        clientLocalIp = "127.0.0.1"

    monkeypatch.setattr(client_ip, "_patched", False)
    monkeypatch.setattr(client_ip, "resolve_client_ips", lambda settings: ("", "10.0.0.9"))
    monkeypatch.setattr(auth, "SmartConnect", FakeSDK)

    client_ip.patch_sdk_client_ip(Settings(), force=True)
    assert client_ip.is_using_sdk_hardcoded_ip() is True  # documented, not silent
    assert FakeSDK.clientLocalIp == "10.0.0.9"  # local still fixed


def test_patch_is_safe_without_sdk(monkeypatch):
    import banknifty_trading_app.angelone.auth as auth

    monkeypatch.setattr(client_ip, "_patched", False)
    monkeypatch.setattr(auth, "SmartConnect", None)
    assert client_ip.patch_sdk_client_ip(Settings(), force=True) == ("", "")
