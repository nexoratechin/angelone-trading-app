"""Route TLS verification through the operating system trust store.

Why this exists
---------------
On a corporate network that performs TLS inspection (Zscaler, Netskope,
Fortinet, ...), HTTPS connections are re-signed by the proxy's own root CA. That
CA is installed in the **operating system** trust store but *not* in
``certifi``'s bundle, which ``requests`` uses by default. Every HTTPS call then
fails with ``CERTIFICATE_VERIFY_FAILED`` - including the Angel One login, the
instrument-master download and historical candles. Nothing works, and the error
looks like a credentials or SDK problem rather than a network policy.

``truststore`` (Python 3.10+) makes ``ssl`` consult the OS store instead, so
Python trusts exactly the roots the browser on this machine already trusts.

Security note
-------------
This does **not** disable verification. ``verify=False`` would accept any
certificate and leave your API key and PIN open to interception - this library
keeps verification fully on against the OS store. It does mean that a
TLS-inspecting proxy on your network can see the traffic, but that is already
true of every other HTTPS connection this machine makes, and it is controlled by
your organisation rather than by this app.
"""

from __future__ import annotations

import os

from .logging import get_logger

log = get_logger("tls")

_enabled: bool | None = None


def enable_system_trust_store(force: bool = False) -> bool:
    """Point Python's SSL at the OS certificate store. Returns True if active.

    Safe to call repeatedly; the work happens once. Never raises - if
    ``truststore`` is unavailable we keep the default (certifi) behaviour and
    say so, rather than taking the whole app down.
    """
    global _enabled
    if _enabled is not None and not force:
        return _enabled

    if os.environ.get("BANKNIFTY_NO_SYSTEM_TRUST", "").strip().lower() in ("1", "true", "yes"):
        log.info("System trust store disabled by BANKNIFTY_NO_SYSTEM_TRUST")
        _enabled = False
        return False

    try:
        import truststore

        truststore.inject_into_ssl()
        _enabled = True
        log.info(
            "TLS verification uses the OS trust store (truststore). "
            "Corporate/custom root CAs installed on this machine are honoured."
        )
    except ImportError:
        _enabled = False
        log.debug("truststore not installed; using certifi's CA bundle")
    except Exception as exc:  # pragma: no cover - defensive
        _enabled = False
        log.warning("Could not enable the OS trust store: %s", exc)
    return _enabled


def tls_diagnosis(host: str = "apiconnect.angelone.in") -> dict:
    """Explain, for one host, whether TLS verifies and who signed the cert.

    Used by ``preflight``/``check`` so a TLS-inspection proxy is reported as
    such instead of surfacing as a confusing login failure.
    """
    import socket
    import ssl

    out: dict = {"host": host, "ok": False, "issuer": "", "error": "", "system_trust": _enabled}
    try:
        ctx = ssl.create_default_context()
        with socket.create_connection((host, 443), timeout=15) as sock:
            with ctx.wrap_socket(sock, server_hostname=host) as ss:
                cert = ss.getpeercert()
                issuer = dict(x[0] for x in cert.get("issuer", []))
                out["issuer"] = issuer.get("organizationName") or issuer.get("commonName") or ""
                out["ok"] = True
    except Exception as exc:
        out["error"] = f"{type(exc).__name__}: {exc}"
    return out


def looks_like_tls_inspection(issuer: str) -> bool:
    """Heuristic: is this certificate issued by a known inspection vendor?"""
    if not issuer:
        return False
    needles = (
        "zscaler", "netskope", "fortinet", "forcepoint", "bluecoat", "symantec",
        "palo alto", "cisco", "sophos", "checkpoint", "iboss", "cato", "cloudflare gateway",
    )
    low = issuer.lower()
    return any(n in low for n in needles)
