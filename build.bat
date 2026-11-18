@echo off
REM Builds the LocalDevX installer + portable version into .\release  (see build.py)
REM Needs: pip install pyinstaller   and   Inno Setup 6 (winget install JRSoftware.InnoSetup)
cd /d "%~dp0"
python build.py
pause
