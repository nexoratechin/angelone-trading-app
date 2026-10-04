"""Work around a smartapi-python bug that sends the wrong client IP.

The SDK builds its request headers from **class** attributes::

    "X-ClientLocalIP": self.clientLocalIp,
    "X-ClientPublicIP": self.clientPublicIp,

Those class attributes are assigned inside a ``finally`` block that overrides
whatever was detected::

    finally:
        clientPublicIp = "106.193.147.98"   # the SDK author's IP
        clientLocalIp  = "127.0.0.1"

The result is that *every* account sends the same hardcoded public IP. If your
Angel One API key is IP-whitelisted, the broker compares your request against
``106.193.147.98`` instead of your real address and rejects the login.

Because the values are read off the class, patching the class attribute before
any request is enough to fix every call - REST and WebSocket alike.
"""

from __future__ import annotations

import socket

from ..logging import get_logger

log = get_logger("angelone.client_ip")

#: Endpoints tried in order. Kept short - this runs once, before login.
#: Ordered by how often they survive corporate egress filtering: some proxies
#: block ipify specifically, so it is last rather than first.
_PUBLIC_IP_URLS = (
    "https://icanhazip.com",
    "https://ifconfig.me/ip",
    "https://ipinfo.io/ip",
    "https://checkip.amazonaws.com",
    "https://api.ipify.org",
)

#: The value the SDK ships with, so we can recognise "still not patched".
_SDK_HARDCODED_IP = "106.193.147.98"

_patched = False


def detect_local_ip() -> str:
    """Best-effort LAN address of this machine (no traffic sent)."""
    try:
        # A UDP socket connect() does not send packets, it just picks a route.
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.settimeout(3)
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except Exception:
        try:
            return socket.gethostbyname(socket.gethostname())
        except Exception:
            return "127.0.0.1"


def detect_public_ip(timeout: float = 6.0) -> str:
    """Best-effort public IP. Returns "" when every endpoint fails."""
    import requests

    for url in _PUBLIC_IP_URLS:
        try:
            resp = requests.get(url, timeout=timeout)
            if resp.ok:
                ip = (resp.text or "").strip()
                # guard against HTML error pages being returned
                if ip and len(ip) <= 45 and not ip.startswith("<"):
                    return ip
        except Exception as exc:
            log.debug("public IP lookup failed via %s: %s", url, exc)
    return ""


def resolve_client_ips(settings) -> tuple[str, str]:
    """Return ``(public_ip, local_ip)`` for the SDK, honouring overrides."""
    public = (settings.client_public_ip or "").strip() or detect_public_ip()
    local = (settings.client_local_ip or "").strip() or detect_local_ip()
    return public, local


def patch_sdk_client_ip(settings, force: bool = False) -> tuple[str, str]:
    """Point the SDK's class-level IP attributes at the real machine.

    Returns the ``(public_ip, local_ip)`` actually applied. Safe to call more
    than once; the lookup only happens on the first call unless ``force``.
    """
    global _patched

    from .auth import SmartConnect

    if SmartConnect is None:
        return "", ""

    if _patched and not force:
        return (
            getattr(SmartConnect, "clientPublicIp", "") or "",
            getattr(SmartConnect, "clientLocalIp", "") or "",
        )

    public, local = resolve_client_ips(settings)

    if not public:
        log.warning(
            "Could not detect the public IP. Angel One will keep seeing the SDK's "
            "hardcoded value (%s). Set CLIENT_PUBLIC_IP in .env if your API key is "
            "IP-whitelisted.",
            _SDK_HARDCODED_IP,
        )
        public = ""

    if public:
        SmartConnect.clientPublicIp = public
        log.info("Angel One requests will report X-ClientPublicIP=%s", public)
    SmartConnect.clientLocalIp = local or "127.0.0.1"

    _patched = True
    return public, SmartConnect.clientLocalIp


def current_sdk_ips() -> tuple[str, str]:
    """What the SDK would send *right now*, without patching."""
    from .auth import SmartConnect

    if SmartConnect is None:
        return "", ""
    return (
        getattr(SmartConnect, "clientPublicIp", "") or "",
        getattr(SmartConnect, "clientLocalIp", "") or "",
    )


def is_using_sdk_hardcoded_ip() -> bool:
    """True when the SDK is still sending its built-in placeholder address."""
    public, _ = current_sdk_ips()
    return public == _SDK_HARDCODED_IP
