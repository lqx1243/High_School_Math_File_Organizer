from __future__ import annotations

import io
import logging
import os
import sys
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image

LOGGER = logging.getLogger(__name__)
SUPPORTED_EXTENSIONS = {".pdf", ".doc", ".docx", ".ppt", ".pptx"}
MAX_OCR_PAGES = 30
MAX_OCR_IMAGES = 30


@dataclass
class ExtractionResult:
    text: str = ""
    warnings: list[str] = field(default_factory=list)
    ocr_used: bool = False


def configure_ocr_engine() -> None:
    """让 Windows 打包版优先使用随应用分发的 Tesseract。"""
    try:
        import pytesseract
    except ImportError:
        return
    roots = [Path(getattr(sys, "_MEIPASS", "")), Path(sys.executable).parent, Path(__file__).resolve().parents[1]]
    for root in roots:
        binary = root / "tesseract" / "tesseract.exe"
        if binary.is_file():
            pytesseract.pytesseract.tesseract_cmd = str(binary)
            os.environ.setdefault("TESSDATA_PREFIX", str(binary.parent / "tessdata"))
            return


def extract_document(path: str | Path) -> ExtractionResult:
    document = Path(path)
    suffix = document.suffix.lower()
    if suffix == ".pdf":
        return _extract_pdf(document)
    if suffix == ".docx":
        return _extract_docx(document)
    if suffix == ".doc":
        return _extract_legacy_doc(document)
    if suffix == ".pptx":
        return _extract_pptx(document)
    if suffix == ".ppt":
        return _extract_legacy_ppt(document)
    return ExtractionResult(warnings=[f"不支持的格式：{suffix}"])


def _extract_pdf(path: Path) -> ExtractionResult:
    try:
        import pypdf
        import pypdfium2 as pdfium
    except ImportError:
        return ExtractionResult(warnings=["PDF 组件尚未安装。"])
    try:
        reader = pypdf.PdfReader(path)
        chunks = [page.extract_text() or "" for page in reader.pages]
        text = "\n".join(chunks).strip()
        needs_ocr = len(text) < max(200, len(reader.pages) * 80)
        if not needs_ocr:
            return ExtractionResult(text=text)

        pdf = pdfium.PdfDocument(path)
        ocr_text: list[str] = []
        for index, page in enumerate(pdf):
            if index >= MAX_OCR_PAGES:
                break
            bitmap = page.render(scale=2)
            found = _ocr_image(bitmap.to_pil())
            if found:
                ocr_text.append(found)
        if ocr_text:
            return ExtractionResult(text=(text + "\n" + "\n".join(ocr_text)).strip(), ocr_used=True)
        return ExtractionResult(text=text, warnings=["此 PDF 文字很少，但本机 OCR 没有可用结果。"])
    except Exception as error:
        LOGGER.exception("Could not extract PDF: %s", path)
        return ExtractionResult(warnings=[f"无法读取 PDF：{error}"])


def _extract_docx(path: Path) -> ExtractionResult:
    try:
        from docx import Document
        document = Document(path)
        parts = [paragraph.text for paragraph in document.paragraphs]
        for table in document.tables:
            for row in table.rows:
                parts.extend(cell.text for cell in row.cells)
        return _append_embedded_image_ocr(path, "word/media/", "\n".join(parts).strip())
    except Exception as error:
        LOGGER.exception("Could not extract DOCX: %s", path)
        return ExtractionResult(warnings=[f"无法读取 Word 文件：{error}"])


def _extract_legacy_doc(path: Path) -> ExtractionResult:
    """通过已安装的 Microsoft Word 读取旧 .doc，不要求用户手动转换。"""
    word = document = None
    try:
        client = _office_client()
        word = client.DispatchEx("Word.Application")
        word.Visible = False
        word.DisplayAlerts = 0
        document = word.Documents.Open(str(path.resolve()), False, True, False)
        return ExtractionResult(text=(document.Content.Text or "").strip())
    except Exception as error:
        return ExtractionResult(warnings=[f"无法读取旧 Word .doc：{_office_help(error)}"])
    finally:
        if document is not None:
            try:
                document.Close(False)
            except Exception:
                pass
        if word is not None:
            try:
                word.Quit()
            except Exception:
                pass


