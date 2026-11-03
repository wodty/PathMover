# PathMover · Windows 默认存储位置管理

[![release](https://img.shields.io/github/v/release/wodty/PathMover?color=2563eb)](https://github.com/wodty/PathMover/releases)
[![platform](https://img.shields.io/badge/platform-Windows%2010%20%2F%2011-2563eb)](https://github.com/wodty/PathMover)
[![license](https://img.shields.io/badge/license-MIT-16a34a)](LICENSE)

> 把 C 盘越用越满的「桌面 / 下载 / AppData 缓存 / 程序安装目录」按需搬到别的盘。
> A Windows GUI tool that relocates Desktop, Downloads, AppData caches and the
> program install directory to another drive — item by item, with automatic
> registry backup and one-click rollback.

按需把 **桌面 / 文档 / 下载 / 图片 / 音乐 / 视频**、**AppData 里的缓存目录**、
**临时目录**、**程序安装默认目录** 迁移到你指定的磁盘。每一项都可以单独勾选、单独指定目标位置。

纯 Python 标准库实现（tkinter 界面），无第三方依赖。

## 已打包好的可执行文件

```
dist\PathMover.exe        约 12 MB，单文件
```

- 免装 Python、免装任何运行库，双击即用，可拷到别的机器上跑；
- 首次启动会自解压到临时目录，约 1~2 秒，之后正常；
- 默认以普通权限运行（够用），需要改程序安装目录时点界面上「以管理员重启」。

## 快速开始

```bat
:: 双击 main.py，或：
python main.py
```

建议先用「以管理员重启」拿到管理员权限（只有改程序安装目录才必须，其它操作普通权限即可）。

操作流程：勾选项目 → 填新位置（或点浏览）→ **预检** → **开始执行**。
每次执行前自动把相关注册表值备份到 `%LOCALAPPDATA%\PathMover\backups\`，
出问题到「备份与还原」页一键回滚（可选连文件一起搬回）。

## 各类修改的生效范围（重要）

| 项目 | 生效时机 | 以后的新文件 | 说明 |
|---|---|---|---|
| 用户文件夹（桌面/文档/下载…） | 立即 · 永久 | ✅ 直接落在新位置 | 走 `SHSetKnownFolderPath`，即资源管理器「位置→移动」同款 API。极少数写死路径的老程序除外 |
| AppData 缓存（目录联接） | 立即 · 永久 | ✅ 程序无感，全部写进新盘 | 原位置留联接指向新盘，程序仍按原路径读写 |
| 临时目录 TEMP/TMP | 新进程立即生效 | ✅ | 已开着的程序重启后才切换 |
| 程序安装默认目录 | 只影响今后的新安装 | ⚠️ 仅限读注册表的安装器 | 已装程序不动；风险最高，建议装完再改回 |
| AppData 整体重定向 | 需重新登录 | ⚠️ 半生效，会分裂成两处 | 只改环境变量时 Windows 自身不跟随；真要整体搬请用「生成离线迁移脚本」 |

界面里每个页顶部都有一句话提示，「生效范围说明」按钮里有完整版。

## 恢复

- 「备份与还原」页 → 选中记录 → 还原；
- 选「是」会把文件一起搬回原处，选「否」只改回注册表里的路径；
- 还原后建议注销或重启，让资源管理器彻底刷新。

## 打包成单个 exe

```bat
build_exe.bat
```

内部用 PyInstaller `--onefile --windowed`，产物在 `dist\PathMover.exe`。
需要管理员 UAC 提示的话，把参数换成 `--uac-admin`。
也可以直接用已生成好的 spec：`pyinstaller PathMover.spec`。

## 目录结构

```
engine.py   引擎：known folder / 联接 / 注册表 / 搬运 / 快照（可单独 `python engine.py` 自检）
main.py     tkinter 界面
_shot.py    开发用：把窗口渲染成 PNG 检查布局
_e2e.py     开发用：沙盒目录端到端测试（建行→预检→执行→还原）
```

## 已知限制

- 「AppData 整体」的离线脚本必须在目标用户**已注销**的状态下运行（脚本内有说明）；
- 文件搬运用 `robocopy /MOVE`，被占用的文件会保留在原处并明确报告，不会丢数据；
- 目标盘建议固定内置 NTFS 盘，可移动盘/网络盘会给出警告。
