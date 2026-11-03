# -*- coding: utf-8 -*-
"""PathMover 界面层：只做「收集参数 + 展示结果」，所有系统操作在 engine.py。"""

from __future__ import annotations

import os
import queue
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import engine as E

# --------------------------------------------------------------------------- #
# 配色与字体（浅色主题）
# --------------------------------------------------------------------------- #
C = {
    "bg": "#eef1f6",
    "card": "#ffffff",
    "line": "#dbe1ea",
    "text": "#1f2430",
    "sub": "#6b7280",
    "accent": "#2563eb",
    "accent_bg": "#eaf1fe",
    "ok": "#15803d",
    "warn": "#b45309",
    "err": "#b91c1c",
    "warnbg": "#fff7ed",
    "errbg": "#fef2f2",
    "okbg": "#f0fdf4",
}
FONT = ("Microsoft YaHei UI", 9)
FONT_B = ("Microsoft YaHei UI", 9, "bold")
FONT_S = ("Microsoft YaHei UI", 8)
FONT_T = ("Microsoft YaHei UI", 12, "bold")
FONT_MONO = ("Consolas", 9)

RISK = {
    "shellfolder": ("安全", C["ok"]),
    "junction": ("安全", C["ok"]),
    "tempdir": ("谨慎", C["warn"]),
    "programfiles": ("高危", C["err"]),
    "appdata_root": ("高危", C["err"]),
}


def short_path(p: str, n: int = 46) -> str:
    if len(p) <= n:
        return p
    return p[:18] + " … " + p[-(n - 21):]


# --------------------------------------------------------------------------- #
# 滚动容器
# --------------------------------------------------------------------------- #
class ScrollFrame(tk.Frame):
    def __init__(self, master, bg=C["bg"]):
        super().__init__(master, bg=bg)
        self.canvas = tk.Canvas(self, bg=bg, highlightthickness=0)
        self.vs = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.vs.set)
        self.vs.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.body = tk.Frame(self.canvas, bg=bg)
        self._win = self.canvas.create_window((0, 0), window=self.body, anchor="nw")
        self.body.bind("<Configure>", self._on_body)
        self.canvas.bind("<Configure>", self._on_canvas)
        self.bind("<Enter>", lambda e: self.canvas.bind_all("<MouseWheel>", self._wheel))
        self.bind("<Leave>", lambda e: self.canvas.unbind_all("<MouseWheel>"))

    def _on_body(self, _e=None):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas(self, e):
        self.canvas.itemconfigure(self._win, width=e.width)

    def _wheel(self, e):
        self.canvas.yview_scroll(int(-e.delta / 120), "units")