def _extract_pptx(path: Path) -> ExtractionResult:
    try:
        from pptx import Presentation
        presentation = Presentation(path)
        parts: list[str] = []
        for slide in presentation.slides:
            for shape in slide.shapes:
                if getattr(shape, "has_text_frame", False):
                    parts.append(shape.text)
                if getattr(shape, "has_table", False):
                    for row in shape.table.rows:
                        parts.extend(cell.text for cell in row.cells)
        return _append_embedded_image_ocr(path, "ppt/media/", "\n".join(parts).strip())
    except Exception as error:
        LOGGER.exception("Could not extract PPTX: %s", path)
        return ExtractionResult(warnings=[f"无法读取 PowerPoint 文件：{error}"])


def _extract_legacy_ppt(path: Path) -> ExtractionResult:
    """通过已安装的 Microsoft PowerPoint 读取旧 .ppt 的文字内容。"""
    powerpoint = presentation = None
    try:
        client = _office_client()
        powerpoint = client.DispatchEx("PowerPoint.Application")
        presentation = powerpoint.Presentations.Open(str(path.resolve()), True, False, False)
        parts: list[str] = []
        for slide in presentation.Slides:
            for shape in slide.Shapes:
                if shape.HasTextFrame and shape.TextFrame.HasText:
                    parts.append(shape.TextFrame.TextRange.Text)
                if shape.HasTable:
                    for row in range(1, shape.Table.Rows.Count + 1):
                        for column in range(1, shape.Table.Columns.Count + 1):
                            parts.append(shape.Table.Cell(row, column).Shape.TextFrame.TextRange.Text)
        return ExtractionResult(text="\n".join(parts).strip())
    except Exception as error:
        return ExtractionResult(warnings=[f"无法读取旧 PowerPoint .ppt：{_office_help(error)}"])
    finally:
        if presentation is not None:
            try:
                presentation.Close()
            except Exception:
                pass
        if powerpoint is not None:
            try:
                powerpoint.Quit()
            except Exception:
                pass


def _office_client():
    if os.name != "nt":
        raise RuntimeError("旧 Office 格式只能在 Windows 上读取")
    try:
        import win32com.client
        return win32com.client
    except ImportError as error:
        raise RuntimeError("缺少 Windows Office 读取组件") from error


def _office_help(error: Exception) -> str:
    if os.name == "nt":
        return f"请确认本机已安装 Microsoft Word/PowerPoint，然后重试。（{error}）"
    return str(error)


def _append_embedded_image_ocr(path: Path, prefix: str, text: str) -> ExtractionResult:
    if len(text) >= 300:
        return ExtractionResult(text=text)
    ocr_text: list[str] = []
    try:
        with zipfile.ZipFile(path) as archive:
            images = [item for item in archive.namelist() if item.startswith(prefix)]
            for image_name in images[:MAX_OCR_IMAGES]:
                try:
                    image = Image.open(io.BytesIO(archive.read(image_name)))
                    found = _ocr_image(image)
                    if found:
                        ocr_text.append(found)
                except Exception:
                    LOGGER.debug("Skipping embedded image %s in %s", image_name, path)
    except Exception:
        LOGGER.debug("Cannot inspect embedded images in %s", path, exc_info=True)
    if ocr_text:
        return ExtractionResult(text=(text + "\n" + "\n".join(ocr_text)).strip(), ocr_used=True)
    warning = "文字内容很少，已尝试读取内嵌图片，但本机 OCR 没有可用结果。"
    return ExtractionResult(text=text, warnings=[warning] if len(text) < 100 else [])


def _ocr_image(image: Image.Image) -> str:
    try:
        import pytesseract
        return pytesseract.image_to_string(image.convert("RGB"), lang="chi_sim+eng").strip()
    except Exception:
        LOGGER.debug("OCR unavailable", exc_info=True)
        return ""
