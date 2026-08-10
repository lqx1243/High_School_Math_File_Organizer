@echo off
setlocal

set "APP_NAME=HighSchoolMathFileOrganizer"
set "APP_VERSION="
for /f "tokens=3" %%V in ('findstr /B /C:"APP_VERSION = " organizer\app.py') do set "APP_VERSION=%%~V"
if "%APP_VERSION%"=="" (
  echo Unable to read APP_VERSION from organizer\app.py.
  pause
  exit /b 1
)
set "DIST_DIR=%APP_NAME%-windows-x64-v%APP_VERSION%"
if exist "dist\%DIST_DIR%" (
  echo Release folder already exists: dist\%DIST_DIR%
  echo Change APP_VERSION before packaging, or move the existing release folder first.
  pause
  exit /b 1
)

REM Use the working "python" command, not the optional "py" launcher.
python --version
if errorlevel 1 (
  echo Python 3.12 was not found. Install Python and make sure "python" works in Command Prompt.
  pause
  exit /b 1
)

python -m venv .venv
if errorlevel 1 (
  echo Failed to create the virtual environment.
  pause
  exit /b 1
)

.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -r requirements.txt pyinstaller
if errorlevel 1 (
  echo Failed to install Python packages. Check the Internet connection and run this file again.
  pause
  exit /b 1
)

if not exist vendor\tesseract\tesseract.exe (
  echo Missing OCR runtime: vendor\tesseract\tesseract.exe
  pause
  exit /b 1
)

.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean --windowed --name "%APP_NAME%" --icon "assets\app_icon.ico" --add-data "assets;assets" --add-data "defaults;defaults" --add-data "ACKNOWLEDGEMENTS.md;." --add-data "THIRD_PARTY_NOTICES.md;." --collect-all win32com --hidden-import pythoncom --hidden-import pywintypes --collect-all pypdf --collect-all pypdfium2 --collect-all docx --collect-all pptx --collect-all PIL main.py
if errorlevel 1 (
  echo Packaging failed. Please send the error text above.
  pause
  exit /b 1
)
move "dist\%APP_NAME%" "dist\%DIST_DIR%" >nul
if errorlevel 1 (
  echo Failed to rename the release folder.
  pause
  exit /b 1
)
if exist "dist\%DIST_DIR%\category_rules.txt" del /Q "dist\%DIST_DIR%\category_rules.txt"
xcopy /E /I /Y vendor\tesseract "dist\%DIST_DIR%\tesseract" >nul

echo.
echo Build complete:
echo dist\%DIST_DIR%\%APP_NAME%.exe
pause
