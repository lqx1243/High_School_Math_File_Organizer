# 高中数学文件分类工具

面向 Windows 11 教师的本地桌面工具：读取教学资料的文件名和内容，使用 DeepSeek 提供分类建议，并以**复制**方式整理文件，始终保留原文件。

> 这是辅助整理工具。所有 AI 分类结果都应由老师在复制前人工复核。

## 下载与使用

请从 [Releases](https://github.com/lqx1243/High_School_Math_File_Organizer/releases) 下载最新的 Windows ZIP 包。

1. 解压整个 ZIP，保持其内部文件夹结构不变；
2. 双击 `HighSchoolMathFileOrganizer.exe`；
3. 添加一个或多个待整理资料文件夹，并选择分类结果保存位置；
4. 填写历史截止年份和 DeepSeek API Key；
5. 扫描、复核分类建议，再确认复制。

运行电脑不需要安装 Python、Office 或 Tesseract OCR。

## 功能

- 支持 PDF、DOCX、PPTX，以及安装 Microsoft Office 时的旧 `.doc`、`.ppt`；
- 自动忽略 macOS 复制资料时产生的 `._*` 元数据文件，以及 Office 的 `~$*` 临时文件；
- 可添加多个资料文件夹，并递归扫描它们的所有子文件夹；即使同时添加父文件夹和子文件夹，同一文件也只会扫描和复制一次；
- 本机 OCR 处理扫描 PDF，以及图片为主的 Word/PowerPoint；
- 先判断一级分类，再仅在对应一级分类下判断二级分类；
- 结合文件名和正文内容；
- 历史文件、综合文件、无法分类文件独立归档；
- 低置信度结果自动留给人工处理；
- 表格中可双击修改任意分类；
- 安全复制、重名自动编号，并生成 `分类清单.csv`。
- 扫描进度会保存在本机缓存中；意外关闭后再次打开可恢复已完成的分类，并继续处理其余文件。

## 分类标准

“分类标准”路径默认留空。留空时程序会使用与 `.exe` 同级的 `category_rules.txt`；点击“编辑分类标准”即可修改它。

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
- “删除扫描缓存”只会删除本机的扫描进度和分类建议；不会删除原文件、复制结果或 `分类清单.csv`。界面会显示缓存大小。

## 开发与许可证

开发者可在 Windows 11 上运行 `build_windows.bat` 重新打包。OCR 运行时已由 `vendor/tesseract` 提供。

本项目采用 [GPL-3.0](LICENSE) 许可证；第三方组件说明见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
