@echo off
rem UTF-8 so the Russian output is readable. This file is ASCII-only on
rem purpose: cmd reads a batch file by byte offset and loses its place when
rem the codepage changes mid-file, executing fragments of words instead of
rem commands. All logic and all text live in scripts\server.py.
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
rem Stops your server (port 8710).
python "%~dp0scripts\server.py" stop user
