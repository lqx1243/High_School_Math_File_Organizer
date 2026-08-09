@echo off
setlocal

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

.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean --windowed --name "HighSchoolMathFileOrganizer" --icon "assets\app_icon.ico" --add-data "assets;assets" --add-data "defaults;defaults" --add-data "THIRD_PARTY_NOTICES.md;." --collect-all win32com --hidden-import pythoncom --hidden-import pywintypes --collect-all pypdf --collect-all pypdfium2 --collect-all docx --collect-all pptx --collect-all PIL main.py
if errorlevel 1 (
  echo Packaging failed. Please send the error text above.
  pause
  exit /b 1
)
copy /Y defaults\category_rules.txt dist\HighSchoolMathFileOrganizer\category_rules.txt >nul
xcopy /E /I /Y vendor\tesseract dist\HighSchoolMathFileOrganizer\tesseract >nul

echo.
echo Build complete:
echo dist\HighSchoolMathFileOrganizer\HighSchoolMathFileOrganizer.exe
pause
