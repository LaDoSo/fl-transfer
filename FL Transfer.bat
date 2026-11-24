@echo off
cd /d "%~dp0"
set PYTHONPATH=src
start "" pythonw -m fltransfer gui %*
