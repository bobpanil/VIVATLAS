"""Where the visitor's address comes from, with and without CLIENT_IP_HEADER."""

from starlette.requests import Request

from vivatlas import auth
from vivatlas.config import settings


def _request(headers: dict[str, str], peer: str = "172.18.0.3") -> Request:
    return Request(
        {
            "type": "http",
            "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
            "client": (peer, 40000),
        }
    )


def test_default_takes_the_first_forwarded_entry(monkeypatch):
    monkeypatch.setattr(settings, "client_ip_header", "")
    assert auth.client_ip(_request({"X-Forwarded-For": "203.0.113.7, 10.0.0.1"})) == "203.0.113.7"
    assert auth.client_ip(_request({})) == "172.18.0.3"


def test_cloudflare_header_wins_over_a_faked_forwarded_for(monkeypatch):
    monkeypatch.setattr(settings, "client_ip_header", "cf-connecting-ip")
    req = _request({"X-Forwarded-For": "1.2.3.4, 198.51.100.9", "CF-Connecting-IP": "198.51.100.9"})
    assert auth.client_ip(req) == "198.51.100.9"


def test_without_the_header_the_connection_counts_not_forwarded_for(monkeypatch):
    monkeypatch.setattr(settings, "client_ip_header", "cf-connecting-ip")
    assert auth.client_ip(_request({"X-Forwarded-For": "1.2.3.4"})) == "172.18.0.3"
