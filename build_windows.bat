@echo off
setlocal

REM 在 Windows 11 的命令提示字元中执行本文件，即可建立独立的 exe。
py -3.12 -m venv .venv
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements.txt pyinstaller
if not exist vendor\tesseract\tesseract.exe (
  echo Missing OCR runtime: vendor\tesseract\tesseract.exe
  echo Place the Windows Tesseract runtime and tessdata\chi_sim.traineddata / eng.traineddata there, then run again.
  pause
  exit /b 1
)
pyinstaller --noconfirm --clean --windowed --name "高中数学文件分类工具" --collect-all fitz --collect-all docx --collect-all pptx --collect-all PIL main.py
xcopy /E /I /Y vendor\tesseract dist\高中数学文件分类工具\tesseract >nul

echo.
echo 已建立：dist\高中数学文件分类工具\高中数学文件分类工具.exe
pause
