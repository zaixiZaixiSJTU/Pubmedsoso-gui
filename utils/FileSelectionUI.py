# -*- coding: utf-8 -*-
"""文献选择对话框 — 用户勾选需要下载的文献后返回 PMCID 列表。"""
import sqlite3
import tkinter as tk
from tkinter import ttk


def show_file_selection_dialog(parent: tk.Tk, dbpath: str, tablename: str):
    """
    弹出模态对话框，显示数据库中有 PMCID 的文献列表，供用户勾选。
    返回选中的 PMCID 列表；用户关闭窗口返回 None。
    """
    result = {"selected": None}

    dlg = tk.Toplevel(parent)
    dlg.title("选择要下载的文献")
    dlg.geometry("900x520")
    dlg.minsize(700, 400)
    dlg.transient(parent)
    dlg.grab_set()

    # ---------- 读数据库 ----------
    rows = []
    try:
        with sqlite3.connect(dbpath) as conn:
            cur = conn.cursor()
            cur.execute(f"SELECT doctitle, short_author, PMID, PMCID, freemark FROM {tablename}")
            rows = cur.fetchall()
    except Exception as e:
        tk.Label(dlg, text=f"读取数据库失败: {e}").pack(pady=20)
        return None

    pmc_rows = [r for r in rows if r[3]]  # 只显示有 PMCID 的

    if not pmc_rows:
        tk.Label(dlg, text="没有可下载的免费 PMC 文献").pack(pady=20)
        ttk.Button(dlg, text="关闭", command=dlg.destroy).pack(pady=10)
        dlg.wait_window()
        return []

    # ---------- 顶部统计 + 按钮 ----------
    top = ttk.Frame(dlg, padding=8)
    top.pack(fill="x")
    info_var = tk.StringVar(value=f"共 {len(rows)} 篇文献，其中 {len(pmc_rows)} 篇有 PMC 全文可下载")
    ttk.Label(top, textvariable=info_var).pack(side="left")

    # ---------- Treeview ----------
    tree_frame = ttk.Frame(dlg)
    tree_frame.pack(fill="both", expand=True, padx=8, pady=(0, 4))

    cols = ("sel", "title", "author", "pmid", "pmcid")
    tree = ttk.Treeview(tree_frame, columns=cols, show="headings", height=18)
    tree.heading("sel", text="  ")
    tree.heading("title", text="标题")
    tree.heading("author", text="作者")
    tree.heading("pmid", text="PMID")
    tree.heading("pmcid", text="PMCID")
    tree.column("sel", width=36, anchor="center", stretch=False)
    tree.column("title", width=420, anchor="w")
    tree.column("author", width=200, anchor="w")
    tree.column("pmid", width=90, anchor="w", stretch=False)
    tree.column("pmcid", width=110, anchor="w", stretch=False)

    vsb = ttk.Scrollbar(tree_frame, orient="vertical", command=tree.yview)
    tree.configure(yscrollcommand=vsb.set)
    tree.pack(side="left", fill="both", expand=True)
    vsb.pack(side="right", fill="y")

    checked = set()

    for r in pmc_rows:
        title, author, pmid, pmcid, _ = r
        iid = tree.insert("", "end", values=("☑", title[:80], author[:50] if author else "", pmid, pmcid))
        checked.add(iid)

    def toggle(event):
        item = tree.identify_row(event.y)
        if not item:
            return
        vals = list(tree.item(item, "values"))
        if item in checked:
            checked.discard(item)
            vals[0] = "☐"
        else:
            checked.add(item)
            vals[0] = "☑"
        tree.item(item, values=vals)
        update_count()

    def select_all():
        for iid in tree.get_children():
            checked.add(iid)
            vals = list(tree.item(iid, "values"))
            vals[0] = "☑"
            tree.item(iid, values=vals)
        update_count()

    def deselect_all():
        checked.clear()
        for iid in tree.get_children():
            vals = list(tree.item(iid, "values"))
            vals[0] = "☐"
            tree.item(iid, values=vals)
        update_count()

    def update_count():
        info_var.set(f"已选 {len(checked)} / {len(pmc_rows)} 篇")

    tree.bind("<ButtonRelease-1>", toggle)
    update_count()

    # ---------- 底部按钮 ----------
    bot = ttk.Frame(dlg, padding=8)
    bot.pack(fill="x")
    ttk.Button(bot, text="全选", command=select_all).pack(side="left", padx=4)
    ttk.Button(bot, text="取消全选", command=deselect_all).pack(side="left", padx=4)

    def on_confirm():
        selected = []
        for iid in checked:
            vals = tree.item(iid, "values")
            selected.append(vals[4])  # PMCID
        result["selected"] = selected
        dlg.destroy()

    def on_cancel():
        result["selected"] = None
        dlg.destroy()

    ttk.Button(bot, text="取消", command=on_cancel).pack(side="right", padx=4)
    ttk.Button(bot, text="确认下载", command=on_confirm).pack(side="right", padx=4)

    dlg.protocol("WM_DELETE_WINDOW", on_cancel)
    dlg.wait_window()
    return result["selected"]