# --------------------------------------------------------------------------- #
# 一行可编辑的目标
# --------------------------------------------------------------------------- #
class Row:
    def __init__(self, parent, app, label, current, kind, note="", remove_cb=None):
        self.app = app
        self.kind = kind
        self.label = label
        self.current = current
        self.on = tk.BooleanVar(value=False)
        self.dst = tk.StringVar()

        risk, color = RISK.get(kind, ("安全", C["ok"]))
        self.frame = tk.Frame(parent, bg=C["card"], highlightthickness=1,
                              highlightbackground=C["line"])
        self.frame.pack(fill="x", padx=12, pady=3)
        self.frame.columnconfigure(2, weight=3)
        self.frame.columnconfigure(4, weight=3)

        tk.Frame(self.frame, bg=color, width=3).grid(row=0, column=0, sticky="ns", padx=(0, 8))

        self.cb = tk.Checkbutton(
            self.frame, variable=self.on, bg=C["card"], activebackground=C["card"],
            highlightthickness=0, bd=0, command=self._on_toggle, cursor="hand2",
        )
        self.cb.grid(row=0, column=1, padx=(0, 2), pady=6)

        self.name_lbl = tk.Label(self.frame, text=label, bg=C["card"], fg=C["text"],
                                 font=FONT_B, width=13, anchor="w")
        self.name_lbl.grid(row=0, column=2, sticky="w")

        self.cur_lbl = tk.Label(self.frame, text=short_path(current), bg=C["card"],
                                fg=C["sub"], font=FONT_MONO, anchor="w")
        self.cur_lbl.grid(row=0, column=3, sticky="w", padx=(0, 6))
        if E.is_reparse_point(current):
            self.cur_lbl.configure(fg=C["accent"])

        tk.Label(self.frame, text="→", bg=C["card"], fg=C["sub"], font=FONT).grid(
            row=0, column=4, sticky="")

        self.entry = tk.Entry(
            self.frame, textvariable=self.dst, font=FONT_MONO, bg="#f8fafc", fg=C["text"],
            relief="flat", highlightthickness=1, highlightbackground=C["line"],
            highlightcolor=C["accent"], insertbackground=C["text"],
        )
        self.entry.grid(row=0, column=5, sticky="ew", padx=(0, 6), pady=6, ipady=3)

        tk.Button(self.frame, text="浏览", font=FONT_S, bg="#f1f5f9", fg=C["text"],
                  relief="flat", padx=8, cursor="hand2",
                  command=self._browse).grid(row=0, column=6, padx=(0, 6))

        self.status = tk.Label(self.frame, text="", bg=C["card"], fg=color, font=FONT_S,
                               width=9, anchor="w")
        self.status.grid(row=0, column=7, padx=(0, 8))

        if remove_cb:
            tk.Button(self.frame, text="✕", font=FONT_S, bg=C["card"], fg=C["sub"],
                      relief="flat", cursor="hand2", command=lambda: remove_cb(self)).grid(
                row=0, column=8, padx=(0, 4))
        if note:
            self.name_lbl.configure(text=label)

    # -- 交互 ------------------------------------------------------------- #
    def _browse(self):
        init = os.path.dirname(self.dst.get()) if self.dst.get() else os.path.splitdrive(self.current)[0] + "\\"
        p = filedialog.askdirectory(parent=self.app.root, title=f"选择「{self.label}」的新位置",
                                    initialdir=init if os.path.isdir(init) else None)
        if p:
            self.dst.set(os.path.normpath(p))
            self.on.set(True)
            self._on_toggle()

    def _on_toggle(self):
        if self.on.get() and not self.dst.get():
            self.dst.set(self._suggest())
        on = self.on.get()
        self.frame.configure(highlightbackground=C["accent"] if on else C["line"])
        self.entry.configure(bg="#ffffff" if on else "#f8fafc")
        self.set_status("", C["sub"])

    def _suggest(self) -> str:
        """给个合理的默认建议：同一个盘符下同名的目录结构。"""
        drive = self.app.best_drive()
        if self.kind == "shellfolder":
            return os.path.join(drive, "Users", os.environ.get("USERNAME", "user"), self.label)
        if self.kind == "tempdir":
            return os.path.join(drive, "Temp")
        base = os.path.basename(self.current.rstrip("\\")) or "cache"
        return os.path.join(drive, "AppDataMove", base)

    def set_status(self, text, color=None):
        self.status.configure(text=text, fg=color or C["sub"])

    def set_size_hint(self, text):
        if not self.on.get() or not self.dst.get():
            pass

    @property
    def selected(self) -> bool:
        return self.on.get() and bool(self.dst.get().strip())


