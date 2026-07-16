"""Адреса этой машины в домашней сети."""

import socket


def lan_addresses() -> list[str]:
    """По этим адресам страница откроется с телефона.

    Берём только домашние диапазоны: 127.0.0.1 телефону бесполезен, а всякие
    служебные адреса виртуальных машин только путают.
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