def show_scihub_selection_dialog(parent: tk.Tk, dbpath: str, tablename: str):
    """
    弹出模态对话框，显示没有 PMCID 但有 DOI 的文献，供用户勾选通过 Sci-Hub 下载。
    返回选中的 [(doi, pmid, doctitle), ...] 列表；取消返回 None。
    """
    result = {"selected": None}

    dlg = tk.Toplevel(parent)
    dlg.title("Sci-Hub 下载 — 选择非免费文献")
    dlg.geometry("900x520")
    dlg.minsize(700, 400)
    dlg.transient(parent)
    dlg.grab_set()

    rows = []
    try:
        with sqlite3.connect(dbpath) as conn:
            cur = conn.cursor()
            cur.execute(f"SELECT doi, PMID, doctitle, short_author FROM {tablename} "
                        f"WHERE (PMCID IS NULL OR PMCID = '') AND doi IS NOT NULL AND doi != ''")
            rows = cur.fetchall()
    except Exception as e:
        tk.Label(dlg, text=f"读取数据库失败: {e}").pack(pady=20)
        dlg.wait_window()
        return None

    if not rows:
        tk.Label(dlg, text="没有可通过 Sci-Hub 下载的文献\n（所有文献要么已有 PMC 全文，要么没有 DOI）").pack(pady=20)
        ttk.Button(dlg, text="关闭", command=dlg.destroy).pack(pady=10)
        dlg.wait_window()
        return []

    top = ttk.Frame(dlg, padding=8)
    top.pack(fill="x")
    info_var = tk.StringVar(value=f"共 {len(rows)} 篇非免费文献有 DOI，可尝试 Sci-Hub 下载")
    ttk.Label(top, textvariable=info_var).pack(side="left")

    tree_frame = ttk.Frame(dlg)
    tree_frame.pack(fill="both", expand=True, padx=8, pady=(0, 4))

    cols = ("sel", "title", "author", "pmid", "doi")
    tree = ttk.Treeview(tree_frame, columns=cols, show="headings", height=18)
    tree.heading("sel", text="  ")
    tree.heading("title", text="标题")
    tree.heading("author", text="作者")
    tree.heading("pmid", text="PMID")
    tree.heading("doi", text="DOI")
    tree.column("sel", width=36, anchor="center", stretch=False)
    tree.column("title", width=380, anchor="w")
    tree.column("author", width=160, anchor="w")
    tree.column("pmid", width=90, anchor="w", stretch=False)
    tree.column("doi", width=190, anchor="w")

    vsb = ttk.Scrollbar(tree_frame, orient="vertical", command=tree.yview)
    tree.configure(yscrollcommand=vsb.set)
    tree.pack(side="left", fill="both", expand=True)
    vsb.pack(side="right", fill="y")

    checked = set()

    for doi, pmid, doctitle, author in rows:
        iid = tree.insert("", "end", values=(
            "☑", (doctitle or "")[:80], (author or "")[:50], pmid or "", doi or ""))
        checked.add(iid)

    def toggle(event):
        item = tree.identify_row(event.y)
        if not item:
            return
        vals = list(tree.item(item, "values"))
        if item in checked:
            checked.discard(item)
            vals[0] = "☐"
        else:
            checked.add(item)
            vals[0] = "☑"
        tree.item(item, values=vals)
        update_count()

    def select_all():
        for iid in tree.get_children():
            checked.add(iid)
            vals = list(tree.item(iid, "values"))
            vals[0] = "☑"
            tree.item(iid, values=vals)
        update_count()

    def deselect_all():
        checked.clear()
        for iid in tree.get_children():
            vals = list(tree.item(iid, "values"))
            vals[0] = "☐"
            tree.item(iid, values=vals)
        update_count()

    def update_count():
        info_var.set(f"已选 {len(checked)} / {len(rows)} 篇")

    tree.bind("<ButtonRelease-1>", toggle)
    update_count()

    bot = ttk.Frame(dlg, padding=8)
    bot.pack(fill="x")
    ttk.Button(bot, text="全选", command=select_all).pack(side="left", padx=4)
    ttk.Button(bot, text="取消全选", command=deselect_all).pack(side="left", padx=4)

    def on_confirm():
        selected = []
        for iid in checked:
            vals = tree.item(iid, "values")
            # (doi, pmid, doctitle)
            selected.append((vals[4], vals[3], vals[1]))
        result["selected"] = selected
        dlg.destroy()

    def on_cancel():
        result["selected"] = None
        dlg.destroy()

    ttk.Button(bot, text="取消", command=on_cancel).pack(side="right", padx=4)
    ttk.Button(bot, text="确认下载", command=on_confirm).pack(side="right", padx=4)

    dlg.protocol("WM_DELETE_WINDOW", on_cancel)
    dlg.wait_window()
    return result["selected"]
