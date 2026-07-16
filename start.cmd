@echo off
rem UTF-8 so the Russian output is readable. This file is ASCII-only on
rem purpose: cmd reads a batch file by byte offset and loses its place when
rem the codepage changes mid-file, executing fragments of words instead of
rem commands. All logic and all text live in scripts\server.py.
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
rem Your server. Constant port 8710, address never changes.
rem Listens on the home network so it opens on your phone.
rem Claude has a separate server on 8711 and restarts only that one.
python "%~dp0scripts\server.py" start user
