# -*- coding: utf-8 -*-
"""开发用：端到端测试（沙盒目录），验证 建行 → 预检 → 执行 → 还原 全链路。"""

import os
import shutil
import tkinter as tk

import engine as E
import main as M

BASE = os.path.join(os.environ["TEMP"], "pathmover_e2e")
SRC = os.path.join(BASE, "cache-src")
DST = os.path.join("D:\\", "pathmover_e2e_dst")


def check(name, cond):
    print(f"  {'✓' if cond else '✗'} {name}")
    if not cond:
        raise SystemExit(f"测试失败: {name}")


def main():
    shutil.rmtree(BASE, ignore_errors=True)
    shutil.rmtree(DST, ignore_errors=True)
    os.makedirs(SRC)
    open(os.path.join(SRC, "a.txt"), "w", encoding="utf-8").write("hello")
    os.makedirs(os.path.join(SRC, "sub"))
    open(os.path.join(SRC, "sub", "b.txt"), "w", encoding="utf-8").write("world")

    root = tk.Tk()
    app = M.App(root)
    root.withdraw()

    print("[1] 添加自定义行并勾选")
    row = M.Row(app.cache_body, app, "e2e-cache", SRC, "junction")
    app.rows.append(row)
    row.dst.set(DST)
    row.on.set(True)
    row._on_toggle()

    print("[2] 预检")
    good = app._precheck(silent=True)
    check("预检通过 1 项", len(good) == 1)

    print("[3] 执行")
    planned = [{"kind": "junction", "label": "e2e-cache", "old": SRC, "new": DST}]
    snap = E.save_snapshot(planned, note="e2e")
    E.apply_junction_move(row.label, SRC, DST, app.log)
    check("源位置变成了联接", E.is_reparse_point(SRC))
    check("联接目标正确", os.path.normcase(E.read_junction(SRC)) == os.path.normcase(DST))
    check("通过联接读到文件", open(os.path.join(SRC, "a.txt"), encoding="utf-8").read() == "hello")
    check("子目录也在", os.path.isfile(os.path.join(SRC, "sub", "b.txt")))

    print("[4] 还原（连文件一起搬回）")
    E.restore_snapshot(snap, True, app.log)
    check("联接已删除", not E.is_reparse_point(SRC))
    check("数据回到原位", open(os.path.join(SRC, "a.txt"), encoding="utf-8").read() == "hello")
    check("目标目录已清空", not os.path.exists(DST) or not os.listdir(DST))

    print("[5] 快照列表")
    E.list_snapshots()
    check("快照可读", E.load_snapshot(snap)["note"] == "e2e")

    root.destroy()
    shutil.rmtree(BASE, ignore_errors=True)
    shutil.rmtree(DST, ignore_errors=True)
    print("\n端到端测试全部通过。")


if __name__ == "__main__":
    main()
