@echo off
rem UTF-8 so the Russian output is readable. This file is ASCII-only on
rem purpose: cmd reads a batch file by byte offset and loses its place when
rem the codepage changes mid-file, executing fragments of words instead of
rem commands. All logic and all text live in scripts\server.py.
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
rem Claude's server. Port 8711, local only, not reachable from the phone.
rem Separate from your 8710 on purpose: restarting it must never drop
rem the page you are looking at.
python "%~dp0scripts\server.py" start claude
