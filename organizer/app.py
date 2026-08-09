from __future__ import annotations

import csv
import json
import os
import shutil
import sys
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
import tkinter as tk

import keyring

from .category_rules import CategoryRules
from .classifier import Classification, DEFAULT_API_URL, DEFAULT_MODEL, classify_with_deepseek
from .extractors import SUPPORTED_EXTENSIONS, configure_ocr_engine, extract_document

APP_NAME = "高中数学文件分类工具"
KEYRING_SERVICE = "HighSchoolMathFileOrganizer"


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
        self.geometry("1240x720")
        self.minsize(960, 600)
        self._configure_styles()
        self.items: list[ReviewItem] = []
        self.busy = False
        self._load_settings()
        self._build_widgets()

    def _app_data_dir(self) -> Path:
        base = Path(os.environ.get("APPDATA", Path.home() / ".config")) / "HighSchoolMathFileOrganizer"
        base.mkdir(parents=True, exist_ok=True)
        return base

    def _settings_path(self) -> Path:
        return self._app_data_dir() / "settings.json"

    def _editable_default_rules(self) -> Path:
        visible_template = Path(sys.executable).parent / "category_rules.txt"
        if visible_template.is_file():
            return visible_template
        target = self._app_data_dir() / "分类标准.txt"
        if target.is_file():
            return target
        roots = [Path(sys.executable).parent, Path(getattr(sys, "_MEIPASS", "")), Path(__file__).resolve().parents[1]]
        for root in roots:
            for candidate in (root / "category_rules.txt", root / "defaults" / "category_rules.txt", root / "defaults" / "分类标准.txt", root / "分类标准.txt"):
                if candidate.is_file():
                    shutil.copy2(candidate, target)
                    return target
        return target

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
        defaults = {"source": "", "rules": "", "output": "", "year": str(datetime.now().year - 5), "threshold": "0.70", "api_url": DEFAULT_API_URL, "model": DEFAULT_MODEL}
        try:
            loaded = json.loads(self._settings_path().read_text(encoding="utf-8"))
            defaults.update({key: value for key, value in loaded.items() if key in defaults and key != "rules"})
        except (OSError, json.JSONDecodeError):
            pass
        self.source_var = tk.StringVar(value=defaults["source"])
        self.rules_var = tk.StringVar(value=defaults["rules"])
        self.output_var = tk.StringVar(value=defaults["output"])
        self.year_var = tk.StringVar(value=defaults["year"])
        self.threshold_var = tk.StringVar(value=defaults["threshold"])
        self.api_url_var = tk.StringVar(value=defaults["api_url"])
        self.model_var = tk.StringVar(value=defaults["model"])
        try:
            saved_key = keyring.get_password(KEYRING_SERVICE, "deepseek_api_key") or ""
        except keyring.errors.KeyringError:
            saved_key = ""
        self.api_key_var = tk.StringVar(value=saved_key)

    def _configure_styles(self) -> None:
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        background = "#F4F7FB"
        self.configure(bg=background)
        style.configure("TFrame", background=background)
        style.configure("Header.TFrame", background="#EAF2FF")
        style.configure("TLabel", background=background, foreground="#1F2937", font=("Microsoft YaHei UI", 9))
        style.configure("Title.TLabel", background="#EAF2FF", foreground="#123B6D", font=("Microsoft YaHei UI", 16, "bold"))
        style.configure("SubTitle.TLabel", background="#EAF2FF", foreground="#4B5563", font=("Microsoft YaHei UI", 9))
        style.configure("TLabelframe", background=background, bordercolor="#D7E0ED")
        style.configure("TLabelframe.Label", background=background, foreground="#123B6D", font=("Microsoft YaHei UI", 9, "bold"))
        style.configure("TEntry", padding=5)
        style.configure("TCombobox", padding=5)
        style.configure("TButton", padding=(10, 6), font=("Microsoft YaHei UI", 9))
        style.configure("Accent.TButton", background="#2563EB", foreground="white")
        style.map("Accent.TButton", background=[("active", "#1D4ED8"), ("disabled", "#A8BCE9")])
        style.configure("Treeview", rowheight=30, font=("Microsoft YaHei UI", 9), background="white", fieldbackground="white")
        style.configure("Treeview.Heading", font=("Microsoft YaHei UI", 9, "bold"), background="#E8EEF8", foreground="#183B67", padding=7)

    def _save_settings(self) -> None:
        settings = {"source": self.source_var.get(), "output": self.output_var.get(), "year": self.year_var.get(), "threshold": self.threshold_var.get(), "api_url": self.api_url_var.get(), "model": self.model_var.get()}
        self._settings_path().write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")
        if self.api_key_var.get().strip():
            try:
                keyring.set_password(KEYRING_SERVICE, "deepseek_api_key", self.api_key_var.get().strip())
            except keyring.errors.KeyringError:
                # 即使凭据管理器不可用，也不阻止本次使用；密钥不会写入普通设置文件。
                pass

    def _build_widgets(self) -> None:
        outer = ttk.Frame(self, padding=12)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(1, weight=1)

        header = ttk.Frame(outer, style="Header.TFrame", padding=(16, 12))
        header.grid(row=0, column=0, columnspan=3, sticky="ew", pady=(0, 12))
        ttk.Label(header, text=APP_NAME, style="Title.TLabel").pack(anchor="w")
        ttk.Label(header, text="安全复制原文件 · AI 提供建议 · 老师最后复核", style="SubTitle.TLabel").pack(anchor="w", pady=(3, 0))

        self._path_row(outer, 1, "待整理文件夹", self.source_var, self._choose_source, "选择文件夹", "选择需要扫描的资料根文件夹；会递归读取 PDF、DOCX 和 PPTX。")
        self._path_row(outer, 2, "分类标准（可选）", self.rules_var, self._choose_rules, "选择文件", "留空时自动使用程序同级的 category_rules.txt；也可选择资料文件夹中的 分类标准.txt。")
        self._path_row(outer, 3, "复制结果到", self.output_var, self._choose_output, "选择文件夹", "确认后只复制分类结果到这里；原文件绝不会被移动或删除。")

        config = ttk.LabelFrame(outer, text="分类设置", padding=8)
        config.grid(row=4, column=0, columnspan=3, sticky="ew", pady=(8, 10))
        for column in (1, 3, 5):
            config.columnconfigure(column, weight=1)
        ttk.Label(config, text="历史截止年份：").grid(row=0, column=0, sticky="w")
        year_box = ttk.Spinbox(config, from_=1980, to=datetime.now().year + 1, textvariable=self.year_var, width=8)
        year_box.grid(row=0, column=1, sticky="w")
        ToolTip(year_box, "编辑年份，例如 2021 代表最后编辑年份为 2020 或更早的文件进入“历史文件”。")
        ttk.Label(config, text="低置信度阈值：").grid(row=0, column=2, padx=(16, 0), sticky="w")
        threshold_box = ttk.Spinbox(config, from_=0.1, to=1.0, increment=0.05, textvariable=self.threshold_var, width=8)
        threshold_box.grid(row=0, column=3, sticky="w")
        ToolTip(threshold_box, "模型置信度低于该值时，文件将进入“无法分类”，等待人工处理。")
        ttk.Label(config, text="低于此值进入“无法分类”").grid(row=0, column=4, columnspan=2, sticky="w")
        ttk.Label(config, text="DeepSeek API Key：").grid(row=1, column=0, pady=(8, 0), sticky="w")
        api_key_entry = ttk.Entry(config, textvariable=self.api_key_var, show="●", width=36)
        api_key_entry.grid(row=1, column=1, columnspan=3, pady=(8, 0), sticky="ew")
        ToolTip(api_key_entry, "仅用于调用 DeepSeek；会优先保存在 Windows 凭据管理器，不写入普通设置文件。")
        ttk.Label(config, text="首次使用填写一次；安全保存在 Windows 凭据管理器。").grid(row=1, column=4, columnspan=2, pady=(8, 0), sticky="w")

        actions = ttk.Frame(outer)
        actions.grid(row=5, column=0, columnspan=3, sticky="ew", pady=(0, 8))
        self.scan_button = ttk.Button(actions, text="1. 扫描并生成分类建议", command=self.start_scan, style="Accent.TButton")
        self.scan_button.pack(side="left")
        ToolTip(self.scan_button, "读取文件内容并生成建议。扫描过程中不会复制、移动或删除任何文件。")
        edit_button = ttk.Button(actions, text="修改选中文件分类", command=self.edit_selected)
        edit_button.pack(side="left", padx=8)
        ToolTip(edit_button, "在表格中选择一个文件后，可手动更正其分类；也可以直接双击该行。")
        edit_rules_button = ttk.Button(actions, text="编辑分类标准", command=self._edit_rules)
        edit_rules_button.pack(side="left")
        ToolTip(edit_rules_button, "用记事本打开当前分类标准；路径留空时打开程序自带模板。顶格为一级分类，缩进为二级分类。")
        self.copy_button = ttk.Button(actions, text="2. 确认后复制分类结果", command=self.copy_results, state="disabled")
        self.copy_button.pack(side="left")
        ToolTip(self.copy_button, "仅在你确认后执行安全复制；同名文件会自动编号，原始文件保持不变。")
        open_button = ttk.Button(actions, text="打开结果文件夹", command=self.open_output)
        open_button.pack(side="left", padx=8)
        ToolTip(open_button, "打开已复制完成的分类结果文件夹。")
        self.status_var = tk.StringVar(value="请选择资料文件夹；分类标准可留空使用默认模板。")
        ttk.Label(actions, textvariable=self.status_var).pack(side="right")

        columns = ("file", "suggestion", "confidence", "reason", "status")
        self.table = ttk.Treeview(outer, columns=columns, show="headings", selectmode="browse")
        heads = {"file": "文件", "suggestion": "分类建议", "confidence": "置信度", "reason": "分类依据 / 提示", "status": "状态"}
        widths = {"file": 285, "suggestion": 220, "confidence": 75, "reason": 470, "status": 130}
        for name in columns:
            self.table.heading(name, text=heads[name])
            self.table.column(name, width=widths[name], minwidth=60, anchor="w")
        scrollbar = ttk.Scrollbar(outer, orient="vertical", command=self.table.yview)
        self.table.configure(yscrollcommand=scrollbar.set)
        self.table.grid(row=6, column=0, columnspan=2, sticky="nsew")
        scrollbar.grid(row=6, column=2, sticky="ns")
        outer.rowconfigure(6, weight=1)
        self.table.bind("<Double-1>", lambda _event: self.edit_selected())
        ToolTip(self.table, "查看 AI 的分类依据和置信度。双击一行即可人工调整结果。")

    def _path_row(self, parent: ttk.Frame, row: int, label: str, variable: tk.StringVar, command, button: str, hint: str) -> None:
        ttk.Label(parent, text=f"{label}：").grid(row=row, column=0, sticky="w", pady=3)
        entry = ttk.Entry(parent, textvariable=variable)
        entry.grid(row=row, column=1, sticky="ew", pady=3)
        action = ttk.Button(parent, text=button, command=command)
        action.grid(row=row, column=2, padx=(8, 0), pady=3)
        ToolTip(entry, hint)
        ToolTip(action, hint)

    def _choose_source(self) -> None:
        chosen = filedialog.askdirectory(title="选择待整理资料所在文件夹")
        if chosen:
            self.source_var.set(chosen)
            default_rules = Path(chosen) / "分类标准.txt"
            if default_rules.is_file():
                self.rules_var.set(str(default_rules))
            if not self.output_var.get():
                self.output_var.set(str(Path(chosen).parent / "数学资料分类结果"))

    def _choose_rules(self) -> None:
        chosen = filedialog.askopenfilename(title="选择分类标准.txt", filetypes=[("文本文件", "*.txt"), ("所有文件", "*.*")])
        if chosen:
            self.rules_var.set(chosen)

    def _edit_rules(self) -> None:
        rules = Path(self.rules_var.get()).expanduser() if self.rules_var.get().strip() else self._editable_default_rules()
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

    def _read_inputs(self) -> tuple[Path, Path, Path, CategoryRules, int, float]:
        source = Path(self.source_var.get()).expanduser()
        rules_file = Path(self.rules_var.get()).expanduser() if self.rules_var.get().strip() else self._editable_default_rules()
        output = Path(self.output_var.get()).expanduser()
        if not source.is_dir():
            raise ValueError("请选择有效的待整理文件夹。")
        if not self.output_var.get().strip():
            raise ValueError("请选择分类结果保存位置。")
        try:
            cutoff_year = int(self.year_var.get())
            threshold = float(self.threshold_var.get())
        except ValueError as error:
            raise ValueError("历史截止年份和置信度阈值必须是数字。") from error
        if not 1980 <= cutoff_year <= datetime.now().year + 1:
            raise ValueError("历史截止年份不在可用范围内。")
        if not 0 <= threshold <= 1:
            raise ValueError("置信度阈值必须在 0 到 1 之间。")
        return source, output, rules_file, CategoryRules.load(rules_file), cutoff_year, threshold

    def start_scan(self) -> None:
        if self.busy:
            return
        try:
            source, output, rules_file, rules, cutoff_year, threshold = self._read_inputs()
            files = self._documents_under(source, output)
            if not files:
                raise ValueError("这个文件夹内没有 PDF、DOCX 或 PPTX 文件。")
            historical_files = [file for file in files if datetime.fromtimestamp(file.stat().st_mtime).year < cutoff_year]
            if len(historical_files) != len(files) and not self.api_key_var.get().strip():
                raise ValueError("请填写 DeepSeek API Key，或将所有文件设为历史文件。")
        except Exception as error:
            messagebox.showerror(APP_NAME, str(error))
            return
        self._save_settings()
        self.items.clear()
        self._refresh_table()
        self.busy = True
        self.scan_button.configure(state="disabled")
        self.copy_button.configure(state="disabled")
        threading.Thread(target=self._scan_worker, args=(files, rules, cutoff_year, threshold, self.api_key_var.get().strip(), self.api_url_var.get().strip() or DEFAULT_API_URL, self.model_var.get().strip() or DEFAULT_MODEL), daemon=True).start()

    def _documents_under(self, source: Path, output: Path) -> list[Path]:
        result: list[Path] = []
        resolved_output = output.resolve()
        for file in source.rglob("*"):
            # macOS resource forks (._*) and Microsoft Office lock files (~$*) are not real documents.
            if file.name.startswith(("._", "~$")):
                continue
            if not file.is_file() or file.suffix.lower() not in SUPPORTED_EXTENSIONS:
                continue
            try:
                if file.resolve().is_relative_to(resolved_output):
                    continue
            except ValueError:
                pass
            result.append(file)
        return sorted(result, key=lambda item: item.name.lower())

    def _scan_worker(self, files: list[Path], rules: CategoryRules, cutoff_year: int, threshold: float, api_key: str, api_url: str, model: str) -> None:
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
            self.items.append(item)
            self.after(0, self._refresh_table)
        self.after(0, self._scan_finished)

    def _scan_finished(self) -> None:
        self.busy = False
        self.scan_button.configure(state="normal")
        self.copy_button.configure(state="normal" if self.items else "disabled")
        self.status_var.set(f"已完成 {len(self.items)} 个文件的扫描。请复核后再复制。")

    def _refresh_table(self) -> None:
        self.table.delete(*self.table.get_children())
        for index, item in enumerate(self.items):
            details = item.result.reason + (f" 〔{item.note}〕" if item.note else "")
            self.table.insert("", "end", iid=str(index), values=(item.source.name, item.label, f"{item.result.confidence:.0%}", details, "待人工复核"))

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
            rules = CategoryRules.load(self.rules_var.get())
            for primary, children in rules.groups.items():
                options.append(primary)
                options.extend(f"{primary} / {child}" for child in children)
        except ValueError:
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
            dialog.destroy()
            self._refresh_table()

        ttk.Button(dialog, text="保存", command=save).grid(row=2, column=1, padx=15, pady=(4, 15), sticky="e")

    def copy_results(self) -> None:
        if not self.items:
            return
        try:
            _source, output, _rules, _categories, _year, _threshold = self._read_inputs()
            if messagebox.askyesno(APP_NAME, f"将复制 {len(self.items)} 个文件到：\n{output}\n\n原文件不会被移动或删除。是否继续？"):
                output.mkdir(parents=True, exist_ok=True)
                report_rows = []
                for item in self.items:
                    destination_dir = output / self._relative_destination(item)
                    destination_dir.mkdir(parents=True, exist_ok=True)
                    destination = self._non_conflicting_name(destination_dir / item.source.name)
                    shutil.copy2(item.source, destination)
                    report_rows.append([str(item.source), str(destination), item.label, f"{item.result.confidence:.0%}", item.result.reason, item.note])
                self._write_report(output, report_rows)
                self.status_var.set(f"已安全复制 {len(self.items)} 个文件，并生成分类清单。")
                messagebox.showinfo(APP_NAME, "复制完成。原文件保持不变，分类清单已保存到结果文件夹。")
        except Exception as error:
            messagebox.showerror(APP_NAME, f"复制失败：{error}")

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
            writer.writerow(["原文件", "复制后的文件", "分类", "置信度", "分类依据", "提取提示"])
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
