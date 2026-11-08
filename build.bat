@echo off
REM Builds RoomIDOverlay-Setup.exe on a Windows PC.
REM Needs: Python 3.11 (python.org) and Inno Setup 6 (jrsoftware.org/isinfo.php)
cd /d "%~dp0"

python -m pip install -r requirements-build.txt || goto :error

python -m PyInstaller --noconfirm --clean --windowed --name RoomIDOverlay ^
  --add-data "overlay.html;." --add-data "control.html;." --add-data "example_room.png;." --add-data "example_roomid.png;." --add-data "version.txt;." ^
  --collect-all rapidocr_onnxruntime --collect-binaries onnxruntime --collect-all windows_capture ^
  --hidden-import pygrabber.dshow_graph ^
  app.py || goto :error

set ISCC="%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not exist %ISCC% set ISCC="%ProgramFiles%\Inno Setup 6\ISCC.exe"
%ISCC% installer.iss || goto :error

echo.
echo Done! Installer: Output\RoomIDOverlay-Setup.exe
pause
exit /b 0

:error
echo.
echo Build failed - see the messages above.
pause
exit /b 1
