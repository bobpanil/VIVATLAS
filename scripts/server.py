"""Start and stop the web server.

Why this is in Python and not a .cmd: cmd reads the file by byte offset and,
when the encoding changes mid-file, loses its place — instead of commands it
starts running fragments of words. Russian text in a .cmd with chcp 65001
breaks parsing. So the .cmd files stayed as bare launchers in Latin script,
while all the work and all the text live here.

Who holds the port we ask Windows itself, rather than storing the process
number in a file. A file lies: if the server crashed or the window was closed
with the X, the number stays in it while the process is already gone.
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

# The ports are fixed. 8710 is yours, 8711 is Claude's. Deliberately different:
# Claude restarts its own many times, and that must not take down the page you
# are looking at right then.
PORTS = {
    "user": (8710, "0.0.0.0", "yours"),
    "claude": (8711, "127.0.0.1", "Claude's"),
}


def listening_pid(port: int) -> int | None:
    """Who is listening on the port. None — nobody."""
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
        print(f"  Already running: port {port}, process {pid}.")
        _where(port, host, whose)
        return 0

    if not PY.exists():
        print(f"  No environment: {PY}")
        print("  First: python -m venv .venv && .venv\\Scripts\\pip install -e .")
        return 1

    # No window: CREATE_NO_WINDOW does not raise a console over the program. The
    # output used to live in that window — now we write to a log, otherwise it
    # would vanish, and with it the reason if the server fails to come up.
    log_path = ROOT / "logs" / f"serve-{port}.log"
    log_path.parent.mkdir(exist_ok=True)
    logf = open(log_path, "ab")  # noqa: SIM115 — holds the child process, we don't close it
    subprocess.Popen(
        [str(PY), "-m", "vivatlas.cli", "serve", "--host", host, "--port", str(port)],
        cwd=str(ROOT),
        stdout=logf,
        stderr=logf,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )

    # Wait for the port to open. Printing the address sooner would be a lie: the
    # page isn't answering yet, the user clicks and sees an error.
    for _ in range(20):
        time.sleep(0.5)
        pid = listening_pid(port)
        if pid:
            print(f"  Server {whose} is running: port {port}, process {pid}.")
            _where(port, host, whose)
            return 0

    print(f"  Server did not come up within 10 seconds. Reason is in {log_path}.")
    return 1


def _where(port: int, host: str, whose: str) -> None:
    print(f"    on this computer : http://127.0.0.1:{port}")
    if host == "0.0.0.0":
        for ip in lan_addresses():
            print(f"    from a phone     : http://{ip}:{port}")
        print("")
        print("  The phone must be on the same network. If it won't open — on the")
        print("  first launch Windows asks about the firewall, you have to allow it.")


def stop(who: str) -> int:
    port, _, whose = PORTS[who]
    pid = listening_pid(port)
    if not pid:
        print(f"  Nobody is listening on port {port} — nothing to stop.")
        return 0
    r = subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, text=True)
    if r.returncode != 0:
        print(f"  Could not stop process {pid}: {r.stderr.strip() or r.stdout.strip()}")
        return 1
    print(f"  Server {whose} stopped: port {port}, process {pid}.")
    return 0


def status() -> int:
    print("")
    for port, _host, whose in PORTS.values():
        pid = listening_pid(port)
        state = f"running, process {pid}" if pid else "not started"
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
