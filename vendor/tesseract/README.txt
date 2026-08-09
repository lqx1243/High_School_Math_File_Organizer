请将 Windows 版 Tesseract OCR 运行文件放入此目录，再执行 build_windows.bat：

vendor/tesseract/tesseract.exe
vendor/tesseract/tessdata/chi_sim.traineddata
vendor/tesseract/tessdata/eng.traineddata

打包脚本会将整个 tesseract 目录复制到应用目录。最终老师只需双击 exe，
无需自行安装 OCR。
