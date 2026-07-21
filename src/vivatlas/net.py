"""This machine's addresses on the home network."""

import socket


def lan_addresses() -> list[str]:
    """The page opens from a phone at these addresses.

    We take only home ranges: 127.0.0.1 is useless to a phone, and all the
    various virtual-machine service addresses only cause confusion.
    """
    found: list[str] = []
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip.startswith(("192.168.", "10.")) and ip not in found:
                found.append(ip)
    except OSError:
        pass
    return found
