"""Запуск и остановка веб-сервера.

Почему это на Python, а не в .cmd: cmd читает файл по смещению в байтах и,
когда посреди файла меняется кодировка, теряет своё место — вместо команд
начинает выполнять обрывки слов. Русский текст в .cmd с chcp 65001 разваливает
разбор. Поэтому .cmd остались голыми пусковыми файлами на латинице, а вся
работа и весь текст — здесь.

Кто занял порт, спрашиваем у самой Windows, а не храним номер процесса в
файле. Файл врёт: если сервер упал или окно закрыли крестиком, номер в нём
останется, а процесса уже нет.
"""

import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = ROOT / ".venv" / "Scripts" / "python.exe"
sys.path.insert(0, str(ROOT / "src"))

from vivatlas.net import lan_addresses  # noqa: E402

# Порты постоянные. 8710 — ваш, 8711 — Клода. Разные намеренно: Клод свой
# перезапускает по многу раз, и это не должно ронять страницу, которую вы в
# этот момент смотрите.
PORTS = {
    "user": (8710, "0.0.0.0", "ваш"),
    "claude": (8711, "127.0.0.1", "Клода"),
}


def listening_pid(port: int) -> int | None:
    """Кто слушает порт. None — никто."""
    try:
        out = subprocess.run(
            ["netstat", "-ano", "-p", "TCP"], capture_output=True, text=True, timeout=15
        ).stdout
    except Exception:
        return None
    for line in out.splitlines():
        # "  TCP    0.0.0.0:8710    0.0.0.0:0    LISTENING    12345"
        m = re.match(r"\s*TCP\s+\S+:(\d+)\s+\S+\s+LISTENING\s+(\d+)", line)
        if m and int(m.group(1)) == port:
            return int(m.group(2))
    return None


def start(who: str) -> int:
    port, host, whose = PORTS[who]
    pid = listening_pid(port)
    if pid:
        print(f"  Уже работает: порт {port}, процесс {pid}.")
        _where(port, host, whose)
        return 0

    if not PY.exists():
        print(f"  Нет окружения: {PY}")
        print("  Сначала: python -m venv .venv && .venv\\Scripts\\pip install -e .")
        return 1

    # Без окна: CREATE_NO_WINDOW не поднимает консоль над программой. Вывод
    # раньше жил в этом окне — теперь пишем в лог, иначе бы он пропал, а с ним
    # и причина, если сервер не встанет.
    log_path = ROOT / "logs" / f"serve-{port}.log"
    log_path.parent.mkdir(exist_ok=True)
    logf = open(log_path, "ab")  # noqa: SIM115 — держит дочерний процесс, не закрываем
    subprocess.Popen(
        [str(PY), "-m", "vivatlas.cli", "serve", "--host", host, "--port", str(port)],
        cwd=str(ROOT),
        stdout=logf,
        stderr=logf,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )

    # Ждём, пока порт откроется. Напечатать адрес раньше — обмануть: страница
    # ещё не отвечает, человек ткнёт и увидит ошибку.
    for _ in range(20):
        time.sleep(0.5)
        pid = listening_pid(port)
        if pid:
            print(f"  Сервер {whose} работает: порт {port}, процесс {pid}.")
            _where(port, host, whose)
            return 0

    print(f"  Сервер не поднялся за 10 секунд. Причина — в {log_path}.")
    return 1


def _where(port: int, host: str, whose: str) -> None:
    print(f"    на этом компьютере : http://127.0.0.1:{port}")
    if host == "0.0.0.0":
        for ip in lan_addresses():
            print(f"    с телефона         : http://{ip}:{port}")
        print("")
        print("  Телефон должен быть в той же сети. Не открывается — при первом")
        print("  запуске Windows спрашивает про брандмауэр, надо разрешить.")


def stop(who: str) -> int:
    port, _, whose = PORTS[who]
    pid = listening_pid(port)
    if not pid:
        print(f"  На порту {port} никто не слушает — останавливать нечего.")
        return 0
    r = subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, text=True)
    if r.returncode != 0:
        print(f"  Не смог остановить процесс {pid}: {r.stderr.strip() or r.stdout.strip()}")
        return 1
    print(f"  Сервер {whose} остановлен: порт {port}, процесс {pid}.")
    return 0


def status() -> int:
    print("")
    for port, _host, whose in PORTS.values():
        pid = listening_pid(port)
        state = f"работает, процесс {pid}" if pid else "не запущен"
        print(f"  {port} ({whose:6s}) — {state}")
    return 0


if __name__ == "__main__":
    action = sys.argv[1] if len(sys.argv) > 1 else "status"
    who = sys.argv[2] if len(sys.argv) > 2 else "user"
    if action == "start":
        sys.exit(start(who))
    if action == "stop":
        sys.exit(stop(who))
    sys.exit(status())
