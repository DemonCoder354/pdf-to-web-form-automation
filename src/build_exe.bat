@echo off
rem Builds ..\program\PDF-to-Web-Form\PDF-to-Web-Form.exe (Windows, Python 3.10+ needed only for building).
cd /d "%~dp0"
python -m pip install -r requirements.txt pyinstaller || goto :error
python -m PyInstaller --noconfirm --clean --windowed --name PDF-to-Web-Form --distpath ..\program ^
  --collect-all playwright --hidden-import filler --hidden-import PIL._tkinter_finder app.pyw || goto :error
xcopy /E /I /Y demo_form ..\program\PDF-to-Web-Form\demo_form >nul
xcopy /E /I /Y sample_data ..\program\PDF-to-Web-Form\sample_data >nul
copy /Y template.json ..\program\PDF-to-Web-Form\ >nul
powershell -NoProfile -Command "Compress-Archive -Path '..\program\PDF-to-Web-Form' -DestinationPath '..\program\PDF-to-Web-Form.zip' -Force" || goto :error
echo.
echo Done: program\PDF-to-Web-Form\PDF-to-Web-Form.exe
echo Zip for GitHub Releases: program\PDF-to-Web-Form.zip
echo Copy the whole PDF-to-Web-Form folder to any Windows PC; Python is not needed there.
pause
exit /b 0
:error
echo Build failed.
pause
exit /b 1
