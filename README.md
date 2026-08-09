# 高中数学文件分类工具

面向 Windows 11 教师的本地桌面工具：读取教学资料的文件名和内容，使用 DeepSeek 提供分类建议，并以**复制**方式整理文件，始终保留原文件。

> 这是辅助整理工具。所有 AI 分类结果都应由老师在复制前人工复核。

## 下载与使用

请从 [Releases](https://github.com/lqx1243/High_School_Math_File_Organizer/releases) 下载最新的 Windows ZIP 包。

1. 解压整个 ZIP，保持其内部文件夹结构不变；
2. 双击 `HighSchoolMathFileOrganizer.exe`；
3. 选择待整理资料所在文件夹与分类结果保存位置；
4. 填写历史截止年份和 DeepSeek API Key；
5. 扫描、复核分类建议，再确认复制。

运行电脑不需要安装 Python、Office 或 Tesseract OCR。

## 功能

- 支持 PDF、DOCX、PPTX，以及安装 Microsoft Office 时的旧 `.doc`、`.ppt`；
- 本机 OCR 处理扫描 PDF，以及图片为主的 Word/PowerPoint；
- 先判断一级分类，再仅在对应一级分类下判断二级分类；
- 结合文件名和正文内容；
- 历史文件、综合文件、无法分类文件独立归档；
- 低置信度结果自动留给人工处理；
- 表格中可双击修改任意分类；
- 安全复制、重名自动编号，并生成 `分类清单.csv`。

## 分类标准

首次启动时，程序会把仓库中的 [分类标准.txt](分类标准.txt) 复制为一份可编辑的本机默认模板。点击“编辑分类标准”即可修改；如果待整理资料文件夹中有同名文件，程序会自动优先使用那一份。

格式如下：顶格文本为一级分类，缩进文本为该分类的二级分类；空行和以 `#` 开头的行会忽略。

```text
# 这一行的内容会被忽略
函数与导数
    函数及其性质
    导数及其应用
```

## 注意事项

- 文件名和文字摘录会发送给 DeepSeek API；请自行管理 API Key 与账户额度。
- OCR 对图片中的数学公式并不完全可靠，尤其需要人工复核。
- 旧 `.doc`、`.ppt` 通过本机已安装的 Microsoft Word/PowerPoint 读取；若电脑未安装对应 Office，仍可先按文件名低置信度分类，或手动另存为新格式。

## 开发与许可证

开发者可在 Windows 11 上运行 `build_windows.bat` 重新打包。OCR 运行时已由 `vendor/tesseract` 提供。

本项目采用 [GPL-3.0](LICENSE) 许可证；第三方组件说明见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