# --------------------------------------------------------------------------- #
# 主程序
# --------------------------------------------------------------------------- #
class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.q: queue.Queue = queue.Queue()
        self.rows: list[Row] = []
        self.busy = False
        self._build_style()
        self._build_header()
        self._build_tabs()
        self._build_log()
        self._build_footer()
        self.root.after(120, self._drain)
        self.root.after(400, self._compute_sizes)

    # -- 外观 ------------------------------------------------------------- #
    def _build_style(self):
        self.root.title(f"PathMover · 默认存储位置管理  v{E.APP_VERSION}")
        self.root.geometry("1120x780")
        self.root.minsize(980, 660)
        self.root.configure(bg=C["bg"])
        st = ttk.Style()
        try:
            st.theme_use("vista")
        except tk.TclError:
            st.theme_use("clam")
        st.configure("TNotebook", background=C["bg"], borderwidth=0)
        st.configure("TNotebook.Tab", padding=(16, 7), font=FONT)
        st.configure("TScrollbar", background=C["bg"])

    def _build_header(self):
        bar = tk.Frame(self.root, bg=C["card"], highlightthickness=1,
                       highlightbackground=C["line"])
        bar.pack(fill="x", side="top")
        left = tk.Frame(bar, bg=C["card"])
        left.pack(side="left", padx=18, pady=12)
        tk.Label(left, text="PathMover", bg=C["card"], fg=C["text"], font=FONT_T).pack(anchor="w")
        tk.Label(left, text="按需把桌面 / 下载 / AppData 缓存 / 安装目录搬到你指定的磁盘",
                 bg=C["card"], fg=C["sub"], font=FONT_S).pack(anchor="w")

        right = tk.Frame(bar, bg=C["card"])
        right.pack(side="right", padx=18)
        admin = E.is_admin()
        tk.Label(right, text="管理员模式" if admin else "普通用户",
                 bg=C["okbg"] if admin else "#f3f4f6",
                 fg=C["ok"] if admin else C["sub"], font=FONT_S, padx=8, pady=2).pack(side="right", padx=4)
        tk.Button(right, text="生效范围说明", font=FONT_S, bg="#f1f5f9", fg=C["text"],
                  relief="flat", padx=10, pady=3, cursor="hand2",
                  command=self._show_effects).pack(side="right", padx=4)
        if not admin:
            tk.Button(right, text="以管理员重启", font=FONT_S, bg=C["accent"], fg="white",
                      relief="flat", padx=10, pady=3, cursor="hand2",
                      command=self._relaunch_admin).pack(side="right", padx=4)

    def _build_tabs(self):
        self.nb = ttk.Notebook(self.root)
        self.nb.pack(fill="both", expand=True, padx=12, pady=(10, 0))
        self._tab_shell()
        self._tab_cache()
        self._tab_program()
        self._tab_appdata()
        self._tab_backup()

    def _info_bar(self, parent, text, bg=None, fg=None):
        f = tk.Frame(parent, bg=bg or C["accent_bg"])
        f.pack(fill="x", padx=12, pady=(10, 4))
        tk.Label(f, text=text, bg=bg or C["accent_bg"], fg=fg or C["accent"],
                 font=FONT_S, justify="left", anchor="w", padx=10, pady=6).pack(fill="x")
        return f

    # -- Tab 1：用户文件夹 ------------------------------------------------ #
    def _tab_shell(self):
        tab = tk.Frame(self.nb, bg=C["bg"])
        self.nb.add(tab, text="  用户文件夹  ")
        self._info_bar(tab, "生效：立即生效且永久有效。改完后浏览器下载、截图、"
                            "所有「另存为」默认目录都会直接落到新位置。")
        sf = ScrollFrame(tab)
        sf.pack(fill="both", expand=True)
        for kf in E.KNOWN_FOLDERS:
            self.rows.append(Row(sf.body, self, kf.label, kf.current(), "shellfolder",
                                 note=kf.key))
            setattr(self.rows[-1], "kf", kf)
        self.move_shell = tk.BooleanVar(value=True)
        tk.Checkbutton(
            sf.body, text="同时把现有文件搬过去（跨盘移动，耗时取决于文件量；取消勾选则只改指向）",
            variable=self.move_shell, bg=C["bg"], fg=C["sub"], font=FONT_S,
            activebackground=C["bg"], anchor="w",
        ).pack(fill="x", padx=16, pady=(8, 12))

    # -- Tab 2：AppData 与缓存 -------------------------------------------- #
    def _tab_cache(self):
        tab = tk.Frame(self.nb, bg=C["bg"])
        self.nb.add(tab, text="  AppData 与缓存  ")
        self._info_bar(tab, "生效：程序完全无感。在原位置留一个目录联接指向新盘，"
                            "以后新增的缓存、更新、新版本数据都会继续写进新位置。")
        sf = ScrollFrame(tab)
        sf.pack(fill="both", expand=True)
        self.cache_body = sf.body

        tk.Label(sf.body, text="检测到的可迁移目录", bg=C["bg"], fg=C["text"],
                 font=FONT_B).pack(anchor="w", padx=16, pady=(8, 4))
        found = E.cache_candidates()
        if not found:
            tk.Label(sf.body, text="（没检测到常见缓存目录，可以手动添加）",
                     bg=C["bg"], fg=C["sub"], font=FONT_S).pack(anchor="w", padx=16)
        for label, path in found:
            r = Row(sf.body, self, label, path, "junction")
            self.rows.append(r)

        tk.Label(sf.body, text="临时目录", bg=C["bg"], fg=C["text"],
                 font=FONT_B).pack(anchor="w", padx=16, pady=(14, 4))
        temp_row = Row(sf.body, self, "TEMP / TMP", E.get_temp_dir(), "tempdir")
        self.rows.append(temp_row)

        btns = tk.Frame(sf.body, bg=C["bg"])
        btns.pack(fill="x", padx=16, pady=12)
        tk.Button(btns, text="＋ 添加其他目录", font=FONT_S, bg="#f1f5f9", fg=C["text"],
                  relief="flat", padx=10, pady=4, cursor="hand2",
                  command=self._add_custom).pack(side="left")
        tk.Label(btns, text="适合搬：浏览器配置、开发缓存、聊天记录、游戏库",
                 bg=C["bg"], fg=C["sub"], font=FONT_S).pack(side="left", padx=12)

    def _add_custom(self):
        p = filedialog.askdirectory(parent=self.root, title="选择要搬走的目录",
                                    initialdir=os.environ.get("LOCALAPPDATA"))
        if not p:
            return
        p = os.path.normpath(p)
        low = p.lower()
        if not any(low.startswith(k.lower()) for k in
                   (os.environ.get("LOCALAPPDATA", "x"), os.environ.get("APPDATA", "x"),
                    os.environ.get("USERPROFILE", "x"), "c:\\program files")):
            if not messagebox.askyesno(
                "确认目录", "这个目录不在 AppData / 用户目录 / Program Files 下。\n"
                           "搬移后原位置会变成目录联接，确认继续？"):
                return
        r = Row(self.cache_body, self, os.path.basename(p) or p, p, "junction",
                remove_cb=self._remove_custom)
        self.rows.append(r)
        r.on.set(True)
        r._on_toggle()

    def _remove_custom(self, row: Row):
        row.frame.destroy()
        if row in self.rows:
            self.rows.remove(row)

    # -- Tab 3：安装目录 -------------------------------------------------- #
    def _tab_program(self):
        tab = tk.Frame(self.nb, bg=C["bg"])
        self.nb.add(tab, text="  程序安装目录  ")
        self._info_bar(
            tab,
            "只影响之后的新安装，已装程序一个都不会移动。风险最高：部分驱动 / 运行库 / "
            "Windows 更新组件假定程序在 C 盘，建议装完需要的软件再改回默认。",
            bg=C["warnbg"], fg=C["warn"],
        )
        card = tk.Frame(tab, bg=C["card"], highlightthickness=1, highlightbackground=C["line"])
        card.pack(fill="x", padx=12, pady=8)
        cur = E.get_program_dirs()
        self.pf_vars = {}
        rows = [
            ("64 位程序", "ProgramFilesDir", "ProgramFilesDir"),
            ("32 位程序", "ProgramFilesDir (x86)", "ProgramFilesDir (x86)"),
        ]
        for i, (label, key, regname) in enumerate(rows):
            var = tk.StringVar(value="")
            self.pf_vars[key] = var
            tk.Label(card, text=label, bg=C["card"], fg=C["text"], font=FONT_B,
                     width=10, anchor="w").grid(row=i, column=0, padx=(14, 6), pady=8, sticky="w")
            tk.Label(card, text=short_path(cur.get(regname) or "—", 40), bg=C["card"],
                     fg=C["sub"], font=FONT_MONO).grid(row=i, column=1, padx=(0, 10), sticky="w")
            tk.Label(card, text="→", bg=C["card"], fg=C["sub"]).grid(row=i, column=2)
            e = tk.Entry(card, textvariable=var, font=FONT_MONO, bg="#f8fafc", relief="flat",
                         highlightthickness=1, highlightbackground=C["line"],
                         highlightcolor=C["accent"], width=44)
            e.grid(row=i, column=3, padx=8, pady=8, ipady=3, sticky="we")
            tk.Button(card, text="浏览", font=FONT_S, bg="#f1f5f9", fg=C["text"], relief="flat",
                      padx=8, cursor="hand2",
                      command=lambda v=var: self._pick_dir(v)).grid(row=i, column=4, padx=(0, 14))
        card.columnconfigure(3, weight=1)

        foot = tk.Frame(tab, bg=C["bg"])
        foot.pack(fill="x", padx=12, pady=6)
        tk.Button(foot, text="应用安装目录设置", font=FONT, bg=C["accent"], fg="white",
                  relief="flat", padx=16, pady=7, cursor="hand2",
                  command=self._apply_program_dirs).pack(side="left")
        tk.Label(foot, text="需要管理员权限；写入前会自动备份注册表", bg=C["bg"],
                 fg=C["sub"], font=FONT_S).pack(side="left", padx=12)

        tips = ("补充说明\n\n"
                "· %ProgramFiles% 环境变量要重新登录才会更新，但安装器读的是注册表，通常马上生效。\n"
                "· 已安装程序记录的是绝对路径，不会因为改这里而失效。\n"
                "· 想彻底避免 C 盘被占：更推荐安装时手动把路径改到 D 盘，本页只作兜底。")
        tk.Label(tab, text=tips, bg=C["bg"], fg=C["sub"], font=FONT_S,
                 justify="left", anchor="nw").pack(fill="both", expand=True, padx=20, pady=8)

    def _pick_dir(self, var: tk.StringVar):
        p = filedialog.askdirectory(parent=self.root)
        if p:
            var.set(os.path.normpath(p))

    def _apply_program_dirs(self):
        if not E.is_admin():
            if messagebox.askyesno("需要管理员权限", "修改程序安装目录需要管理员权限。\n现在以管理员身份重启？"):
                self._relaunch_admin()
            return
        x64 = self.pf_vars["ProgramFilesDir"].get().strip()
        x86 = self.pf_vars["ProgramFilesDir (x86)"].get().strip()
        if not x64 and not x86:
            messagebox.showinfo("提示", "两个路径都为空，没有需要修改的内容。")
            return
        cur = E.get_program_dirs()
        x64 = x64 or cur["ProgramFilesDir"]
        x86 = x86 or cur["ProgramFilesDir (x86)"]
        if not messagebox.askyesno(
            "确认修改安装目录",
            f"64 位：{cur['ProgramFilesDir']}\n        →  {x64}\n\n"
            f"32 位：{cur['ProgramFilesDir (x86)']}\n        →  {x86}\n\n"
            "只影响新安装的程序。确定写入？"):
            return
        try:
            snap = E.save_snapshot([], note="修改程序安装目录")
            self.log(f"已备份注册表：{snap}")
            E.apply_program_dirs(x64, x86, self.log)
            messagebox.showinfo("完成", "已写入。新安装的程序会使用新目录。\n如需还原，到「备份与还原」页。")
        except Exception as e:
            self.log(f"✕ {e}")
            messagebox.showerror("失败", str(e))

    # -- Tab 4：AppData 整体 ---------------------------------------------- #
    def _tab_appdata(self):
        tab = tk.Frame(self.nb, bg=C["bg"])
        self.nb.add(tab, text="  AppData 整体  ")
        self._info_bar(
            tab,
            "不建议直接改。AppData 有大量句柄常驻，在线整体搬迁必然失败；只改环境变量又是"
            "「半生效」——Windows 自身仍按注册表走原路径，结果会分裂成两处。",
            bg=C["warnbg"], fg=C["warn"],
        )
        local, roaming = E.appdata_current()
        card = tk.Frame(tab, bg=C["card"], highlightthickness=1, highlightbackground=C["line"])
        card.pack(fill="x", padx=12, pady=8)
        self.ap_vars = {}
        for i, (label, cur, key) in enumerate([
            ("Local", local, "local"), ("Roaming", roaming, "roaming"),
        ]):
            var = tk.StringVar()
            self.ap_vars[key] = var
            tk.Label(card, text=label, bg=C["card"], fg=C["text"], font=FONT_B, width=8,
                     anchor="w").grid(row=i, column=0, padx=(14, 6), pady=8, sticky="w")
            tk.Label(card, text=short_path(cur, 38), bg=C["card"], fg=C["sub"],
                     font=FONT_MONO).grid(row=i, column=1, padx=(0, 10), sticky="w")
            tk.Label(card, text="→", bg=C["card"], fg=C["sub"]).grid(row=i, column=2)
            tk.Entry(card, textvariable=var, font=FONT_MONO, bg="#f8fafc", relief="flat",
                     highlightthickness=1, highlightbackground=C["line"],
                     highlightcolor=C["accent"], width=42).grid(
                row=i, column=3, padx=8, pady=8, ipady=3, sticky="we")
            tk.Button(card, text="浏览", font=FONT_S, bg="#f1f5f9", fg=C["text"], relief="flat",
                      padx=8, cursor="hand2",
                      command=lambda v=var: self._pick_dir(v)).grid(row=i, column=4, padx=(0, 14))
        card.columnconfigure(3, weight=1)

        btns = tk.Frame(tab, bg=C["bg"])
        btns.pack(fill="x", padx=12, pady=6)
        tk.Button(btns, text="生成离线迁移脚本（推荐）", font=FONT, bg=C["accent"], fg="white",
                  relief="flat", padx=14, pady=7, cursor="hand2",
                  command=self._gen_appdata_script).pack(side="left")
        tk.Button(btns, text="仅改环境变量（半生效）", font=FONT_S, bg="#f1f5f9", fg=C["text"],
                  relief="flat", padx=12, pady=7, cursor="hand2",
                  command=self._apply_appdata_env).pack(side="left", padx=10)

        why = ("为什么脚本要离线执行？\n\n"
               "迁移方法是在 C:\\Users\\你\\AppData\\Local 的位置放一个指向新盘的目录联接。\n"
               "但登录状态下这个目录被 explorer、各种后台进程占用着，重命名和删除都会失败，\n"
               "硬来会造成下次登录异常。所以脚本必须在「该用户已注销」的状态下运行：\n\n"
               "  1. 建一个临时管理员账户，用它对目标账户的目录操作（最省事）；\n"
               "  2. 进 WinPE / 恢复环境，挂载系统盘后执行；\n"
               "  3. 在登录界面按 Shift+重启 进命令提示符执行。\n\n"
               "在能真正省空间这件事上，其实更推荐「AppData 与缓存」页的逐目录联接：\n"
               "同样透明、不用注销、失败也不影响登录，搬 Chrome、微信、npm 缓存就够了。")
        tk.Label(tab, text=why, bg=C["bg"], fg=C["sub"], font=FONT_S, justify="left",
                 anchor="nw").pack(fill="both", expand=True, padx=20, pady=8)

    def _gen_appdata_script(self):
        local = self.ap_vars["local"].get().strip()
        roaming = self.ap_vars["roaming"].get().strip()
        if not local and not roaming:
            messagebox.showinfo("提示", "先填写新位置再生成脚本。")
            return
        cur_local, cur_roam = E.appdata_current()
        if not local:
            local = os.path.join(os.path.splitdrive(cur_local)[0] + "\\", "AppData", "Local")
        if not roaming:
            roaming = os.path.join(os.path.splitdrive(cur_roam)[0] + "\\", "AppData", "Roaming")
        paths = E.write_appdata_offline_script(local, roaming)
        self.log("已生成脚本：")
        for p in paths:
            self.log(f"  {p}")
        if messagebox.askyesno("已生成", "脚本已生成到备份目录：\n\n" +
                               "\n".join(paths) + "\n\n现在打开所在文件夹？"):
            try:
                os.startfile(os.path.dirname(paths[0]))
            except OSError:
                pass

    def _apply_appdata_env(self):
        if not messagebox.askyesno(
            "确认（不推荐）",
            "只改环境变量的话：\n"
            "· 需要重新登录才生效\n"
            "· Windows 自身和大量程序不会跟随，AppData 会分裂到两个盘\n"
            "· 文件不会被搬运\n\n"
            "确定继续？"):
            return
        local = self.ap_vars["local"].get().strip()
        roaming = self.ap_vars["roaming"].get().strip()
        cur_local, cur_roam = E.appdata_current()
        if not local or not roaming:
            messagebox.showinfo("提示", "两个路径都要填。")
            return
        try:
            E.save_snapshot([], note="AppData 环境变量")
            E.apply_appdata_env_only(local, roaming, self.log)
            messagebox.showinfo("完成", "已写入。请注销后重新登录验证。")
        except Exception as e:
            self.log(f"✕ {e}")
            messagebox.showerror("失败", str(e))

    # -- Tab 5：备份与还原 ------------------------------------------------ #
    def _tab_backup(self):
        tab = tk.Frame(self.nb, bg=C["bg"])
        self.nb.add(tab, text="  备份与还原  ")
        self._info_bar(tab, "每次执行前都会自动备份相关注册表值。还原会写回备份里的路径，"
                            "并可选地把文件搬回原处。")
        wrap = tk.Frame(tab, bg=C["bg"])
        wrap.pack(fill="both", expand=True, padx=12, pady=8)
        self.snap_list = tk.Listbox(wrap, font=FONT_MONO, bg=C["card"], fg=C["text"],
                                    relief="flat", highlightthickness=1,
                                    highlightbackground=C["line"], activestyle="none")
        self.snap_list.pack(fill="both", expand=True, side="left")
        sb = ttk.Scrollbar(wrap, orient="vertical", command=self.snap_list.yview)
        sb.pack(side="left", fill="y")
        self.snap_list.configure(yscrollcommand=sb.set)

        side = tk.Frame(tab, bg=C["bg"])
        side.pack(fill="x", padx=12, pady=(0, 10))
        tk.Button(side, text="刷新列表", font=FONT_S, bg="#f1f5f9", fg=C["text"], relief="flat",
                  padx=10, pady=4, cursor="hand2", command=self._refresh_snapshots).pack(side="left")
        tk.Button(side, text="还原选中项", font=FONT_S, bg=C["accent"], fg="white", relief="flat",
                  padx=10, pady=4, cursor="hand2", command=self._restore).pack(side="left", padx=8)
        tk.Button(side, text="打开备份目录", font=FONT_S, bg="#f1f5f9", fg=C["text"],
                  relief="flat", padx=10, pady=4, cursor="hand2",
                  command=lambda: self._open(E.BACKUP_DIR)).pack(side="left")
        self._refresh_snapshots()

    def _refresh_snapshots(self):
        self.snap_list.delete(0, tk.END)
        self._snaps = E.list_snapshots()
        for p in self._snaps:
            try:
                d = E.load_snapshot(p)
                note = d.get("note") or ""
                self.snap_list.insert(
                    tk.END,
                    f"{d.get('created_at','?')}   {len(d.get('entries',[]))} 项   {note}")
            except Exception:
                self.snap_list.insert(tk.END, os.path.basename(p))

    def _restore(self):
        sel = self.snap_list.curselection()
        if not sel:
            messagebox.showinfo("提示", "先选一条备份记录。")
            return
        path = self._snaps[sel[0]]
        move_back = messagebox.askyesno(
            "还原方式", "是否同时把文件搬回原来的位置？\n\n"
                       "选「是」：注册表 + 文件一起回滚（慢，需要空间）\n"
                       "选「否」：只把注册表里的路径改回去")
        try:
            E.restore_snapshot(path, move_back, self.log)
            messagebox.showinfo("完成", "已还原。建议注销或重启让资源管理器彻底刷新。")
            self._refresh_snapshots()
        except Exception as e:
            self.log(f"✕ 还原失败：{e}")
            messagebox.showerror("失败", str(e))

    def _open(self, path):
        try:
            os.makedirs(path, exist_ok=True)
            os.startfile(path)
        except OSError as e:
            messagebox.showerror("打不开", str(e))

    # -- 日志 / 底栏 ------------------------------------------------------ #
    def _build_log(self):
        wrap = tk.Frame(self.root, bg=C["bg"])
        wrap.pack(fill="both", padx=12, pady=(6, 0))
        head = tk.Frame(wrap, bg=C["bg"])
        head.pack(fill="x")
        tk.Label(head, text="执行日志", bg=C["bg"], fg=C["sub"], font=FONT_S).pack(side="left")
        tk.Button(head, text="清空", font=FONT_S, bg=C["bg"], fg=C["sub"], relief="flat",
                  cursor="hand2", command=lambda: self.logbox.delete("1.0", tk.END)).pack(side="right")
        self.logbox = tk.Text(wrap, height=7, font=FONT_MONO, bg="#0f172a", fg="#cbd5e1",
                              relief="flat", insertbackground="#cbd5e1", wrap="word")
        self.logbox.pack(fill="both", expand=False, pady=(2, 0))

    def _build_footer(self):
        bar = tk.Frame(self.root, bg=C["card"], highlightthickness=1,
                       highlightbackground=C["line"])
        bar.pack(fill="x", side="bottom", pady=(8, 0))
        self.status_lbl = tk.Label(bar, text="勾选要修改的项目，填好新位置，然后点「预检」",
                                   bg=C["card"], fg=C["sub"], font=FONT_S)
        self.status_lbl.pack(side="left", padx=18, pady=10)
        self.btn_run = tk.Button(bar, text="开始执行", font=FONT_B, bg=C["accent"], fg="white",
                                 relief="flat", padx=22, pady=8, cursor="hand2",
                                 command=self._execute)
        self.btn_run.pack(side="right", padx=(6, 16), pady=8)
        self.btn_check = tk.Button(bar, text="预检", font=FONT, bg="#f1f5f9", fg=C["text"],
                                   relief="flat", padx=18, pady=8, cursor="hand2",
                                   command=self._precheck)
        self.btn_check.pack(side="right", padx=6, pady=8)

    # -- 工具 ------------------------------------------------------------- #
    def best_drive(self) -> str:
        best, best_free = None, -1
        for d in "CDEFGH":
            root = f"{d}:\\"
            if os.path.isdir(root) and E.drive_type(root) == "fixed" and E.is_ntfs(root):
                free = E.free_space(root)
                if free > best_free:
                    best, best_free = root, free
        return best or "D:\\"

    def log(self, msg: str):
        if threading.current_thread() is threading.main_thread():
            self._append(msg)
        else:
            self.q.put(("log", msg))

    def _append(self, msg: str):
        self.logbox.insert(tk.END, msg + "\n")
        self.logbox.see(tk.END)

    def _drain(self):
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == "log":
                    self._append(payload)
                elif kind == "size":
                    row, text = payload
                    row.set_status(text, C["sub"])
                elif kind == "done":
                    self._finish(payload)
        except queue.Empty:
            pass
        self.root.after(120, self._drain)

    def _compute_sizes(self):
        """后台算一下各目录占多少，帮用户判断该搬哪个。"""
        def work():
            for row in list(self.rows):
                try:
                    if not os.path.isdir(row.current) or E.is_reparse_point(row.current):
                        continue
                    self.q.put(("size", (row, E.human_size(E.dir_size(row.current)))))
                except OSError:
                    pass
        threading.Thread(target=work, daemon=True).start()

    def _relaunch_admin(self):
        import ctypes
        try:
            if getattr(sys, "frozen", False):
                exe, params = sys.executable, ""
            else:
                exe, params = sys.executable, f'"{os.path.abspath(sys.argv[0])}"'
            rc = ctypes.windll.shell32.ShellExecuteW(
                None, "runas", exe, params, os.path.dirname(os.path.abspath(sys.argv[0])), 1)
            if rc > 32:
                self.root.destroy()
            else:
                messagebox.showinfo("已取消", "没有获得管理员权限。")
        except Exception as e:
            messagebox.showerror("失败", str(e))

    def _show_effects(self):
        win = tk.Toplevel(self.root)
        win.title("各类修改的生效范围")
        win.geometry("720x620")
        win.configure(bg=C["bg"])
        txt = tk.Text(win, font=FONT, bg=C["card"], fg=C["text"], relief="flat", wrap="word",
                      padx=16, pady=14, spacing1=2, spacing3=6)
        txt.pack(fill="both", expand=True, padx=12, pady=12)
        order = ["shellfolder", "junction", "tempdir", "programfiles", "appdata_root"]
        names = {
            "shellfolder": "① 用户文件夹（桌面 / 文档 / 下载 …）",
            "junction": "② AppData 缓存目录（目录联接）",
            "tempdir": "③ 临时目录 TEMP / TMP",
            "programfiles": "④ 程序安装默认目录",
            "appdata_root": "⑤ AppData 整体重定向",
        }
        for k in order:
            e = E.EFFECTS[k]
            txt.insert(tk.END, names[k] + "\n", "h")
            txt.insert(tk.END, f"生效时机：{e['scope']}\n以后的新文件：{e['future']}\n")
            txt.insert(tk.END, e["detail"] + "\n\n", "b")
        txt.tag_configure("h", font=FONT_B, foreground=C["accent"], spacing1=10)
        txt.tag_configure("b", foreground=C["sub"])
        txt.configure(state="disabled")

    # -- 预检 ------------------------------------------------------------- #
    def _selected(self) -> list[Row]:
        return [r for r in self.rows if r.selected]

    def _precheck(self, silent=False) -> list[Row]:
        rows = self._selected()
        for r in self.rows:
            r.set_status("", C["sub"])
        if not rows:
            if not silent:
                messagebox.showinfo("提示", "还没有勾选任何项目。\n勾上要修改的项，并在右侧填好新位置。")
            return []

        good, bad, msgs = [], [], []
        for r in rows:
            checks = E.precheck_move(r.current, r.dst.get(),
                                     need_ntfs=(r.kind in ("junction",)))
            err = [c for c in checks if c.level == "error"]
            warn = [c for c in checks if c.level == "warn"]
            info = [c for c in checks if c.level == "info"]
            if err:
                bad.append(r)
                r.set_status("✕ 有问题", C["err"])
                msgs.append(f"【{r.label}】\n  " + "\n  ".join(c.message for c in err))
            else:
                good.append(r)
                r.set_status("✓ " + (info[0].message.split("，")[0] if info else "就绪"), C["ok"])
                if warn or info:
                    msgs.append(f"【{r.label}】\n  " + "\n  ".join(c.message for c in warn + info))

        self.log(f"预检：{len(good)} 项可执行，{len(bad)} 项有问题")
        if not silent:
            body = ""
            if good:
                body += "将执行：\n" + "\n".join(f"  {r.label}  →  {r.dst.get()}" for r in good)
            if msgs:
                body += "\n\n检查结果：\n" + "\n".join(msgs)
            if bad:
                body += "\n\n标 ✕ 的项目需要先解决问题，本次会被跳过。"
            messagebox.showinfo("预检结果", body or "没有问题。")
        return good

    # -- 执行 ------------------------------------------------------------- #
    def _execute(self):
        if self.busy:
            return
        rows = self._precheck(silent=True)
        if not rows:
            messagebox.showinfo("没有可执行项", "勾选并填好新位置后再试；点「预检」可以看到具体原因。")
            return

        move_files = self.move_shell.get()
        lines = []
        for r in rows:
            tag = "（不搬文件，只改指向）" if r.kind == "shellfolder" and not move_files else ""
            lines.append(f"  {r.label}\n     {r.current}\n     → {r.dst.get()}  {tag}")
        if not messagebox.askyesno(
            "确认执行",
            "即将修改：\n\n" + "\n".join(lines) +
            "\n\n执行前会自动备份注册表。中途失败可以到「备份与还原」页回滚。\n\n确定开始？"):
            return

        planned = [{"kind": r.kind, "label": r.label, "old": r.current, "new": r.dst.get()}
                   for r in rows]
        try:
            snap = E.save_snapshot(planned, note=f"批量迁移 {len(rows)} 项")
            self.log(f"注册表已备份：{snap}")
        except Exception as e:
            messagebox.showerror("无法备份", f"备份失败，已中止：\n{e}")
            return

        self._set_busy(True)
        threading.Thread(target=self._worker, args=(rows, move_files), daemon=True).start()

    def _worker(self, rows, move_files):
        ok, fail = 0, 0
        try:
            for r in rows:
                self.log(f"── {r.label}")
                try:
                    if r.kind == "shellfolder":
                        E.apply_shell_folder(r.kf, r.dst.get(), move_files, self.log)
                    elif r.kind == "junction":
                        E.apply_junction_move(r.label, r.current, r.dst.get(), self.log)
                    elif r.kind == "tempdir":
                        E.apply_temp_dir(r.dst.get(), move_files, self.log)
                    ok += 1
                except E.MoveFailed as e:
                    fail += 1
                    self.log(f"  ✕ {e}")
                    if e.leftovers:
                        self.log("  仍被占用的项：" + "、".join(e.leftovers[:6]))
                    self.log("  提示：关掉相关程序后重试；已复制的文件保留在目标目录，不会丢。")
                except Exception as e:
                    fail += 1
                    self.log(f"  ✕ {type(e).__name__}: {e}")
        finally:
            self.q.put(("done", (ok, fail)))

    def _set_busy(self, busy: bool):
        self.busy = busy
        state = "disabled" if busy else "normal"
        self.btn_run.configure(state=state, text="执行中…" if busy else "开始执行")
        self.btn_check.configure(state=state)
        self.status_lbl.configure(
            text="正在搬运，请勿关闭窗口…" if busy else "完成。建议注销或重启，让资源管理器彻底刷新。")

    def _finish(self, result):
        self._set_busy(False)
        ok, fail = result
        self.log(f"══ 结束：成功 {ok} 项，失败 {fail} 项")
        if fail:
            messagebox.showwarning("部分完成", f"成功 {ok} 项，失败 {fail} 项。\n详情见下方日志；"
                                            "需要回滚请到「备份与还原」页。")
        else:
            messagebox.showinfo("完成", f"{ok} 项全部处理完成。\n\n建议注销或重启一次，"
                                     "让资源管理器和新启动的程序彻底识别新位置。")
        self._refresh_snapshots()


def main():
    root = tk.Tk()
    app = App(root)
    if "--smoke" in sys.argv:
        root.after(1500, root.destroy)
    root.mainloop()
    return app


if __name__ == "__main__":
    main()
