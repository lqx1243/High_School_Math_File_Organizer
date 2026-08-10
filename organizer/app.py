from __future__ import annotations

import csv
import hashlib
import json
import os
import shutil
import sys
import threading
import webbrowser
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, ttk
import tkinter as tk

import keyring

from .category_rules import CategoryRules
from .classifier import Classification, DEFAULT_API_URL, DEFAULT_MODEL, PREFERRED_MODEL_IDS, classify_with_deepseek, list_available_models
from .extractors import SUPPORTED_EXTENSIONS, configure_ocr_engine, extract_document

APP_NAME = "高中数学文件分类工具"
APP_VERSION = "0.2.3"
PROJECT_URL = "https://github.com/lqx1243/High_School_Math_File_Organizer"
KEYRING_SERVICE = "HighSchoolMathFileOrganizer"
CACHE_FILE_NAME = "scan_cache.json"
CACHE_VERSION = 1
COPY_RESERVE_BYTES = 20 * 1024 * 1024
HASH_CHUNK_SIZE = 1024 * 1024


class ToolTip:
    """轻量悬停提示，不改变既有界面操作方式。"""

    def __init__(self, widget: tk.Widget, text: str) -> None:
        self.widget = widget
        self.text = text
        self.window: tk.Toplevel | None = None
        self.after_id: str | None = None
        widget.bind("<Enter>", self._schedule, add=True)
        widget.bind("<Leave>", self._hide, add=True)
        widget.bind("<ButtonPress>", self._hide, add=True)

    def _schedule(self, _event=None) -> None:
        self.after_id = self.widget.after(450, self._show)

    def _show(self) -> None:
        if self.window or not self.widget.winfo_viewable():
            return
        self.window = tk.Toplevel(self.widget)
        self.window.wm_overrideredirect(True)
        self.window.attributes("-topmost", True)
        x = self.widget.winfo_rootx() + 12
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 8
        self.window.wm_geometry(f"+{x}+{y}")
        tk.Label(self.window, text=self.text, justify="left", wraplength=300, bg="#1F2937", fg="white", padx=10, pady=7, font=("Microsoft YaHei UI", 9)).pack()

    def _hide(self, _event=None) -> None:
        if self.after_id:
            self.widget.after_cancel(self.after_id)
            self.after_id = None
        if self.window:
            self.window.destroy()
            self.window = None


@dataclass
class ReviewItem:
    source: Path
    result: Classification
    note: str = ""

    @property
    def label(self) -> str:
        if self.result.kind == "historical":
            return "历史文件"
        return self.result.display_name


class OrganizerApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(APP_NAME)
        self._configure_window_icon()
        self.geometry("1180x780")
        self.minsize(1040, 680)
        self._configure_styles()
        self.items: list[ReviewItem] = []
        self.busy = False
        self.cache_lock = threading.RLock()
        self._load_settings()
        self.scan_cache, self.cache_load_error = self._load_scan_cache()
        self.active_cache_key: str | None = None
        self.copy_cancel_requested = threading.Event()
        self._build_widgets()
        self.after_idle(self._restore_cached_scan)

    def _app_data_dir(self) -> Path:
        base = Path(os.environ.get("APPDATA", Path.home() / ".config")) / "HighSchoolMathFileOrganizer"
        base.mkdir(parents=True, exist_ok=True)
        return base

    def _settings_path(self) -> Path:
        return self._app_data_dir() / "settings.json"

    def _scan_cache_path(self) -> Path:
        return self._app_data_dir() / CACHE_FILE_NAME

    def _load_scan_cache(self) -> tuple[dict, str | None]:
        path = self._scan_cache_path()
        if not path.is_file():
            return {"version": CACHE_VERSION, "sessions": {}}, None
        try:
            cache = json.loads(path.read_text(encoding="utf-8"))
            if cache.get("version") != CACHE_VERSION or not isinstance(cache.get("sessions"), dict):
                raise ValueError("缓存格式不受支持")
            return cache, None
        except (OSError, json.JSONDecodeError, ValueError) as error:
            return {"version": CACHE_VERSION, "sessions": {}}, str(error)

    def _write_scan_cache(self) -> None:
        with self.cache_lock:
            path = self._scan_cache_path()
            temporary = path.with_suffix(".tmp")
            temporary.write_text(json.dumps(self.scan_cache, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(temporary, path)
            self.cache_load_error = None

    @staticmethod
    def _file_fingerprint(file: Path) -> dict[str, int]:
        stat = file.stat()
        return {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns}

    def _cache_config(self, sources: list[Path], output: Path, rules_file: Path, cutoff_year: int, threshold: float) -> dict:
        return {
            "sources": sorted(str(source.resolve()) for source in self._non_overlapping_sources(sources)),
            "output": str(output.resolve()),
            "rules_file": str(rules_file.resolve()),
            "rules_hash": hashlib.sha256(rules_file.read_bytes()).hexdigest(),
            "cutoff_year": cutoff_year,
            "threshold": threshold,
            "api_url": self.api_url_var.get().strip() or DEFAULT_API_URL,
            "model": self.model_var.get().strip() or DEFAULT_MODEL,
        }

    @staticmethod
    def _cache_key(config: dict) -> str:
        payload = json.dumps(config, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _cache_session(self, key: str, config: dict, create: bool = False) -> dict | None:
        sessions = self.scan_cache.setdefault("sessions", {})
        session = sessions.get(key)
        if session is not None and not isinstance(session, dict):
            session = None
        if session is None and create:
            session = {"config": config, "completed": {}, "failed": {}, "updated_at": datetime.now().isoformat(timespec="seconds")}
            sessions[key] = session
        return session

    @staticmethod
    def _cached_item(file: Path, record: dict) -> ReviewItem | None:
        try:
            if record.get("fingerprint") != OrganizerApp._file_fingerprint(file):
                return None
            result = record["result"]
            return ReviewItem(
                file,
                Classification(str(result["kind"]), result.get("primary"), result.get("secondary"), float(result["confidence"]), str(result["reason"])),
                str(record.get("note", "")),
            )
        except (KeyError, OSError, TypeError, ValueError):
            return None

    def _cached_items_for_files(self, session: dict, files: list[Path]) -> dict[Path, ReviewItem]:
        completed = session.get("completed", {})
        if not isinstance(completed, dict):
            return {}
        restored: dict[Path, ReviewItem] = {}
        for file in files:
            item = self._cached_item(file, completed.get(str(file.resolve()), {}))
            if item:
                restored[file.resolve()] = item
        return restored

    def _cache_store_completed(self, cache_key: str | None, item: ReviewItem) -> None:
        if not cache_key:
            return
        with self.cache_lock:
            session = self._cache_session(cache_key, {}, create=False)
            if session is None:
                return
            completed = session.setdefault("completed", {})
            failed = session.setdefault("failed", {})
            file_key = str(item.source.resolve())
            completed[file_key] = {
                "status": "completed",
                "fingerprint": self._file_fingerprint(item.source),
                "result": {
                    "kind": item.result.kind,
                    "primary": item.result.primary,
                    "secondary": item.result.secondary,
                    "confidence": item.result.confidence,
                    "reason": item.result.reason,
                },
                "note": item.note,
            }
            failed.pop(file_key, None)
            session["updated_at"] = datetime.now().isoformat(timespec="seconds")
            self._write_scan_cache()

    def _cache_store_failure(self, cache_key: str | None, file: Path, error: Exception) -> None:
        if not cache_key:
            return
        with self.cache_lock:
            session = self._cache_session(cache_key, {}, create=False)
            if session is None:
                return
            failed = session.setdefault("failed", {})
            failed[str(file.resolve())] = {
                "status": "failed",
                "fingerprint": self._file_fingerprint(file),
                "result": {"kind": "unclassifiable", "confidence": 0.0, "reason": f"处理失败：{error}"},
                "error": str(error),
                "updated_at": datetime.now().isoformat(timespec="seconds"),
            }
            session["updated_at"] = datetime.now().isoformat(timespec="seconds")
            self._write_scan_cache()

    def _copy_cache_section(self, cache_key: str | None, output: Path, *, create: bool) -> dict | None:
        if not cache_key:
            return None
        session = self._cache_session(cache_key, {}, create=create)
        if session is None:
            return None
        resolved_output = str(output.resolve())
        section = session.get("copy")
        if not isinstance(section, dict) or section.get("output") != resolved_output:
            if not create:
                return None
            section = {"output": resolved_output, "records": {}, "updated_at": datetime.now().isoformat(timespec="seconds")}
            session["copy"] = section
        if not isinstance(section.get("records"), dict):
            section["records"] = {}
        return section

    def _copy_records_snapshot(self, cache_key: str | None, output: Path) -> dict[str, dict]:
        with self.cache_lock:
            section = self._copy_cache_section(cache_key, output, create=False)
            if section is None:
                return {}
            return {key: dict(record) for key, record in section["records"].items() if isinstance(record, dict)}

    def _cache_store_copy_result(
        self,
        cache_key: str | None,
        output: Path,
        item: ReviewItem,
        relative_destination: Path,
        *,
        status: str,
        sha256: str | None = None,
        destination: Path | None = None,
        error: str = "",
    ) -> None:
        with self.cache_lock:
            section = self._copy_cache_section(cache_key, output, create=True)
            if section is None:
                return
            records = section["records"]
            records[str(item.source.resolve())] = {
                "status": status,
                "fingerprint": self._file_fingerprint(item.source),
                "classification": str(relative_destination),
                "sha256": sha256,
                "destination": str(destination.resolve()) if destination else "",
                "error": error,
                "updated_at": datetime.now().isoformat(timespec="seconds"),
            }
            section["updated_at"] = datetime.now().isoformat(timespec="seconds")
            self._write_scan_cache()

    @staticmethod
    def _sha256_file(file: Path) -> str:
        digest = hashlib.sha256()
        with file.open("rb") as stream:
            while chunk := stream.read(HASH_CHUNK_SIZE):
                digest.update(chunk)
        return digest.hexdigest()

    def _copy_record_is_complete(self, record: dict, item: ReviewItem, relative_destination: Path) -> bool:
        try:
            if record.get("status") not in {"copied", "duplicate"}:
                return False
            if record.get("fingerprint") != self._file_fingerprint(item.source):
                return False
            if record.get("classification") != str(relative_destination):
                return False
            expected_hash = record.get("sha256")
            destination = Path(str(record.get("destination", "")))
            return bool(expected_hash and destination.is_file() and self._sha256_file(destination) == expected_hash)
        except (OSError, ValueError):
            return False

    @staticmethod
    def _format_size(size: int) -> str:
        if size < 1024:
            return f"{size} B"
        if size < 1024 * 1024:
            return f"{size / 1024:.1f} KB"
        return f"{size / (1024 * 1024):.1f} MB"

    def _update_cache_info(self) -> None:
        path = self._scan_cache_path()
        if not path.is_file():
            self.cache_info_var.set("缓存：无")
            return
        if self.cache_load_error:
            self.cache_info_var.set("缓存：读取失败，可删除")
            return
        try:
            completed = sum(len(session.get("completed", {})) for session in self.scan_cache.get("sessions", {}).values() if isinstance(session, dict))
            copied = 0
            for session in self.scan_cache.get("sessions", {}).values():
                if not isinstance(session, dict):
                    continue
                copy_section = session.get("copy")
                if not isinstance(copy_section, dict) or not isinstance(copy_section.get("records"), dict):
                    continue
                copied += sum(1 for record in copy_section["records"].values() if isinstance(record, dict) and record.get("status") == "copied")
            copy_note = f"，已复制 {copied} 项" if copied else ""
            self.cache_info_var.set(f"缓存：{self._format_size(path.stat().st_size)}（已分类 {completed} 项{copy_note}）")
        except OSError:
            self.cache_info_var.set("缓存：无法读取")

    def _restore_cached_scan(self) -> None:
        if not self._scan_cache_path().is_file():
            return
        try:
            sources, output, rules_file, _rules, cutoff_year, threshold = self._read_inputs()
            files = self._documents_under(sources, output)
            config = self._cache_config(sources, output, rules_file, cutoff_year, threshold)
            key = self._cache_key(config)
            session = self._cache_session(key, config)
        except (OSError, ValueError):
            return
        if session is None:
            return
        restored = self._cached_items_for_files(session, files)
        self.active_cache_key = key
        self.items = [restored[file.resolve()] for file in files if file.resolve() in restored]
        self._refresh_table()
        pending = len(files) - len(self.items)
        failed = len(session.get("failed", {}))
        if pending:
            retry_note = f"，其中 {failed} 项将在继续时重试" if failed else ""
            self.status_var.set(f"已从缓存恢复 {len(self.items)} 个分类结果；还有 {pending} 个待处理{retry_note}。点击扫描即可继续。")
        elif self.items:
            self.copy_button.configure(state="normal")
            self.status_var.set(f"已从缓存恢复全部 {len(self.items)} 个分类结果。请复核后复制，或删除缓存重新扫描。")

    def clear_scan_cache(self) -> None:
        if self.busy:
            return
        path = self._scan_cache_path()
        if not path.is_file():
            messagebox.showinfo(APP_NAME, "当前没有可删除的扫描缓存。")
            return
        if not messagebox.askyesno(APP_NAME, "删除所有未完成和已完成的扫描缓存？\n\n这不会删除原文件、分类结果或分类清单。"):
            return
        try:
            path.unlink()
            self.scan_cache = {"version": CACHE_VERSION, "sessions": {}}
            self.cache_load_error = None
            self.active_cache_key = None
            self._update_cache_info()
            self.status_var.set("扫描缓存已删除。下次扫描将重新处理文件。")
        except OSError as error:
            messagebox.showerror(APP_NAME, f"删除缓存失败：{error}")

    def _default_rules_file(self) -> Path:
        roots = [Path(sys.executable).parent, Path(getattr(sys, "_MEIPASS", "")), Path(__file__).resolve().parents[1]]
        for root in roots:
            for candidate in (root / "defaults" / "category_rules.txt", root / "defaults" / "分类标准.txt"):
                if candidate.is_file():
                    return candidate
        return Path(sys.executable).parent / "defaults" / "category_rules.txt"

    def _active_rules_file(self) -> Path:
        custom_path = self.rules_var.get().strip()
        return Path(custom_path).expanduser() if custom_path else self._default_rules_file()

    def _editable_default_rules(self) -> Path:
        """把随软件提供的模板复制到用户目录，再作为自定义规则编辑。"""
        target = self._app_data_dir() / "分类标准.txt"
        if not target.is_file():
            default_rules = self._default_rules_file()
            if not default_rules.is_file():
                raise ValueError("未找到软件自带的默认分类标准。")
            shutil.copy2(default_rules, target)
        self.rules_var.set(str(target))
        self._validate_rules(show_errors=False)
        return target

    def _third_party_notices_text(self) -> str:
        roots = [Path(sys.executable).parent, Path(getattr(sys, "_MEIPASS", "")), Path(__file__).resolve().parents[1]]
        for root in roots:
            notices = root / "THIRD_PARTY_NOTICES.md"
            if notices.is_file():
                try:
                    return notices.read_text(encoding="utf-8")
                except OSError:
                    continue
        return "未找到第三方鸣谢文件。请查看项目主页中的 THIRD_PARTY_NOTICES.md。"

    def _project_acknowledgements_text(self) -> str:
        roots = [Path(sys.executable).parent, Path(getattr(sys, "_MEIPASS", "")), Path(__file__).resolve().parents[1]]
        for root in roots:
            acknowledgements = root / "ACKNOWLEDGEMENTS.md"
            if acknowledgements.is_file():
                try:
                    return acknowledgements.read_text(encoding="utf-8")
                except OSError:
                    continue
        return "感谢每一位为本项目提供帮助、建议与支持的人。"

    @staticmethod
    def _readable_markdown(text: str) -> str:
        """在 Tk 文本框中呈现简洁可读的 Markdown，而不是暴露标记符号。"""
        lines: list[str] = []
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if line.startswith("#"):
                line = line.lstrip("#").strip()
            if line.startswith("- "):
                line = "• " + line[2:]
            line = line.replace("**", "").replace("__", "").replace("`", "")
            lines.append(line)
        return "\n".join(lines).strip()

    @staticmethod
    def _open_project_page(_event=None) -> None:
        webbrowser.open(PROJECT_URL, new=2)

    def _show_third_party_notices(self) -> None:
        dialog = tk.Toplevel(self)
        dialog.title("鸣谢与许可证")
        dialog.transient(self)
        dialog.geometry("720x500")
        dialog.minsize(540, 360)
        dialog.configure(bg="#F4F7FB")
        ttk.Label(dialog, text="鸣谢与许可证", style="SectionTitle.TLabel").pack(anchor="w", padx=18, pady=(16, 4))
        ttk.Label(dialog, text="感谢项目开发过程中的每一份帮助；第三方许可证随软件一同发布。", style="Muted.TLabel").pack(anchor="w", padx=18, pady=(0, 10))
        notice = scrolledtext.ScrolledText(dialog, wrap="word", font=("Microsoft YaHei UI", 10), relief="flat", padx=12, pady=10)
        notice.pack(fill="both", expand=True, padx=18, pady=(0, 12))
        content = "项目鸣谢\n\n" + self._readable_markdown(self._project_acknowledgements_text())
        content += "\n\n\n第三方鸣谢与许可证\n\n" + self._readable_markdown(self._third_party_notices_text())
        notice.insert("1.0", content)
        notice.configure(state="disabled")
        ttk.Button(dialog, text="关闭", command=dialog.destroy, style="Soft.TButton").pack(anchor="e", padx=18, pady=(0, 16))

    def _configure_window_icon(self) -> None:
        roots = [Path(sys.executable).parent, Path(getattr(sys, "_MEIPASS", "")), Path(__file__).resolve().parents[1]]
        for root in roots:
            icon = root / "assets" / "app_icon.ico"
            if icon.is_file():
                try:
                    self.iconbitmap(default=str(icon))
                    return
                except tk.TclError:
                    continue

    def _load_settings(self) -> None:
        defaults = {"source": "", "sources": [], "rules": "", "output": "", "year": str(datetime.now().year - 5), "threshold": "70", "api_url": DEFAULT_API_URL, "model": DEFAULT_MODEL}
        try:
            loaded = json.loads(self._settings_path().read_text(encoding="utf-8"))
            defaults.update({key: value for key, value in loaded.items() if key in defaults and key != "rules"})
        except (OSError, json.JSONDecodeError):
            pass
        saved_sources = defaults["sources"] if isinstance(defaults["sources"], list) else []
        if not saved_sources and defaults["source"]:
            # Keep the single-folder setting written by older releases usable.
            saved_sources = [defaults["source"]]
        self.source_paths = [Path(str(source)).expanduser() for source in saved_sources if str(source).strip()]
        self.rules_var = tk.StringVar(value=defaults["rules"])
        self.output_var = tk.StringVar(value=defaults["output"])
        self.year_var = tk.StringVar(value=defaults["year"])
        try:
            saved_threshold = float(str(defaults["threshold"]))
            threshold_percent = saved_threshold * 100 if 0 <= saved_threshold <= 1 else saved_threshold
            threshold_text = f"{threshold_percent:g}"
        except ValueError:
            threshold_text = "70"
        self.threshold_var = tk.StringVar(value=threshold_text)
        self.api_url_var = tk.StringVar(value=defaults["api_url"])
        self.model_var = tk.StringVar(value=defaults["model"])
        self.available_models: list[str] = []
        self.api_verified_key = ""
        self.api_verified_url = ""
        self.api_testing = False
        try:
            saved_key = keyring.get_password(KEYRING_SERVICE, "deepseek_api_key") or ""
        except keyring.errors.KeyringError:
            saved_key = ""
        self.api_key_var = tk.StringVar(value=saved_key)

    def _configure_styles(self) -> None:
        style = ttk.Style(self)
        try:
            # Windows 使用系统主题，获得稳定的高 DPI 渲染、清晰的悬停状态和键盘焦点提示。
            if os.name == "nt" and "vista" in style.theme_names():
                style.theme_use("vista")
            else:
                style.theme_use("clam")
        except tk.TclError:
            pass
        background = "#F4F7FB"
        self.configure(bg=background)
        style.configure("TFrame", background=background)
        style.configure("Header.TFrame", background="#EAF2FF")
        style.configure("TLabel", background=background, foreground="#1F2937", font=("Microsoft YaHei UI", 9))
        style.configure("Muted.TLabel", background=background, foreground="#64748B", font=("Microsoft YaHei UI", 9))
        style.configure("Status.TLabel", background=background, foreground="#334155", font=("Microsoft YaHei UI", 9))
        style.configure("Link.TLabel", background=background, foreground="#2563EB", font=("Microsoft YaHei UI", 9, "underline"))
        style.configure("SectionTitle.TLabel", background=background, foreground="#123B6D", font=("Microsoft YaHei UI", 14, "bold"))
        style.configure("Title.TLabel", background="#EAF2FF", foreground="#123B6D", font=("Microsoft YaHei UI", 16, "bold"))
        style.configure("SubTitle.TLabel", background="#EAF2FF", foreground="#4B5563", font=("Microsoft YaHei UI", 9))
        style.configure("TLabelframe", background=background, bordercolor="#D7E0ED")
        style.configure("TLabelframe.Label", background=background, foreground="#123B6D", font=("Microsoft YaHei UI", 9, "bold"))
        style.configure("TEntry", padding=5)
        style.configure("TCombobox", padding=5)
        style.configure("TButton", padding=(10, 6), font=("Microsoft YaHei UI", 9))
        style.configure("Soft.TButton", padding=(10, 6))
        style.map("Soft.TButton", background=[("active", "#D7E7FF")], foreground=[("active", "#123B6D")])
        style.configure("Navigation.TLabel", background=background, foreground="#64748B", font=("Microsoft YaHei UI", 10))
        style.configure("NavigationActive.TLabel", background=background, foreground="#123B6D", font=("Microsoft YaHei UI", 10, "bold"))
        style.configure("NavigationHover.TLabel", background=background, foreground="#2563EB", font=("Microsoft YaHei UI", 10, "bold"))
        style.configure("Treeview", rowheight=30, font=("Microsoft YaHei UI", 9), background="white", fieldbackground="white")
        style.configure("Treeview.Heading", font=("Microsoft YaHei UI", 9, "bold"), background="#E8EEF8", foreground="#183B67", padding=7)

    def _save_settings(self) -> None:
        settings = {
            "sources": [str(source) for source in self.source_paths],
            "output": self.output_var.get(),
            "year": self.year_var.get(),
            "threshold": self.threshold_var.get(),
            "api_url": self.api_url_var.get(),
            "model": self.model_var.get(),
        }
        self._settings_path().write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")
        if self.api_key_var.get().strip():
            try:
                keyring.set_password(KEYRING_SERVICE, "deepseek_api_key", self.api_key_var.get().strip())
            except keyring.errors.KeyringError:
                # 即使凭据管理器不可用，也不阻止本次使用；密钥不会写入普通设置文件。
                pass

    def _invalidate_api_verification(self) -> None:
        if self.api_testing:
            return
        current_key = self.api_key_var.get().strip()
        current_url = self.api_url_var.get().strip() or DEFAULT_API_URL
        if current_key == self.api_verified_key and current_url == self.api_verified_url:
            return
        self.api_verified_key = ""
        self.api_verified_url = ""
        if hasattr(self, "model_box"):
            self.model_box.configure(state="disabled")
        if current_key and hasattr(self, "api_status_var"):
            self.api_status_var.set("API Key 已变更，请测试连接并重新获取模型。")

    def _test_api_connection(self) -> None:
        api_key = self.api_key_var.get().strip()
        api_url = self.api_url_var.get().strip() or DEFAULT_API_URL
        if not api_key:
            messagebox.showinfo(APP_NAME, "请先输入 DeepSeek API Key。")
            return
        if self.api_testing:
            return
        self.api_testing = True
        self.test_api_button.configure(state="disabled")
        self.model_box.configure(state="disabled")
        self.api_status_var.set("正在测试连接并获取模型列表…")
        threading.Thread(target=self._api_connection_worker, args=(api_key, api_url), daemon=True).start()

    def _api_connection_worker(self, api_key: str, api_url: str) -> None:
        try:
            models = list_available_models(api_key, api_url)
        except Exception as error:
            self.after(0, self._api_connection_finished, api_key, api_url, [], str(error))
            return
        self.after(0, self._api_connection_finished, api_key, api_url, models, "")

    def _api_connection_finished(self, api_key: str, api_url: str, models: list[str], error: str) -> None:
        self.api_testing = False
        self.test_api_button.configure(state="normal")
        if api_key != self.api_key_var.get().strip() or api_url != (self.api_url_var.get().strip() or DEFAULT_API_URL):
            self.api_status_var.set("API Key 或连接地址已变更，请重新测试。")
            return
        if error:
            self.available_models = []
            self.api_verified_key = ""
            self.api_verified_url = ""
            self.model_box.configure(state="disabled")
            self.api_status_var.set(f"连接失败：{error}")
            messagebox.showerror(APP_NAME, f"无法连接 DeepSeek API：\n{error}")
            return

        self.available_models = models
        self.api_verified_key = api_key
        self.api_verified_url = api_url
        self.model_box.configure(values=models, state="readonly")
        preferred_model = next((model for model in PREFERRED_MODEL_IDS if model in models), None)
        if preferred_model:
            self.model_var.set(preferred_model)
            self.api_status_var.set(f"连接成功，已获取 {len(models)} 个模型；已默认选择 {preferred_model}。")
        else:
            self.model_var.set("")
            self.api_status_var.set(f"连接成功，已获取 {len(models)} 个模型；请从列表选择一个模型。")
        self._save_settings()

    def _build_widgets(self) -> None:
        outer = ttk.Frame(self, padding=(18, 14))
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(2, weight=1)

        header = ttk.Frame(outer, style="Header.TFrame", padding=(16, 12))
        header.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        ttk.Label(header, text=APP_NAME, style="Title.TLabel").pack(anchor="w")
        ttk.Label(header, text="安全复制原文件 · AI 提供建议 · 老师最后复核", style="SubTitle.TLabel").pack(anchor="w", pady=(3, 0))

        navigation = ttk.Frame(outer)
        navigation.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        self.navigation_items: dict[str, tuple[ttk.Label, tk.Frame]] = {}
        for column, (name, text) in enumerate((
            ("setup", "1. 准备资料"),
            ("review", "2. 复核与复制"),
            ("maintenance", "规则与维护"),
        )):
            item = ttk.Frame(navigation)
            item.grid(row=0, column=column, sticky="w", padx=(0, 28))
            label = ttk.Label(item, text=text, style="Navigation.TLabel", cursor="hand2")
            label.grid(row=0, column=0, sticky="w", pady=(0, 5))
            indicator = tk.Frame(item, height=2, bg="#F4F7FB")
            indicator.grid(row=1, column=0, sticky="ew")
            label.bind("<Button-1>", lambda _event, key=name: self._show_page(key))
            label.bind("<Enter>", lambda _event, key=name: self._set_navigation_hover(key, True))
            label.bind("<Leave>", lambda _event, key=name: self._set_navigation_hover(key, False))
            self.navigation_items[name] = (label, indicator)

        page_container = ttk.Frame(outer)
        page_container.grid(row=2, column=0, sticky="nsew")
        page_container.columnconfigure(0, weight=1)
        page_container.rowconfigure(0, weight=1)
        setup_tab = ttk.Frame(page_container, padding=14)
        self.review_tab = ttk.Frame(page_container, padding=14)
        maintenance_tab = ttk.Frame(page_container, padding=14)
        self.pages = {"setup": setup_tab, "review": self.review_tab, "maintenance": maintenance_tab}
        for page in self.pages.values():
            page.place(relx=0, rely=0, relwidth=1, relheight=1)

        setup_tab.columnconfigure(0, weight=1)
        sources = ttk.LabelFrame(setup_tab, text="资料来源与保存位置", padding=10)
        sources.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        sources.columnconfigure(1, weight=1)
        self._source_row(sources, 0)
        self._path_row(sources, 1, "复制结果到", self.output_var, self._choose_output, "选择文件夹", "确认后只复制分类结果到这里；原文件绝不会被移动或删除。")

        config = ttk.LabelFrame(setup_tab, text="分类设置", padding=10)
        config.grid(row=1, column=0, sticky="ew", pady=(0, 10))
        config.columnconfigure(1, weight=1)
        config.columnconfigure(3, weight=1)
        ttk.Label(config, text="历史截止年份：").grid(row=0, column=0, sticky="w")
        year_box = ttk.Spinbox(config, from_=1980, to=datetime.now().year + 1, textvariable=self.year_var, width=8)
        year_box.grid(row=0, column=1, sticky="w")
        ToolTip(year_box, "例如 2021 代表最后编辑年份为 2020 或更早的文件进入“历史文件”。")
        ttk.Label(config, text="低置信度阈值：").grid(row=0, column=2, padx=(20, 0), sticky="w")
        threshold_box = ttk.Spinbox(config, from_=0, to=100, increment=5, textvariable=self.threshold_var, width=6)
        threshold_box.grid(row=0, column=3, sticky="w")
        ttk.Label(config, text="%", style="Muted.TLabel").grid(row=0, column=4, padx=(4, 0), sticky="w")
        ToolTip(threshold_box, "模型置信度低于该百分比时，文件将进入“无法分类”，等待人工处理。")
        ttk.Label(config, text="低于此值会进入“无法分类”", style="Muted.TLabel").grid(row=0, column=5, padx=(12, 0), sticky="w")
        ttk.Label(config, text="DeepSeek API Key：").grid(row=1, column=0, pady=(10, 0), sticky="w")
        api_key_entry = ttk.Entry(config, textvariable=self.api_key_var, show="●")
        api_key_entry.grid(row=1, column=1, columnspan=3, pady=(10, 0), sticky="ew")
        api_key_entry.bind("<KeyRelease>", lambda _event: self._invalidate_api_verification())
        api_key_entry.bind("<FocusOut>", lambda _event: self._invalidate_api_verification())
        self.test_api_button = ttk.Button(config, text="测试连接并获取模型", command=self._test_api_connection, style="Soft.TButton")
        self.test_api_button.grid(row=1, column=4, columnspan=2, padx=(10, 0), pady=(10, 0), sticky="e")
        ToolTip(api_key_entry, "点击“测试连接并获取模型”后才会保存并使用该 Key；Key 优先保存在 Windows 凭据管理器。")
        self.api_status_var = tk.StringVar(value="输入 API Key 后测试连接；成功后可选择模型。")
        ttk.Label(config, textvariable=self.api_status_var, style="Muted.TLabel", wraplength=760).grid(row=2, column=1, columnspan=5, pady=(3, 0), sticky="w")
        ttk.Label(config, text="使用模型：").grid(row=3, column=0, pady=(10, 0), sticky="w")
        self.model_box = ttk.Combobox(config, textvariable=self.model_var, state="disabled")
        self.model_box.grid(row=3, column=1, columnspan=3, pady=(10, 0), sticky="ew")
        ToolTip(self.model_box, "连接成功后列出当前 API Key 可用模型；优先选用 deepseek-v4-flash。")

        scan_area = ttk.LabelFrame(setup_tab, text="开始扫描", padding=10)
        scan_area.grid(row=2, column=0, sticky="ew")
        scan_area.columnconfigure(0, weight=1)
        self.scan_button = ttk.Button(scan_area, text="扫描并生成分类建议", command=self.start_scan, style="Soft.TButton")
        self.scan_button.grid(row=0, column=0, sticky="w")
        ToolTip(self.scan_button, "读取文件内容并生成建议。扫描过程中不会复制、移动或删除任何文件。")
        self.status_var = tk.StringVar(value="请添加一个或多个资料文件夹；分类标准可留空使用默认模板。")
        ttk.Label(scan_area, textvariable=self.status_var, style="Status.TLabel", justify="left", wraplength=820).grid(row=1, column=0, pady=(9, 0), sticky="w")

        self.review_tab.columnconfigure(0, weight=1)
        self.review_tab.rowconfigure(2, weight=1)
        review_actions = ttk.Frame(self.review_tab)
        review_actions.grid(row=0, column=0, sticky="ew", pady=(0, 9))
        review_actions.columnconfigure(0, weight=1)
        ttk.Label(review_actions, textvariable=self.status_var, style="Status.TLabel", justify="left", wraplength=600).grid(row=0, column=0, sticky="w")
        self.edit_button = ttk.Button(review_actions, text="修改选中文件分类", command=self.edit_selected, style="Soft.TButton")
        self.edit_button.grid(row=0, column=1, padx=(10, 0))
        ToolTip(self.edit_button, "在表格中选择一个文件后，可手动更正其分类；也可以直接双击该行。")
        self.copy_button = ttk.Button(review_actions, text="确认后复制分类结果", command=self.copy_results, style="Soft.TButton", state="disabled")
        self.copy_button.grid(row=0, column=2, padx=(8, 0))
        ToolTip(self.copy_button, "复制前会检查磁盘可用空间；复制过程可恢复，原始文件保持不变。")
        self.open_button = ttk.Button(review_actions, text="打开结果文件夹", command=self.open_output, style="Soft.TButton")
        self.open_button.grid(row=0, column=3, padx=(8, 0))
        ToolTip(self.open_button, "打开已复制完成的分类结果文件夹。")

        copy_progress_area = ttk.Frame(self.review_tab)
        copy_progress_area.grid(row=1, column=0, sticky="ew", pady=(0, 9))
        copy_progress_area.columnconfigure(0, weight=1)
        self.copy_progress_var = tk.DoubleVar(value=0)
        self.copy_progress = ttk.Progressbar(copy_progress_area, mode="determinate", variable=self.copy_progress_var, maximum=1)
        self.copy_progress.grid(row=0, column=0, sticky="ew")
        self.stop_copy_button = ttk.Button(copy_progress_area, text="停止本次复制", command=self._request_copy_stop, style="Soft.TButton", state="disabled")
        self.stop_copy_button.grid(row=0, column=1, padx=(10, 0))
        ToolTip(self.stop_copy_button, "当前文件完成后停止；已完成的复制会保存在缓存中，下次可继续。")
        copy_progress_area.grid_remove()
        self.copy_progress_area = copy_progress_area

        results = ttk.LabelFrame(self.review_tab, text="分类结果（双击文件可修改分类）", padding=2)
        results.grid(row=2, column=0, sticky="nsew")
        results.columnconfigure(0, weight=1)
        results.rowconfigure(0, weight=1)
        columns = ("file", "suggestion", "confidence", "reason", "status")
        self.table = ttk.Treeview(results, columns=columns, show="headings", selectmode="browse")
        heads = {"file": "文件", "suggestion": "分类建议", "confidence": "置信度", "reason": "分类依据 / 提示", "status": "状态"}
        widths = {"file": 240, "suggestion": 190, "confidence": 75, "reason": 420, "status": 125}
        for name in columns:
            self.table.heading(name, text=heads[name])
            self.table.column(name, width=widths[name], minwidth=60, anchor="w")
        scrollbar = ttk.Scrollbar(results, orient="vertical", command=self.table.yview)
        self.table.configure(yscrollcommand=scrollbar.set)
        self.table.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.table.bind("<Double-1>", lambda _event: self.edit_selected())
        ToolTip(self.table, "查看 AI 的分类依据和置信度。双击一行即可人工调整结果。")

        maintenance_tab.columnconfigure(0, weight=1)
        about_panel = ttk.LabelFrame(maintenance_tab, text="软件信息", padding=14)
        about_panel.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        ttk.Label(about_panel, text=APP_VERSION, style="SectionTitle.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(about_panel, text="高中数学文件分类工具 · 本地安全复制与人工复核", style="Muted.TLabel").grid(row=1, column=0, pady=(3, 9), sticky="w")
        ttk.Label(about_panel, text="项目主页：", style="Muted.TLabel").grid(row=2, column=0, sticky="w")
        project_link = ttk.Label(about_panel, text=PROJECT_URL, style="Link.TLabel", cursor="hand2")
        project_link.grid(row=3, column=0, pady=(2, 10), sticky="w")
        project_link.bind("<Button-1>", self._open_project_page)
        acknowledgements_button = ttk.Button(about_panel, text="查看鸣谢与许可证", command=self._show_third_party_notices, style="Soft.TButton")
        acknowledgements_button.grid(row=4, column=0, sticky="w")

        rules_panel = ttk.LabelFrame(maintenance_tab, text="分类标准", padding=12)
        rules_panel.grid(row=1, column=0, sticky="ew", pady=(0, 10))
        rules_panel.columnconfigure(1, weight=1)
        ttk.Label(rules_panel, text="留空时使用软件自带的默认模板；需要替换时可输入或选择自己的 UTF-8 文本文件。", style="Muted.TLabel", wraplength=760).grid(row=0, column=0, columnspan=3, sticky="w")
        ttk.Label(rules_panel, text="自定义规则文件（可选）：").grid(row=1, column=0, pady=(10, 0), sticky="w")
        rules_entry = ttk.Entry(rules_panel, textvariable=self.rules_var)
        rules_entry.grid(row=1, column=1, pady=(10, 0), sticky="ew")
        rules_entry.bind("<FocusOut>", lambda _event: self._validate_rules(show_errors=False))
        rules_entry.bind("<Return>", lambda _event: self._validate_rules(show_errors=True))
        choose_rules_button = ttk.Button(rules_panel, text="选择文件", command=self._choose_rules, style="Soft.TButton")
        choose_rules_button.grid(row=1, column=2, padx=(8, 0), pady=(10, 0))
        self.rules_status_var = tk.StringVar()
        ttk.Label(rules_panel, textvariable=self.rules_status_var, style="Status.TLabel", wraplength=760).grid(row=2, column=1, columnspan=2, pady=(5, 0), sticky="w")
        check_rules_button = ttk.Button(rules_panel, text="检查规则", command=lambda: self._validate_rules(show_errors=True), style="Soft.TButton")
        check_rules_button.grid(row=3, column=0, pady=(10, 0), sticky="w")
        reset_rules_button = ttk.Button(rules_panel, text="恢复默认", command=self._use_default_rules, style="Soft.TButton")
        reset_rules_button.grid(row=3, column=1, padx=(8, 0), pady=(10, 0), sticky="w")
        edit_rules_button = ttk.Button(rules_panel, text="编辑当前规则", command=self._edit_rules, style="Soft.TButton")
        edit_rules_button.grid(row=3, column=2, padx=(8, 0), pady=(10, 0), sticky="e")
        ToolTip(rules_entry, "一级分类顶格书写；二级分类以空格或 Tab 缩进。输入后可点击“检查规则”验证。")
        ToolTip(edit_rules_button, "默认规则会先复制到你的本机设置目录，再以自定义规则方式打开编辑。")
        self._validate_rules(show_errors=False)

        cache_panel = ttk.LabelFrame(maintenance_tab, text="扫描缓存", padding=12)
        cache_panel.grid(row=2, column=0, sticky="ew")
        cache_panel.columnconfigure(0, weight=1)
        self.cache_info_var = tk.StringVar()
        self._update_cache_info()
        ttk.Label(cache_panel, textvariable=self.cache_info_var, style="Status.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(cache_panel, text="缓存会保存扫描进度和已完成的分类结果，以便意外关闭后继续；不会保存 API Key。", style="Muted.TLabel", wraplength=760).grid(row=1, column=0, pady=(5, 0), sticky="w")
        self.clear_cache_button = ttk.Button(cache_panel, text="删除扫描缓存", command=self.clear_scan_cache, style="Soft.TButton")
        self.clear_cache_button.grid(row=2, column=0, pady=(10, 0), sticky="w")
        ToolTip(self.clear_cache_button, "删除本机保存的扫描进度和分类建议。不会删除原文件、复制结果或分类清单。")
        self.after_idle(lambda: self._show_page("setup"))

    def _show_page(self, page_name: str) -> None:
        self.pages[page_name].tkraise()
        self.active_page = page_name
        for name, (label, indicator) in self.navigation_items.items():
            label.configure(style="NavigationActive.TLabel" if name == page_name else "Navigation.TLabel")
            indicator.configure(bg="#2563EB" if name == page_name else "#F4F7FB")

    def _set_navigation_hover(self, page_name: str, hovering: bool) -> None:
        if page_name == getattr(self, "active_page", None):
            return
        label, _indicator = self.navigation_items[page_name]
        label.configure(style="NavigationHover.TLabel" if hovering else "Navigation.TLabel")

    def _path_row(self, parent: ttk.Frame, row: int, label: str, variable: tk.StringVar, command, button: str, hint: str) -> None:
        ttk.Label(parent, text=f"{label}：").grid(row=row, column=0, sticky="w", pady=3)
        entry = ttk.Entry(parent, textvariable=variable)
        entry.grid(row=row, column=1, sticky="ew", pady=3)
        action = ttk.Button(parent, text=button, command=command, style="Soft.TButton")
        action.grid(row=row, column=2, padx=(8, 0), pady=3)
        ToolTip(entry, hint)
        ToolTip(action, hint)

    def _source_row(self, parent: ttk.Frame, row: int) -> None:
        hint = "可添加多个资料根文件夹；每个文件夹都会递归扫描。父文件夹与其子文件夹同时添加时，同一文件只会处理一次。"
        ttk.Label(parent, text="待整理文件夹：").grid(row=row, column=0, sticky="nw", pady=3)
        source_frame = ttk.Frame(parent)
        source_frame.grid(row=row, column=1, sticky="ew", pady=3)
        source_frame.columnconfigure(0, weight=1)
        self.source_list = tk.Listbox(source_frame, height=3, activestyle="none", exportselection=False)
        self.source_list.grid(row=0, column=0, sticky="ew")
        ToolTip(self.source_list, hint)
        buttons = ttk.Frame(parent)
        buttons.grid(row=row, column=2, padx=(8, 0), pady=3, sticky="n")
        add_button = ttk.Button(buttons, text="添加文件夹", command=self._choose_source, style="Soft.TButton")
        add_button.pack(fill="x")
        remove_button = ttk.Button(buttons, text="移除选中", command=self._remove_selected_source, style="Soft.TButton")
        remove_button.pack(fill="x", pady=(6, 0))
        ToolTip(add_button, hint)
        ToolTip(remove_button, "从本次待扫描列表中移除选中的资料文件夹，不会删除电脑中的任何文件。")
        self._refresh_source_list()

    def _refresh_source_list(self) -> None:
        self.source_list.delete(0, tk.END)
        for source in self.source_paths:
            self.source_list.insert(tk.END, str(source))

    def _choose_source(self) -> None:
        chosen = filedialog.askdirectory(title="添加待整理资料文件夹")
        if chosen:
            source = Path(chosen).expanduser().resolve()
            if source not in self.source_paths:
                self.source_paths.append(source)
                self._refresh_source_list()
            if not self.output_var.get():
                self.output_var.set(str(source.parent / "数学资料分类结果"))

    def _remove_selected_source(self) -> None:
        selected = self.source_list.curselection()
        if not selected:
            messagebox.showinfo(APP_NAME, "请先选择要移除的资料文件夹。")
            return
        del self.source_paths[selected[0]]
        self._refresh_source_list()

    def _choose_rules(self) -> None:
        chosen = filedialog.askopenfilename(title="选择分类标准.txt", filetypes=[("文本文件", "*.txt"), ("所有文件", "*.*")])
        if chosen:
            self.rules_var.set(chosen)
            self._validate_rules(show_errors=True)

    def _use_default_rules(self) -> None:
        self.rules_var.set("")
        self._validate_rules(show_errors=False)

    def _validate_rules(self, *, show_errors: bool) -> CategoryRules | None:
        rules_file = self._active_rules_file()
        try:
            rules = CategoryRules.load(rules_file)
        except (OSError, ValueError) as error:
            prefix = "自定义分类标准无效" if self.rules_var.get().strip() else "软件自带的分类标准无效"
            self.rules_status_var.set(f"{prefix}：{error}")
            if show_errors:
                messagebox.showerror(APP_NAME, f"{prefix}：\n{error}")
            return None

        source = "自定义分类标准" if self.rules_var.get().strip() else "软件自带的默认分类标准"
        secondary_count = sum(len(children) for children in rules.groups.values())
        self.rules_status_var.set(f"{source}已通过检查：{len(rules.groups)} 个一级分类，{secondary_count} 个二级分类。")
        if show_errors:
            messagebox.showinfo(APP_NAME, self.rules_status_var.get())
        return rules

    def _edit_rules(self) -> None:
        try:
            rules = self._active_rules_file() if self.rules_var.get().strip() else self._editable_default_rules()
        except (OSError, ValueError) as error:
            messagebox.showerror(APP_NAME, f"无法准备分类标准文件：{error}")
            return
        if not rules.is_file():
            messagebox.showerror(APP_NAME, "当前分类标准文件不存在，请先选择或创建一个文本文件。")
            return
        if os.name == "nt":
            os.startfile(rules)  # type: ignore[attr-defined]
        else:
            messagebox.showinfo(APP_NAME, f"分类标准文件：{rules}")

    def _choose_output(self) -> None:
        chosen = filedialog.askdirectory(title="选择分类结果保存位置")
        if chosen:
            self.output_var.set(chosen)

    def _read_inputs(self) -> tuple[list[Path], Path, Path, CategoryRules, int, float]:
        sources = [source.expanduser() for source in self.source_paths]
        rules_file = self._active_rules_file()
        output = Path(self.output_var.get()).expanduser()
        if not sources:
            raise ValueError("请至少添加一个待整理资料文件夹。")
        invalid_sources = [str(source) for source in sources if not source.is_dir()]
        if invalid_sources:
            raise ValueError(f"以下待整理资料文件夹无效：\n" + "\n".join(invalid_sources))
        if not self.output_var.get().strip():
            raise ValueError("请选择分类结果保存位置。")
        try:
            cutoff_year = int(self.year_var.get())
            threshold_percent = float(self.threshold_var.get())
        except ValueError as error:
            raise ValueError("历史截止年份和置信度阈值必须是数字。") from error
        if not 1980 <= cutoff_year <= datetime.now().year + 1:
            raise ValueError("历史截止年份不在可用范围内。")
        if not 0 <= threshold_percent <= 100:
            raise ValueError("置信度阈值必须在 0% 到 100% 之间。")
        threshold = threshold_percent / 100
        rules = CategoryRules.load(rules_file)
        self._validate_rules(show_errors=False)
        return sources, output, rules_file, rules, cutoff_year, threshold

    def start_scan(self) -> None:
        if self.busy:
            return
        try:
            sources, output, rules_file, rules, cutoff_year, threshold = self._read_inputs()
            files = self._documents_under(sources, output)
            if not files:
                raise ValueError("所选文件夹内没有 PDF、DOCX 或 PPTX 文件。")
            historical_files = [file for file in files if datetime.fromtimestamp(file.stat().st_mtime).year < cutoff_year]
            if len(historical_files) != len(files):
                if not self.api_key_var.get().strip():
                    raise ValueError("请填写 DeepSeek API Key，或将所有文件设为历史文件。")
                if self.api_testing:
                    raise ValueError("正在测试 API 连接，请等待完成后再扫描。")
                if self.api_key_var.get().strip() != self.api_verified_key or (self.api_url_var.get().strip() or DEFAULT_API_URL) != self.api_verified_url:
                    raise ValueError("请先点击“测试连接并获取模型”，确认 API Key 可用后再扫描。")
                if not self.model_var.get().strip() or self.model_var.get().strip() not in self.available_models:
                    raise ValueError("请从已获取的模型列表中选择一个模型。")
            cache_config = self._cache_config(sources, output, rules_file, cutoff_year, threshold)
            cache_key = self._cache_key(cache_config)
        except Exception as error:
            messagebox.showerror(APP_NAME, str(error))
            return
        self._save_settings()
        session = self._cache_session(cache_key, cache_config, create=True)
        assert session is not None
        restored = self._cached_items_for_files(session, files)
        self.active_cache_key = cache_key
        self.items = [restored[file.resolve()] for file in files if file.resolve() in restored]
        pending_files = [file for file in files if file.resolve() not in restored]
        self._write_scan_cache()
        self._update_cache_info()
        self._refresh_table()
        self._show_page("review")
        if not pending_files:
            self._scan_finished()
            return
        self.busy = True
        self.scan_button.configure(state="disabled")
        self.copy_button.configure(state="disabled")
        self.clear_cache_button.configure(state="disabled")
        self.status_var.set(f"已从缓存恢复 {len(self.items)} 个结果，正在继续处理 {len(pending_files)} 个文件。")
        threading.Thread(target=self._scan_worker, args=(pending_files, rules, cutoff_year, threshold, self.api_key_var.get().strip(), self.api_url_var.get().strip() or DEFAULT_API_URL, self.model_var.get().strip() or DEFAULT_MODEL, cache_key), daemon=True).start()

    @staticmethod
    def _documents_under(sources: list[Path], output: Path) -> list[Path]:
        result: dict[Path, Path] = {}
        resolved_output = output.resolve()
        for source in OrganizerApp._non_overlapping_sources(sources):
            for file in source.rglob("*"):
                # macOS resource forks (._*) and Microsoft Office lock files (~$*) are not real documents.
                if file.name.startswith(("._", "~$")):
                    continue
                if not file.is_file() or file.suffix.lower() not in SUPPORTED_EXTENSIONS:
                    continue
                resolved_file = file.resolve()
                if resolved_file.is_relative_to(resolved_output):
                    continue
                # Using the resolved path prevents duplicate work when selected folders overlap.
                result.setdefault(resolved_file, file)
        return sorted(result.values(), key=lambda item: str(item).lower())

    @staticmethod
    def _non_overlapping_sources(sources: list[Path]) -> list[Path]:
        roots: list[Path] = []
        for source in sorted((source.resolve() for source in sources), key=lambda item: (len(item.parts), str(item).lower())):
            if any(source.is_relative_to(root) for root in roots):
                continue
            roots.append(source)
        return roots

    def _scan_worker(self, files: list[Path], rules: CategoryRules, cutoff_year: int, threshold: float, api_key: str, api_url: str, model: str, cache_key: str) -> None:
        for number, file in enumerate(files, 1):
            self.after(0, self.status_var.set, f"正在处理 {number}/{len(files)}：{file.name}")
            try:
                if datetime.fromtimestamp(file.stat().st_mtime).year < cutoff_year:
                    item = ReviewItem(file, Classification("historical", None, None, 1.0, f"最后编辑年份早于 {cutoff_year}。"))
                else:
                    extracted = extract_document(file)
                    classified = classify_with_deepseek(api_key=api_key, filename=file.name, content=extracted.text, rules=rules, api_url=api_url, model=model)
                    note = "；".join(extracted.warnings)
                    if extracted.ocr_used:
                        note = (note + "；" if note else "") + "已使用本机 OCR"
                    if classified.confidence < threshold:
                        classified = Classification("unclassifiable", None, None, classified.confidence, f"置信度低于设定阈值 {threshold:.0%}。{classified.reason}")
                    item = ReviewItem(file, classified, note)
            except Exception as error:
                item = ReviewItem(file, Classification("unclassifiable", None, None, 0.0, f"处理失败：{error}"))
                try:
                    self._cache_store_failure(cache_key, file, error)
                except Exception as cache_error:
                    item.note = f"缓存保存失败：{cache_error}"
            else:
                try:
                    self._cache_store_completed(cache_key, item)
                except Exception as cache_error:
                    item.note = (item.note + "；" if item.note else "") + f"缓存保存失败：{cache_error}"
            self.items.append(item)
            self.after(0, self._refresh_table)
            self.after(0, self._update_cache_info)
        self.after(0, self._scan_finished)

    def _scan_finished(self) -> None:
        self.busy = False
        self.scan_button.configure(state="normal")
        self.copy_button.configure(state="normal" if self.items else "disabled")
        self.clear_cache_button.configure(state="normal")
        session = self._cache_session(self.active_cache_key, {}, create=False) if self.active_cache_key else None
        failed = len(session.get("failed", {})) if isinstance(session, dict) else 0
        if failed:
            self.status_var.set(f"已完成 {len(self.items)} 个文件的扫描，其中 {failed} 个处理失败；下次点击扫描会重试。请复核后再复制。")
        else:
            self.status_var.set(f"已完成 {len(self.items)} 个文件的扫描。请复核后再复制。")

    def _refresh_table(self) -> None:
        self.table.delete(*self.table.get_children())
        output = Path(self.output_var.get()).expanduser() if self.output_var.get().strip() else None
        copy_records = self._copy_records_snapshot(self.active_cache_key, output) if output else {}
        copy_statuses = {"copied": "已复制", "duplicate": "内容重复，已跳过", "failed": "复制失败"}
        for index, item in enumerate(self.items):
            details = item.result.reason + (f" 〔{item.note}〕" if item.note else "")
            record = copy_records.get(str(item.source.resolve()), {})
            try:
                is_current = (
                    record.get("fingerprint") == self._file_fingerprint(item.source)
                    and record.get("classification") == str(self._relative_destination(item))
                ) if isinstance(record, dict) else False
            except OSError:
                is_current = False
            status = copy_statuses.get(record.get("status"), "待人工复核") if is_current else "待人工复核"
            if is_current and status == "复制失败" and record.get("error"):
                details += f" 〔复制失败：{record['error']}〕"
            self.table.insert("", "end", iid=str(index), values=(item.source.name, item.label, f"{item.result.confidence:.0%}", details, status))

    def edit_selected(self) -> None:
        selected = self.table.selection()
        if not selected:
            messagebox.showinfo(APP_NAME, "请先在表格中选择一个文件。")
            return
        index = int(selected[0])
        item = self.items[index]
        dialog = tk.Toplevel(self)
        dialog.title("修改分类")
        dialog.transient(self)
        dialog.grab_set()
        dialog.resizable(False, False)
        ttk.Label(dialog, text=item.source.name, wraplength=460).grid(row=0, column=0, columnspan=2, padx=15, pady=(15, 8), sticky="w")
        options = ["历史文件", "综合文件", "无法分类"]
        try:
            rules_file = self._active_rules_file()
            rules = CategoryRules.load(rules_file)
            for primary, children in rules.groups.items():
                options.append(primary)
                options.extend(f"{primary} / {child}" for child in children)
        except (OSError, ValueError):
            pass
        choice = tk.StringVar(value=item.label)
        ttk.Label(dialog, text="放入：").grid(row=1, column=0, padx=15, pady=8, sticky="w")
        ttk.Combobox(dialog, textvariable=choice, values=options, state="readonly", width=42).grid(row=1, column=1, padx=(0, 15), pady=8)

        def save() -> None:
            value = choice.get()
            if value == "历史文件":
                item.result = Classification("historical", None, None, 1.0, "由老师手动指定。")
            elif value == "综合文件":
                item.result = Classification("comprehensive", None, None, 1.0, "由老师手动指定。")
            elif value == "无法分类":
                item.result = Classification("unclassifiable", None, None, 1.0, "由老师手动指定。")
            elif " / " in value:
                primary, secondary = value.split(" / ", 1)
                item.result = Classification("secondary", primary, secondary, 1.0, "由老师手动指定。")
            else:
                item.result = Classification("primary_only", value, None, 1.0, "由老师手动指定。")
            try:
                self._cache_store_completed(self.active_cache_key, item)
                self._update_cache_info()
            except OSError as error:
                messagebox.showwarning(APP_NAME, f"分类已修改，但缓存保存失败：{error}")
            dialog.destroy()
            self._refresh_table()

        ttk.Button(dialog, text="保存", command=save, style="Soft.TButton").grid(row=2, column=1, padx=15, pady=(4, 15), sticky="e")

    def copy_results(self) -> None:
        if self.busy or not self.items:
            return
        try:
            sources, output, rules_file, _categories, cutoff_year, threshold = self._read_inputs()
            config = self._cache_config(sources, output, rules_file, cutoff_year, threshold)
            cache_key = self._cache_key(config)
            self._cache_session(cache_key, config, create=True)
            self.active_cache_key = cache_key
            self._write_scan_cache()
        except Exception as error:
            messagebox.showerror(APP_NAME, f"无法开始复制：{error}")
            return

        self.copy_cancel_requested.clear()
        self._set_copy_operation_active(True, total=len(self.items))
        items = list(self.items)
        threading.Thread(target=self._copy_preflight_worker, args=(items, output, cache_key), daemon=True).start()

    def _set_copy_operation_active(self, active: bool, *, total: int = 1) -> None:
        self.busy = active
        self.scan_button.configure(state="disabled" if active else "normal")
        self.copy_button.configure(state="disabled" if active else ("normal" if self.items else "disabled"))
        self.clear_cache_button.configure(state="disabled" if active else "normal")
        self.edit_button.configure(state="disabled" if active else "normal")
        self.open_button.configure(state="disabled" if active else "normal")
        self.stop_copy_button.configure(state="normal" if active else "disabled")
        if active:
            self.copy_progress.configure(maximum=max(total, 1))
            self.copy_progress_var.set(0)
            self.copy_progress_area.grid()

    def _request_copy_stop(self) -> None:
        if self.busy:
            self.copy_cancel_requested.set()
            self.stop_copy_button.configure(state="disabled")
            self.status_var.set("将在当前检查或复制步骤完成后停止；已完成项目会保留在缓存中。")

    def _update_copy_progress(self, current: int, total: int, status: str) -> None:
        self.copy_progress.configure(maximum=max(total, 1))
        self.copy_progress_var.set(current)
        self.status_var.set(status)

    @staticmethod
    def _copy_reserve_bytes(required: int) -> int:
        return COPY_RESERVE_BYTES if required else 0

    @staticmethod
    def _disk_usage_for(path: Path):
        probe = path.expanduser()
        while not probe.exists() and probe != probe.parent:
            probe = probe.parent
        return shutil.disk_usage(probe)

    def _copy_preflight_worker(self, items: list[ReviewItem], output: Path, cache_key: str) -> None:
        records = self._copy_records_snapshot(cache_key, output)
        tasks: list[dict] = []
        planned_hashes: set[str] = set()
        known_destinations: dict[str, Path] = {}
        required = 0

        for number, item in enumerate(items, 1):
            relative = self._relative_destination(item)
            source_key = str(item.source.resolve())
            record = records.get(source_key, {})
            if self.copy_cancel_requested.is_set():
                self.after(0, self._copy_preflight_finished, tasks, output, cache_key, 0, 0, 0, True)
                return
            try:
                if self._copy_record_is_complete(record, item, relative):
                    sha256 = str(record["sha256"])
                    destination = Path(str(record["destination"]))
                    known_destinations[sha256] = destination
                    tasks.append({"item": item, "relative": relative, "sha256": sha256, "action": "already", "destination": destination})
                else:
                    sha256 = self._sha256_file(item.source)
                    destination = output / relative / item.source.name
                    if destination.is_file() and destination.stat().st_size == item.source.stat().st_size and self._sha256_file(destination) == sha256:
                        known_destinations[sha256] = destination
                        tasks.append({"item": item, "relative": relative, "sha256": sha256, "action": "existing", "destination": destination})
                    elif sha256 in known_destinations or sha256 in planned_hashes:
                        tasks.append({"item": item, "relative": relative, "sha256": sha256, "action": "duplicate"})
                    else:
                        planned_hashes.add(sha256)
                        required += item.source.stat().st_size
                        tasks.append({"item": item, "relative": relative, "sha256": sha256, "action": "copy"})
            except Exception as error:
                error_text = str(error)
                tasks.append({"item": item, "relative": relative, "action": "failed", "error": error_text})
                try:
                    self._cache_store_copy_result(cache_key, output, item, relative, status="failed", error=error_text)
                except OSError:
                    pass
            self.after(0, self._update_copy_progress, number, len(items), f"正在检查 {number}/{len(items)}：{item.source.name}")

        reserve = self._copy_reserve_bytes(required)
        try:
            available = self._disk_usage_for(output).free
        except OSError as error:
            self.after(0, self._copy_preflight_error, f"无法读取目标磁盘容量：{error}")
            return
        self.after(0, self._copy_preflight_finished, tasks, output, cache_key, required, reserve, available, False)

    def _copy_preflight_error(self, message: str) -> None:
        self._set_copy_operation_active(False)
        self.copy_progress_area.grid_remove()
        self.status_var.set(message)
        messagebox.showerror(APP_NAME, message)

    def _copy_preflight_finished(
        self,
        tasks: list[dict],
        output: Path,
        cache_key: str,
        required: int,
        reserve: int,
        available: int,
        cancelled: bool,
    ) -> None:
        if cancelled or self.copy_cancel_requested.is_set():
            self._set_copy_operation_active(False)
            self.copy_progress_area.grid_remove()
            self.status_var.set("复制检查已停止，尚未复制文件。")
            return

        needed = required + reserve
        if available < needed:
            self._set_copy_operation_active(False)
            self.copy_progress_area.grid_remove()
            message = (
                f"目标磁盘可用空间不足。\n\n"
                f"待复制内容：{self._format_size(required)}\n"
                f"安全冗余：{self._format_size(reserve)}（固定预留）\n"
                f"所需可用空间：{self._format_size(needed)}\n"
                f"当前可用空间：{self._format_size(available)}\n\n"
                "请释放空间或选择其他结果文件夹后重试。"
            )
            self.status_var.set("目标磁盘可用空间不足，未开始复制。")
            messagebox.showerror(APP_NAME, message)
            return

        new_count = sum(task["action"] == "copy" for task in tasks)
        duplicate_count = sum(task["action"] in {"duplicate", "existing"} for task in tasks)
        resumed_count = sum(task["action"] == "already" for task in tasks)
        failed_count = sum(task["action"] == "failed" for task in tasks)
        confirmation = (
            f"将处理 {len(tasks)} 个分类结果：\n"
            f"- 需要新复制：{new_count} 个\n"
            f"- 内容重复、无需新复制：{duplicate_count} 个\n"
            f"- 缓存中已完成、无需重复复制：{resumed_count} 个\n"
            f"- 检查失败：{failed_count} 个\n\n"
            f"待复制内容：{self._format_size(required)}\n"
            f"复制后至少保留：{self._format_size(reserve)}\n"
            f"目标磁盘当前可用：{self._format_size(available)}\n\n"
            f"结果将保存到：\n{output}\n\n"
            "原文件不会被移动或删除。是否开始复制？"
        )
        if not messagebox.askyesno(APP_NAME, confirmation):
            self._set_copy_operation_active(False)
            self.copy_progress_area.grid_remove()
            self.status_var.set("已取消复制；尚未写入新的分类结果。")
            return

        self.copy_progress_var.set(0)
        threading.Thread(target=self._copy_worker, args=(tasks, output, cache_key), daemon=True).start()

    def _copy_worker(self, tasks: list[dict], output: Path, cache_key: str) -> None:
        destinations_by_hash: dict[str, Path] = {}
        copied = duplicates = resumed = failed = 0
        errors: list[str] = []
        stopped = False

        try:
            output.mkdir(parents=True, exist_ok=True)
        except Exception as error:
            error_text = f"无法创建结果文件夹：{error}"
            for task in tasks:
                item: ReviewItem = task["item"]
                try:
                    self._cache_store_copy_result(cache_key, output, item, task["relative"], status="failed", sha256=task.get("sha256"), error=error_text)
                except OSError:
                    pass
            self.after(0, self._copy_finished, 0, 0, 0, len(tasks), [error_text], False)
            return

        for number, task in enumerate(tasks, 1):
            item: ReviewItem = task["item"]
            relative: Path = task["relative"]
            if self.copy_cancel_requested.is_set():
                stopped = True
                break
            try:
                action = task["action"]
                if action == "failed":
                    failed += 1
                    errors.append(f"{item.source.name}：{task['error']}")
                elif action == "already":
                    resumed += 1
                    destinations_by_hash[task["sha256"]] = task["destination"]
                elif action == "existing":
                    duplicates += 1
                    destination = task["destination"]
                    destinations_by_hash[task["sha256"]] = destination
                    self._cache_store_copy_result(cache_key, output, item, relative, status="duplicate", sha256=task["sha256"], destination=destination)
                else:
                    sha256 = task["sha256"]
                    destination = destinations_by_hash.get(sha256)
                    if destination and destination.is_file():
                        duplicates += 1
                        self._cache_store_copy_result(cache_key, output, item, relative, status="duplicate", sha256=sha256, destination=destination)
                    else:
                        destination_dir = output / relative
                        destination_dir.mkdir(parents=True, exist_ok=True)
                        candidate = destination_dir / item.source.name
                        if candidate.is_file() and candidate.stat().st_size == item.source.stat().st_size and self._sha256_file(candidate) == sha256:
                            duplicates += 1
                            destination = candidate
                            self._cache_store_copy_result(cache_key, output, item, relative, status="duplicate", sha256=sha256, destination=destination)
                        else:
                            destination = self._non_conflicting_name(candidate)
                            temporary = destination.with_name(f".{destination.name}.partial")
                            shutil.copy2(item.source, temporary)
                            if temporary.stat().st_size != item.source.stat().st_size:
                                raise OSError("复制后的文件大小与原文件不一致。")
                            os.replace(temporary, destination)
                            copied += 1
                            self._cache_store_copy_result(cache_key, output, item, relative, status="copied", sha256=sha256, destination=destination)
                        destinations_by_hash[sha256] = destination
            except Exception as error:
                failed += 1
                error_text = str(error)
                errors.append(f"{item.source.name}：{error_text}")
                try:
                    self._cache_store_copy_result(cache_key, output, item, relative, status="failed", sha256=task.get("sha256"), error=error_text)
                except OSError as cache_error:
                    errors.append(f"{item.source.name} 的复制错误无法写入缓存：{cache_error}")
            self.after(0, self._update_copy_progress, number, len(tasks), f"正在复制 {number}/{len(tasks)}：{item.source.name}")

        try:
            rows = self._copy_report_rows(cache_key, output, tasks)
            self._write_report(output, rows)
        except Exception as error:
            failed += 1
            errors.append(f"分类清单写入失败：{error}")
        self.after(0, self._copy_finished, copied, duplicates, resumed, failed, errors, stopped)

    def _copy_report_rows(self, cache_key: str, output: Path, tasks: list[dict]) -> list[list[str]]:
        records = self._copy_records_snapshot(cache_key, output)
        status_names = {"copied": "已复制", "duplicate": "内容重复，未复制", "failed": "复制失败"}
        rows: list[list[str]] = []
        for task in tasks:
            item: ReviewItem = task["item"]
            record = records.get(str(item.source.resolve()), {})
            status = str(record.get("status", "pending"))
            destination = str(record.get("destination", ""))
            if status == "duplicate" and destination:
                destination = f"内容与此文件相同：{destination}"
            rows.append([
                str(item.source),
                destination,
                status_names.get(status, "未完成"),
                item.label,
                f"{item.result.confidence:.0%}",
                item.result.reason,
                item.note,
                str(record.get("error", "")),
            ])
        return rows

    def _copy_finished(self, copied: int, duplicates: int, resumed: int, failed: int, errors: list[str], stopped: bool) -> None:
        self._set_copy_operation_active(False)
        self._update_cache_info()
        self._refresh_table()
        completed = copied + duplicates + resumed
        if stopped:
            self.status_var.set(f"复制已停止：已完成或跳过 {completed} 项，尚有未完成项目可下次继续。")
            messagebox.showinfo(APP_NAME, "复制已停止。已完成的项目和错误信息已写入缓存；下次点击复制会继续未完成项目。")
            return
        if failed:
            summary = f"复制完成，但有 {failed} 项失败；成功或跳过 {completed} 项。错误信息已写入缓存和分类清单。"
            self.status_var.set(summary)
            detail = "\n".join(errors[:5])
            more = f"\n另有 {len(errors) - 5} 项错误，请查看分类清单。" if len(errors) > 5 else ""
            messagebox.showwarning(APP_NAME, f"{summary}\n\n{detail}{more}")
            return
        self.status_var.set(f"复制完成：新复制 {copied} 项，跳过重复内容 {duplicates} 项，恢复已完成 {resumed} 项。")
        messagebox.showinfo(APP_NAME, "复制完成。原文件保持不变，分类清单已保存到结果文件夹。")

    def _relative_destination(self, item: ReviewItem) -> Path:
        result = item.result
        if result.kind == "historical":
            return Path("历史文件")
        if result.kind == "comprehensive":
            return Path("综合文件")
        if result.kind == "unclassifiable":
            return Path("无法分类")
        if result.kind == "secondary" and result.primary and result.secondary:
            return Path(result.primary) / result.secondary
        return Path(result.primary or "无法分类")

    @staticmethod
    def _non_conflicting_name(destination: Path) -> Path:
        if not destination.exists():
            return destination
        for number in range(1, 10000):
            candidate = destination.with_name(f"{destination.stem} ({number}){destination.suffix}")
            if not candidate.exists():
                return candidate
        raise RuntimeError(f"无法为重名文件生成安全名称：{destination.name}")

    @staticmethod
    def _write_report(output: Path, rows: list[list[str]]) -> None:
        with (output / "分类清单.csv").open("w", newline="", encoding="utf-8-sig") as report:
            writer = csv.writer(report)
            writer.writerow(["原文件", "复制结果", "状态", "分类", "置信度", "分类依据", "提取提示", "错误信息"])
            writer.writerows(rows)

    def open_output(self) -> None:
        destination = Path(self.output_var.get()).expanduser()
        if not destination.is_dir():
            messagebox.showinfo(APP_NAME, "结果文件夹尚未建立。请先完成一次复制。")
            return
        if os.name == "nt":
            os.startfile(destination)  # type: ignore[attr-defined]
        else:
            messagebox.showinfo(APP_NAME, f"结果文件夹：{destination}")


def run() -> None:
    configure_ocr_engine()
    OrganizerApp().mainloop()
