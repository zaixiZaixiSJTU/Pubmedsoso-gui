# -*- coding: utf-8 -*-
import asyncio
import logging
import os
import platform
import queue
import re
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

from config import ProjectInfo, projConfig
from GetSearchResult import spiderpub
from GetEachInfo import geteachinfo
from utils.ExcelHelper import ExcelHelper
from utils.LogHelper import medLog, MedLogger
from utils.PDFHelper import PDFHelper
from utils.WebHelper import WebHelper
from utils.FileSelectionUI import show_file_selection_dialog, show_scihub_selection_dialog
from clean import clean_files, clean_sqlite


class QueueHandler(logging.Handler):
    def __init__(self, log_queue: queue.Queue):
        super().__init__()
        self.log_queue = log_queue

    def emit(self, record):
        try:
            msg = self.format(record)
            self.log_queue.put((record.levelno, msg))
        except Exception:
            self.handleError(record)


class PubmedsosoApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title(f"{ProjectInfo.ProjectName} v{ProjectInfo.VersionInfo} - PubMed 文献检索工具")
        self.root.geometry("820x620")
        self.root.minsize(700, 500)

        icon_path = os.path.join(os.path.dirname(__file__), "assets", "icon.png")
        if os.path.exists(icon_path):
            self.root.iconphoto(False, tk.PhotoImage(file=icon_path))

        self.keyword_var = tk.StringVar()
        self.pagenum_var = tk.IntVar(value=10)
        self.year_var = tk.StringVar(value="不限")
        self.downloadnum_var = tk.IntVar(value=10)
        self.directory_var = tk.StringVar(value="./document")
        self.loglevel_var = tk.StringVar(value="info")
        self.scihub_var = tk.StringVar(value=projConfig.scihubDomain)
        self.status_var = tk.StringVar(value="空闲")
        self.step_var = tk.StringVar(value="")
        self.progress_var = tk.IntVar(value=0)

        self._cancel_event = threading.Event()
        self._worker = None
        self._pipeline_done = False

        self.log_queue = queue.Queue()
        self._setup_log_handler()
        self._build_ui()
        self._poll_log_queue()

    # ============================== UI ==============================

    def _build_ui(self):
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(2, weight=1)
        self._build_param_frame()
        self._build_control_frame()
        self._build_log_frame()

    def _build_param_frame(self):
        frame = ttk.LabelFrame(self.root, text="搜索参数", padding=10)
        frame.grid(row=0, column=0, sticky="ew", padx=10, pady=(10, 5))
        frame.columnconfigure(1, weight=1)
        frame.columnconfigure(4, weight=1)

        ttk.Label(frame, text="关键词:").grid(row=0, column=0, sticky="w", padx=(0, 5))
        kw_entry = ttk.Entry(frame, textvariable=self.keyword_var)
        kw_entry.grid(row=0, column=1, columnspan=5, sticky="ew", pady=3)
        self._kw_entry = kw_entry

        ttk.Label(frame, text="检索页数:").grid(row=1, column=0, sticky="w", padx=(0, 5))
        ttk.Spinbox(frame, from_=1, to=200, textvariable=self.pagenum_var, width=8).grid(row=1, column=1, sticky="w", pady=3)
        ttk.Label(frame, text="年份范围:").grid(row=1, column=2, sticky="w", padx=(20, 5))
        ttk.Combobox(frame, textvariable=self.year_var, values=["不限", "1", "5", "10"], state="readonly", width=8).grid(row=1, column=3, sticky="w", pady=3)

        ttk.Label(frame, text="保存目录:").grid(row=2, column=0, sticky="w", padx=(0, 5))
        ttk.Entry(frame, textvariable=self.directory_var).grid(row=2, column=1, columnspan=4, sticky="ew", pady=3)
        ttk.Button(frame, text="浏览", width=6, command=self._browse_dir).grid(row=2, column=5, padx=(5, 0), pady=3)

        ttk.Label(frame, text="日志级别:").grid(row=3, column=0, sticky="w", padx=(0, 5))
        ttk.Combobox(frame, textvariable=self.loglevel_var, values=["debug", "info", "warning", "error", "critical"], state="readonly", width=8).grid(row=3, column=1, sticky="w", pady=3)

        ttk.Label(frame, text="Sci-Hub:").grid(row=3, column=2, sticky="w", padx=(20, 5))
        ttk.Entry(frame, textvariable=self.scihub_var, width=18).grid(row=3, column=3, sticky="w", pady=3)

        self._input_widgets = [w for w in frame.winfo_children() if isinstance(w, (ttk.Entry, ttk.Spinbox, ttk.Combobox, ttk.Button))]

    def _build_control_frame(self):
        frame = ttk.Frame(self.root, padding=(10, 5))
        frame.grid(row=1, column=0, sticky="ew", padx=10)
        frame.columnconfigure(5, weight=1)

        self.start_btn = ttk.Button(frame, text="开始检索", command=self._on_start)
        self.start_btn.grid(row=0, column=0, padx=(0, 5))
        self.stop_btn = ttk.Button(frame, text="停止", command=self._on_stop, state="disabled")
        self.stop_btn.grid(row=0, column=1, padx=5)
        self.clean_btn = ttk.Button(frame, text="清理历史", command=self._on_clean)
        self.clean_btn.grid(row=0, column=2, padx=5)
        self.scihub_btn = ttk.Button(frame, text="Sci-Hub 下载", command=self._on_scihub, state="disabled")
        self.scihub_btn.grid(row=0, column=3, padx=5)
        ttk.Label(frame, text="状态:").grid(row=0, column=4, padx=(15, 5))
        ttk.Label(frame, textvariable=self.status_var, width=12).grid(row=0, column=5, sticky="w")

        self.progress_bar = ttk.Progressbar(frame, variable=self.progress_var, maximum=100, length=300)
        self.progress_bar.grid(row=1, column=0, columnspan=5, sticky="ew", pady=(8, 0))
        ttk.Label(frame, textvariable=self.step_var).grid(row=1, column=5, sticky="w", padx=5, pady=(8, 0))

    def _build_log_frame(self):
        frame = ttk.LabelFrame(self.root, text="日志输出", padding=5)
        frame.grid(row=2, column=0, sticky="nsew", padx=10, pady=(5, 10))
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)

        self.log_text = tk.Text(frame, state="disabled", wrap="word", font=("Microsoft YaHei", 9), height=12)
        scrollbar = ttk.Scrollbar(frame, orient="vertical", command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=scrollbar.set)
        self.log_text.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")

        self.log_text.tag_configure("DEBUG", foreground="gray")
        self.log_text.tag_configure("INFO", foreground="green")
        self.log_text.tag_configure("WARNING", foreground="orange")
        self.log_text.tag_configure("ERROR", foreground="red")
        self.log_text.tag_configure("CRITICAL", foreground="red", underline=True)

        ttk.Button(frame, text="清空日志", command=self._clear_log).grid(row=1, column=0, columnspan=2, sticky="e", pady=(5, 0))

    # ============================== 日志 ==============================

    def _setup_log_handler(self):
        handler = QueueHandler(self.log_queue)
        handler.setLevel(logging.DEBUG)
        handler.setFormatter(logging.Formatter("%(asctime)s-%(levelname)s- %(message)s", datefmt="%H:%M:%S"))
        medLog.addHandler(handler)

    def _poll_log_queue(self):
        while True:
            try:
                levelno, msg = self.log_queue.get_nowait()
            except queue.Empty:
                break
            tag = logging.getLevelName(levelno)
            self.log_text.configure(state="normal")
            self.log_text.insert(tk.END, msg + "\n", tag)
            self.log_text.see(tk.END)
            self.log_text.configure(state="disabled")
        self.root.after(100, self._poll_log_queue)

    def _clear_log(self):
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", tk.END)
        self.log_text.configure(state="disabled")

    # ============================== 控件状态 ==============================

    def _set_running(self, running: bool):
        s = "disabled" if running else "normal"
        self.start_btn.configure(state=s)
        self.clean_btn.configure(state=s)
        self.scihub_btn.configure(state=s)
        self.stop_btn.configure(state="normal" if running else "disabled")
        for w in self._input_widgets:
            w.configure(state=s)

    # ============================== 事件 ==============================

    def _browse_dir(self):
        d = filedialog.askdirectory(initialdir=self.directory_var.get())
        if d:
            self.directory_var.set(d)

    def _on_start(self):
        keyword = self.keyword_var.get().strip()
        if not keyword:
            messagebox.showwarning("提示", "请输入检索关键词")
            self._kw_entry.focus_set()
            return
        if keyword.isnumeric():
            messagebox.showwarning("提示", "关键词不能为纯数字")
            return

        self._cancel_event.clear()
        self._set_running(True)
        self.status_var.set("运行中")
        self.progress_var.set(0)
        self.step_var.set("")

        level_map = {"debug": logging.DEBUG, "info": logging.INFO, "warning": logging.WARNING, "error": logging.ERROR, "critical": logging.CRITICAL}
        MedLogger.setTerminalLogLevel(medLog, level_map.get(self.loglevel_var.get(), logging.INFO))

        self._worker = threading.Thread(target=self._run_pipeline, daemon=True)
        self._worker.start()

    def _on_stop(self):
        self._cancel_event.set()
        self.status_var.set("正在停止…")
        medLog.warning("用户请求停止，将在当前步骤完成后中断")

    def _on_clean(self):
        if not messagebox.askyesno("确认", "将删除所有历史 Excel、txt 文件并清空数据库，确定？"):
            return
        self._set_running(True)
        def do():
            try:
                clean_files(os.getcwd())
                clean_sqlite("./pubmedsql")
                medLog.info("清理完成")
            except Exception as e:
                medLog.error(f"清理出错: {e}")
            finally:
                self.root.after(0, lambda: self._set_running(False))
        threading.Thread(target=do, daemon=True).start()

    # ============================== 流水线 ==============================

    def _run_pipeline(self):
        try:
            if platform.system() == "Windows":
                asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

            keyword = self.keyword_var.get().strip()
            pagenum = self.pagenum_var.get()
            year_str = self.year_var.get()
            year = int(year_str) if year_str != "不限" else None
            directory = self.directory_var.get().strip()

            projConfig.savetime = time.strftime("%Y%m%d%H%M%S")
            safe_keyword = re.sub(r'[<>:"/\\|?*]', '_', keyword)
            output_dir = os.path.join(directory, safe_keyword)
            projConfig.pdfSavePath = output_dir
            ExcelHelper.savepath = os.path.join(output_dir, f'pubmed-{projConfig.savetime}.xlsx')
            ExcelHelper.tablename = f'pubmed{projConfig.savetime}'
            dbpath = "./pubmedsql"
            os.makedirs(output_dir, exist_ok=True)

            # ---- 步骤 1/4: 搜索 ----
            self._update_step(1, "搜索 PubMed…")
            medLog.info(f'关键词: "{keyword}", 页数: {pagenum}, 年份: {year_str}, 目录: {output_dir}')

            result_num = WebHelper.GetSearchResultNum(keyword=keyword, year=year)
            if not result_num:
                medLog.warning("未检索到结果，请检查关键词或网络")
                self._finish("完成 (无结果)")
                return
            medLog.info(f"检索到 {result_num} 条结果")

            spiderpub(keyword, year, pagenum, result_num)
            self.progress_var.set(25)
            if self._cancelled():
                return

            # ---- 步骤 2/4: 获取详情 ----
            self._update_step(2, "获取文献详情…")
            excel_path = ExcelHelper.savepath
            if os.path.exists(excel_path):
                os.remove(excel_path)
            geteachinfo(dbpath)
            self.progress_var.set(50)
            if self._cancelled():
                return

            # ---- 步骤 3/4: 用户选择 + 下载 PDF ----
            self._update_step(3, "等待选择文献…")
            selected = self._show_file_selection()

            if selected is None:
                medLog.warning("用户取消了选择")
                self._finish("已取消")
                return
            if not selected:
                medLog.info("未选择任何文献，跳过下载")
            else:
                medLog.info(f"选择了 {len(selected)} 篇，开始下载…")
                self._update_step(3, "下载 PDF…")
                PDFHelper.PDFBatchDownloadWithSelection(selected)
            self.progress_var.set(75)
            if self._cancelled():
                return

            # ---- 步骤 4/4: 导出 Excel ----
            self._update_step(4, "导出 Excel…")
            if os.path.exists(excel_path):
                os.remove(excel_path)
            ExcelHelper.PD_To_excel(dbpath, override=True)
            self.progress_var.set(100)

            medLog.info(f"全部完成！Excel: {ExcelHelper.savepath}")
            medLog.info(f"PDF 目录: {projConfig.pdfSavePath}")
            self._pipeline_done = True
            self._finish("完成")

        except SystemExit:
            medLog.warning("流水线被中断")
            self._finish("已中断")
        except Exception as e:
            medLog.error(f"执行出错: {e}")
            self._finish("出错")

    # ============================== 辅助 ==============================

    def _update_step(self, step, text):
        self.root.after(0, lambda: self.step_var.set(f"步骤 {step}/4 - {text}"))

    def _cancelled(self):
        if self._cancel_event.is_set():
            medLog.warning("已取消")
            self._finish("已取消")
            return True
        return False

    def _show_file_selection(self):
        """在主线程弹出选择对话框，后台线程轮询等待结果。"""
        result_q = queue.Queue()
        def show():
            sel = show_file_selection_dialog(self.root, "./pubmedsql", f'pubmed{projConfig.savetime}')
            result_q.put(sel)
        self.root.after(0, show)
        while True:
            try:
                return result_q.get(timeout=0.1)
            except queue.Empty:
                if self._cancel_event.is_set():
                    return None

    # ============================== Sci-Hub ==============================

    def _on_scihub(self):
        """点击 Sci-Hub 下载按钮。"""
        if not hasattr(self, '_pipeline_done') or not self._pipeline_done:
            messagebox.showinfo("提示", "请先完成一次检索流程")
            return

        projConfig.scihubDomain = self.scihub_var.get().strip()
        self._set_running(True)
        self.status_var.set("Sci-Hub…")

        self._worker = threading.Thread(target=self._run_scihub, daemon=True)
        self._worker.start()

    def _run_scihub(self):
        try:
            # 在主线程弹出选择对话框
            result_q = queue.Queue()
            def show():
                sel = show_scihub_selection_dialog(
                    self.root, "./pubmedsql", f'pubmed{projConfig.savetime}')
                result_q.put(sel)
            self.root.after(0, show)

            selected = None
            while True:
                try:
                    selected = result_q.get(timeout=0.1)
                    break
                except queue.Empty:
                    if self._cancel_event.is_set():
                        self._finish("已取消")
                        return

            if selected is None:
                medLog.info("取消 Sci-Hub 下载")
                self._finish("完成")
                return
            if not selected:
                medLog.info("没有可下载的文献")
                self._finish("完成")
                return

            medLog.info(f"Sci-Hub: 选择了 {len(selected)} 篇文献")
            PDFHelper.SciHubBatchDownload(selected)
            self._finish("完成")
        except Exception as e:
            medLog.error(f"Sci-Hub 出错: {e}")
            self._finish("出错")

    def _finish(self, status):
        self.root.after(0, lambda: self._on_done(status))

    def _on_done(self, status):
        self.status_var.set(status)
        self.step_var.set("")
        self._set_running(False)


if __name__ == "__main__":
    root = tk.Tk()
    PubmedsosoApp(root)
    root.mainloop()
