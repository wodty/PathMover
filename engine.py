# -*- coding: utf-8 -*-
"""PathMover 引擎层

把 Windows 上「修改默认存储位置」的实际操作全部集中在这里，UI 只负责收集参数和展示日志。
所有写操作在动手前都会拍一份注册表快照，方便一键还原。

支持的五类目标
--------------
1. 用户文件夹（桌面/文档/下载/图片/音乐/视频/保存的游戏）
   走 SHSetKnownFolderPath，就是资源管理器「属性 → 位置 → 移动」背后那套 API。
2. 缓存目录迁移（Chrome/Edge/npm/pip…）
   复制 + 目录联接（junction），程序完全无感。
3. 临时目录 TEMP/TMP
   改 HKCU\\Environment。
4. 程序安装默认目录
   改 ProgramFilesDir 系列值，需管理员。
5. AppData 整体重定向
   改 LOCALAPPDATA/APPDATA 环境变量，实验性，见 README 的风险说明。
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import datetime
import json
import os
import shutil
import subprocess
import sys
import winreg

APP_NAME = "PathMover"
APP_VERSION = "1.0.0"
BACKUP_DIR = os.path.join(
    os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), APP_NAME, "backups"
)
ROBOCOPY = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "Robocopy.exe")
CREATE_NO_WINDOW = 0x08000000
FILE_ATTRIBUTE_REPARSE_POINT = 0x400


# --------------------------------------------------------------------------- #
# 基础工具
# --------------------------------------------------------------------------- #
def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def norm(path: str) -> str:
    return os.path.normcase(os.path.abspath(os.path.expandvars(path.strip().strip('"')))).rstrip("\\")


def ensure_dir(path: str) -> str:
    os.makedirs(path, exist_ok=True)
    return path


def human_size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1024
    return f"{n:.1f} TB"


def dir_size(path: str) -> int:
    """统计目录大小，跳过符号链接/联接，避免重复计算和死循环。"""
    total = 0
    stack = [path]
    while stack:
        cur = stack.pop()
        try:
            with os.scandir(cur) as it:
                for e in it:
                    try:
                        if e.is_symlink() or e.is_junction():
                            continue
                        if e.is_dir(follow_symlinks=False):
                            stack.append(e.path)
                        else:
                            total += e.stat(follow_symlinks=False).st_size
                    except OSError:
                        continue
        except OSError:
            continue
    return total


def is_reparse_point(path: str) -> bool:
    try:
        return bool(os.lstat(path).st_file_attributes & FILE_ATTRIBUTE_REPARSE_POINT)
    except OSError:
        return False


def read_junction(path: str) -> str | None:
    """返回联接/符号链接的目标路径，不是链接则返回 None。"""
    if not is_reparse_point(path):
        return None
    try:
        target = os.readlink(path)
    except OSError:
        return "（无法读取目标）"
    if target.startswith("\\\\?\\"):
        target = target[4:]
    return target


def drive_type(path: str) -> str:
    root = os.path.splitdrive(os.path.abspath(path))[0] + "\\"
    try:
        t = ctypes.windll.kernel32.GetDriveTypeW(ctypes.c_wchar_p(root))
    except Exception:
        return "unknown"
    return {
        0: "unknown", 1: "no_root", 2: "removable", 3: "fixed",
        4: "network", 5: "cdrom", 6: "ramdisk",
    }.get(t, "unknown")


def is_ntfs(path: str) -> bool:
    root = os.path.splitdrive(os.path.abspath(path))[0] + "\\"
    fs = ctypes.create_unicode_buffer(64)
    ok = ctypes.windll.kernel32.GetVolumeInformationW(
        ctypes.c_wchar_p(root), None, 0, None, None, None, fs, 64
    )
    return bool(ok) and fs.value.upper() == "NTFS"


def free_space(path: str) -> int:
    try:
        p = os.path.abspath(path)
        while p and not os.path.isdir(p):
            parent = os.path.dirname(p)
            if parent == p:
                break
            p = parent
        return shutil.disk_usage(p).free
    except OSError:
        return 0


def broadcast_change() -> None:
    """通知所有窗口环境变量/文件夹位置变了（不重启资源管理器也尽量生效）。"""
    HWND_BROADCAST, WM_SETTINGCHANGE, SMTO_ABORTIFHUNG = 0xFFFF, 0x001A, 0x0002
    try:
        res = wt.DWORD()
        ctypes.windll.user32.SendMessageTimeoutW(
            HWND_BROADCAST, WM_SETTINGCHANGE, 0, ctypes.c_wchar_p("Environment"),
            SMTO_ABORTIFHUNG, 3000, ctypes.byref(res),
        )
        ctypes.windll.shell32.SHChangeNotify(0x8000000, 0x1000, None, None)  # SHCNE_ASSOCCHANGED
    except Exception:
        pass


# --------------------------------------------------------------------------- #
# Known Folder（用户文件夹）
# --------------------------------------------------------------------------- #
class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wt.DWORD),
        ("Data2", wt.WORD),
        ("Data3", wt.WORD),
        ("Data4", ctypes.c_ubyte * 8),
    ]


_shell32 = ctypes.WinDLL("shell32", use_last_error=True)
_ole32 = ctypes.WinDLL("ole32", use_last_error=True)

_ole32.CLSIDFromString.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(GUID)]
_ole32.CLSIDFromString.restype = ctypes.c_long
_ole32.CoTaskMemFree.argtypes = [ctypes.c_void_p]
_ole32.CoTaskMemFree.restype = None

_shell32.SHGetKnownFolderPath.argtypes = [
    ctypes.POINTER(GUID), wt.DWORD, wt.HANDLE, ctypes.POINTER(ctypes.c_wchar_p)
]
_shell32.SHGetKnownFolderPath.restype = ctypes.c_long
_shell32.SHSetKnownFolderPath.argtypes = [
    ctypes.POINTER(GUID), wt.DWORD, wt.HANDLE, ctypes.c_wchar_p
]
_shell32.SHSetKnownFolderPath.restype = ctypes.c_long


def _guid(text: str) -> GUID:
    g = GUID()
    if _ole32.CLSIDFromString(ctypes.c_wchar_p(text), ctypes.byref(g)) != 0:
        raise ValueError(f"非法 GUID: {text}")
    return g


def get_known_path(guid: str) -> str:
    g = _guid(guid)
    out = ctypes.c_wchar_p()
    hr = _shell32.SHGetKnownFolderPath(ctypes.byref(g), 0, None, ctypes.byref(out))
    if hr != 0:
        raise OSError(f"SHGetKnownFolderPath 失败, HRESULT=0x{hr & 0xFFFFFFFF:08X}")
    try:
        return out.value
    finally:
        _ole32.CoTaskMemFree(ctypes.cast(out, ctypes.c_void_p))


def set_known_path(guid: str, path: str) -> None:
    g = _guid(guid)
    hr = _shell32.SHSetKnownFolderPath(ctypes.byref(g), 0, None, ctypes.c_wchar_p(path))
    if hr != 0:
        raise OSError(f"SHSetKnownFolderPath 失败, HRESULT=0x{hr & 0xFFFFFFFF:08X}")


class KnownFolder:
    def __init__(self, key, label, guid, reg_name, default_rel, note=""):
        self.key = key
        self.label = label
        self.guid = guid
        self.reg_name = reg_name          # User Shell Folders 里的值名
        self.default_rel = default_rel    # 默认位置（相对 USERPROFILE）
        self.note = note

    @property
    def default_path(self) -> str:
        return os.path.join(os.environ.get("USERPROFILE", ""), self.default_rel)

    def current(self) -> str:
        try:
            return get_known_path(self.guid)
        except OSError:
            return self.default_path


KNOWN_FOLDERS = [
    KnownFolder("desktop", "桌面", "{B4BFCC3A-DB2C-424C-B029-7FE99A87C641}",
                "Desktop", "Desktop"),
    KnownFolder("documents", "文档", "{FDD39AD0-238F-46AF-ADB4-6C85480369C7}",
                "Personal", "Documents"),
    KnownFolder("downloads", "下载", "{374DE290-123F-4565-9164-39C4925E467B}",
                "{374DE290-123F-4565-9164-39C4925E467B}", "Downloads"),
    KnownFolder("pictures", "图片", "{33E28130-4E1E-4676-835A-98395C3BC3BB}",
                "My Pictures", "Pictures"),
    KnownFolder("music", "音乐", "{4BD8D571-6D19-48D3-BE97-422220080E43}",
                "My Music", "Music"),
    KnownFolder("videos", "视频", "{18989B1D-99B5-455B-841C-AB7C74E4DDFC}",
                "My Video", "Videos"),
    KnownFolder("savedgames", "保存的游戏", "{4C5C32FF-BB9D-43B0-B5B4-2D72E54EAAA4}",
                "{4C5C32FF-BB9D-43B0-B5B4-2D72E54EAAA4}", "Saved Games"),
]


# --------------------------------------------------------------------------- #
# 生效范围说明（UI 直接展示，避免用户误以为「改了就全搬过去」）
# --------------------------------------------------------------------------- #
EFFECTS = {
    "shellfolder": {
        "scope": "立即生效 · 永久",
        "future": "以后的新文件会直接落在新位置",
        "detail": (
            "这类文件夹的位置是写进用户注册表的「已知文件夹」，系统和新程序都通过 "
            "SHGetKnownFolderPath / %USERPROFILE% 变量来定位。改完后：\n"
            "· 浏览器下载、截图、Office 保存对话框的默认目录 → 新位置\n"
            "· 桌面新建文件、拖到桌面 → 新位置\n"
            "· 极少数把 C:\\Users\\你\\Downloads 写死在代码里的老程序仍会去旧路径，"
            "这种情况没法通过改配置解决。"
        ),
    },
    "junction": {
        "scope": "立即生效 · 永久 · 程序无感",
        "future": "程序以为还在原路径，实际全部写进新盘",
        "detail": (
            "移走内容后在原位置留一个「目录联接」（类似快捷方式的指针）。\n"
            "· 程序读写的仍是原路径，文件系统自动转去新盘，所以缓存、更新、新版本\n"
            "  产生的数据都会继续落到新位置，不需要改任何软件设置\n"
            "· 卸载程序时可能只删掉联接、把数据留在新盘（清一下就行，不会丢数据）\n"
            "· 例外：若程序检测到目录是联接就拒绝运行（极罕见），或卸载后重装\n"
            "  重建了真实目录，需要重新迁移一次"
        ),
    },
    "tempdir": {
        "scope": "新启动的程序立即生效",
        "future": "之后启动的进程都用新临时目录",
        "detail": (
            "改的是 HKCU\\Environment 里的 TEMP/TMP。已经开着的程序（包括资源管理器）\n"
            "仍持有旧路径，重启这些程序或重新登录后才会切过去。"
        ),
    },
    "programfiles": {
        "scope": "只影响今后的新安装",
        "future": "已装程序不会搬；新装的看安装器是否读注册表",
        "detail": (
            "改的是 ProgramFilesDir 系列注册表值。\n"
            "· 已安装的程序一个都不会移动，它们记录的是绝对路径，照常能用\n"
            "· 之后新装的程序：MSI / Inno Setup / NSIS 等主流安装器会读到新目录，\n"
            "  但把 C:\\Program Files 写死的安装包仍然装到 C 盘\n"
            "· %ProgramFiles% 环境变量要重新登录才更新\n"
            "· 风险最高：部分驱动、运行库、Windows 更新组件假定程序在 C 盘，"
            "建议只在新装软件前临时改，装完再改回默认"
        ),
    },
    "appdata_root": {
        "scope": "需重新登录 · 且只部分生效",
        "future": "一部分程序去新盘，一部分仍去 C 盘 → 会分裂成两处",
        "detail": (
            "只改 LOCALAPPDATA/APPDATA 环境变量时，只有「读环境变量」的程序会跟随；\n"
            "Windows 自己（shell、UWP、大量 .NET 应用）是通过 known folder 接口读注册表\n"
            "定位 AppData 的，它们不认这个变量。结果就是 AppData 被拆到两个盘。\n"
            "所以：想省空间请用上面的「单目录联接」；真要整体搬，请生成离线脚本，"
            "在登录前执行目录替换（工具不直接在线做，因为目录被占用必然失败）。"
        ),
    },
}


# --------------------------------------------------------------------------- #
# 文件搬运
# --------------------------------------------------------------------------- #
class MoveResult:
    def __init__(self, ok: bool, code: int, src: str, leftovers: list[str], detail: str = ""):
        self.ok = ok
        self.code = code
        self.src = src
        self.leftovers = leftovers
        self.detail = detail

    def __bool__(self):
        return self.ok


def move_tree(src: str, dst: str, log=None) -> MoveResult:
    """用 robocopy /MOVE 搬运目录内容。支持长路径、跨盘、保留时间戳与 ACL。"""
    log = log or (lambda m: None)
    ensure_dir(dst)
    if not os.path.isdir(src):
        return MoveResult(True, 0, src, [], "源目录不存在，无需搬运")
    if norm(src) == norm(dst):
        return MoveResult(True, 0, src, [], "源与目标相同，跳过")

    args = [
        ROBOCOPY, src, dst,
        "/E", "/MOVE", "/R:1", "/W:1",
        "/NFL", "/NDL", "/NJH", "/NJS", "/NP", "/XJ",
    ]
    log(f"  搬运: {src}  ->  {dst}")
    try:
        cp = subprocess.run(args, capture_output=True, text=True, errors="replace",
                            creationflags=CREATE_NO_WINDOW)
        code = cp.returncode
    except OSError as e:
        return MoveResult(False, -1, src, [], f"无法启动 robocopy: {e}")

    leftovers: list[str] = []
    if os.path.isdir(src):
        try:
            leftovers = os.listdir(src)[:20]
        except OSError:
            leftovers = ["（目录不可读）"]
    ok = code < 8 and not leftovers
    detail = f"robocopy 返回码 {code}"
    if code >= 8:
        detail += "；" + (cp.stdout or cp.stderr or "").strip().splitlines()[-1:][0] if (cp.stdout or cp.stderr) else detail
    elif leftovers:
        detail += f"；源目录仍有 {len(leftovers)} 项被占用"
    log("  " + detail)
    return MoveResult(ok, code, src, leftovers, detail)


# --------------------------------------------------------------------------- #
# 目录联接（junction）
# --------------------------------------------------------------------------- #
def create_junction(link_path: str, target_path: str) -> None:
    """在 link_path 建一个指向 target_path 的目录联接（普通用户即可，不需要管理员）。

    用 mklink /J 而不是自己拼 REPARSE_DATA_BUFFER：系统工具在长路径、非空目录、
    ACL 继承这些细节上都处理好了，出错信息也更好读。注意 mklink 要求链接位置
    **不存在**，所以已有空目录时要先删掉。
    """
    link_path = os.path.abspath(link_path)
    target_path = os.path.abspath(target_path)
    ensure_dir(target_path)

    if os.path.lexists(link_path):
        if is_reparse_point(link_path):
            raise OSError(f"该位置已经是联接: {link_path}")
        if os.path.isdir(link_path):
            if os.listdir(link_path):
                raise OSError(f"链接位置必须为空目录: {link_path}")
            os.rmdir(link_path)
        else:
            raise OSError(f"链接位置被文件占用: {link_path}")

    cp = subprocess.run(
        ["cmd", "/c", "mklink", "/J", link_path, target_path],
        capture_output=True, text=True, errors="replace", creationflags=CREATE_NO_WINDOW,
    )
    if cp.returncode != 0:
        msg = (cp.stdout or "") + (cp.stderr or "")
        raise OSError(f"mklink 失败: {msg.strip()[:200]}")
    if not is_reparse_point(link_path):
        raise OSError("mklink 返回成功但目录不是联接，已中止")


def remove_junction(link_path: str) -> None:
    """删除联接本身，绝不动目标里的数据。"""
    if not is_reparse_point(link_path):
        raise OSError(f"不是目录联接: {link_path}")
    os.rmdir(link_path)


# --------------------------------------------------------------------------- #
# 注册表
# --------------------------------------------------------------------------- #
HIVES = {"HKCU": winreg.HKEY_CURRENT_USER, "HKLM": winreg.HKEY_LOCAL_MACHINE}

# 32 位解释器读 HKLM\SOFTWARE 会被自动重定向到 WOW6432Node，而 ProgramFilesDir
# 在两个视图里的值可能不同；一律强制 64 位视图，保证读写的永远是同一个键。
_WOW = getattr(winreg, "KEY_WOW64_64KEY", 0)

USER_SHELL = r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders"
SHELL_FOLDERS = r"Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders"
ENVIRONMENT = "Environment"
WIN_CURRENT = r"SOFTWARE\Microsoft\Windows\CurrentVersion"
WOW_CURRENT = r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion"

PROGRAM_DIR_VALUES = [
    (WIN_CURRENT, "ProgramFilesDir"),
    (WIN_CURRENT, "ProgramFilesDir (x86)"),
    (WIN_CURRENT, "ProgramW6432Dir"),
    (WOW_CURRENT, "ProgramFilesDir"),
]

REG_TYPE_NAME = {
    winreg.REG_SZ: "REG_SZ",
    winreg.REG_EXPAND_SZ: "REG_EXPAND_SZ",
    winreg.REG_DWORD: "REG_DWORD",
    winreg.REG_MULTI_SZ: "REG_MULTI_SZ",
}


def read_reg(hive: str, path: str, name: str):
    try:
        with winreg.OpenKey(HIVES[hive], path, 0, winreg.KEY_READ | _WOW) as k:
            return winreg.QueryValueEx(k, name)
    except OSError:
        return None, None


def write_reg(hive: str, path: str, name: str, value, vtype) -> None:
    with winreg.CreateKeyEx(HIVES[hive], path, 0, winreg.KEY_SET_VALUE | _WOW) as k:
        winreg.SetValueEx(k, name, 0, vtype, value)


def delete_reg(hive: str, path: str, name: str) -> None:
    try:
        with winreg.OpenKey(HIVES[hive], path, 0, winreg.KEY_SET_VALUE | _WOW) as k:
            winreg.DeleteValue(k, name)
    except OSError:
        pass


def get_program_dirs() -> dict:
    return {name: read_reg("HKLM", path, name)[0] for path, name in PROGRAM_DIR_VALUES}


def set_program_dirs(x64: str, x86: str) -> None:
    if not is_admin():
        raise PermissionError("修改程序安装目录需要管理员权限")
    x64, x86 = os.path.abspath(x64), os.path.abspath(x86)
    ensure_dir(x64)
    ensure_dir(x86)
    write_reg("HKLM", WIN_CURRENT, "ProgramFilesDir", x64, winreg.REG_SZ)
    write_reg("HKLM", WIN_CURRENT, "ProgramW6432Dir", x64, winreg.REG_SZ)
    write_reg("HKLM", WIN_CURRENT, "ProgramFilesDir (x86)", x86, winreg.REG_SZ)
    write_reg("HKLM", WOW_CURRENT, "ProgramFilesDir", x86, winreg.REG_SZ)
    broadcast_change()


def get_env_var(name: str) -> str | None:
    value, _ = read_reg("HKCU", ENVIRONMENT, name)
    return value


def set_env_var(name: str, value: str) -> None:
    write_reg("HKCU", ENVIRONMENT, name, value, winreg.REG_EXPAND_SZ)
    broadcast_change()


def get_temp_dir() -> str:
    return os.path.expandvars(get_env_var("TEMP") or os.environ.get("TEMP", ""))


# 常见「吃盘大户」，只把真实存在的列出来
def cache_candidates() -> list[tuple[str, str]]:
    la = os.path.expandvars(os.environ.get("LOCALAPPDATA", "") or get_env_var("LOCALAPPDATA") or "")
    ra = os.path.expandvars(os.environ.get("APPDATA", "") or get_env_var("APPDATA") or "")
    up = os.environ.get("USERPROFILE", "")
    cand = [
        ("Chrome 用户数据", os.path.join(la, "Google", "Chrome", "User Data")),
        ("Edge 用户数据", os.path.join(la, "Microsoft", "Edge", "User Data")),
        ("npm 缓存", os.path.join(la, "npm-cache")),
        ("pip 缓存", os.path.join(la, "pip", "Cache")),
        ("NuGet 缓存", os.path.join(up, ".nuget", "packages")),
        ("Gradle 缓存", os.path.join(up, ".gradle")),
        ("Maven 仓库", os.path.join(up, ".m2", "repository")),
        ("Docker 数据", os.path.join(la, "Docker")),
        ("Steam 客户端", os.path.join("C:\\Program Files (x86)", "Steam")),
        ("微信文件", os.path.join(up, "Documents", "WeChat Files")),
        ("QQ 文件", os.path.join(up, "Documents", "Tencent Files")),
    ]
    return [(label, path) for label, path in cand if os.path.isdir(path)]


# --------------------------------------------------------------------------- #
# 预检
# --------------------------------------------------------------------------- #
class Check:
    def __init__(self, level: str, message: str):
        self.level = level      # ok / warn / error
        self.message = message

    def __repr__(self):
        return f"<{self.level}: {self.message}>"


def precheck_move(src: str, dst: str, need_ntfs: bool = False) -> list[Check]:
    """搬运前体检：盘符、文件系统、空间、嵌套关系。"""
    out: list[Check] = []
    if not dst.strip():
        return [Check("error", "目标路径为空")]
    dst_abs = os.path.abspath(os.path.expandvars(dst.strip().strip('"')))
    if norm(src) == norm(dst_abs):
        return [Check("error", "目标与当前位置相同，无需修改")]

    dt = drive_type(dst_abs)
    if dt == "unknown" or dt == "no_root":
        out.append(Check("error", f"目标磁盘不可用：{os.path.splitdrive(dst_abs)[0]}"))
        return out
    if dt in ("removable", "network", "cdrom"):
        out.append(Check("warn", "目标是可移动/网络盘，拔盘或断网后这些位置会失效"))

    parent = os.path.dirname(dst_abs)
    if parent and not os.path.isdir(parent):
        # 允许逐级创建，但盘符根必须存在
        if not os.path.isdir(os.path.splitdrive(dst_abs)[0] + "\\"):
            out.append(Check("error", "目标盘符不存在"))

    if need_ntfs and not is_ntfs(dst_abs):
        out.append(Check("error", "目标卷不是 NTFS，无法建立目录联接"))

    if len(dst_abs) > 180:
        out.append(Check("warn", "目标路径偏长，个别老程序可能无法访问"))

    # 嵌套关系：目标不能落在源里面，反之亦然
    if os.path.isdir(src):
        ns, nd = norm(src), norm(dst_abs)
        if nd.startswith(ns + os.sep):
            out.append(Check("error", "目标路径位于源目录内部，会自我递归"))
        elif ns.startswith(nd + os.sep):
            out.append(Check("error", "目标路径是源目录的上级目录，会破坏现有结构"))

        size = dir_size(src)
        free = free_space(dst_abs)
        if free and size:
            out.append(Check("info", f"待搬运 {human_size(size)}，目标剩余 {human_size(free)}"))
            if free < size:
                out.append(Check("error", "目标磁盘剩余空间不足"))
            elif free < size * 1.05:
                out.append(Check("warn", "目标磁盘空间非常紧张（不足 5% 余量）"))

    if "OneDrive" in src:
        out.append(Check("warn", "当前位置在 OneDrive 下，建议先关闭「备份 → 管理备份」再迁移，否则会被同步回来"))

    if not any(c.level == "error" for c in out):
        out.insert(0, Check("ok", "预检通过"))
    return out


# --------------------------------------------------------------------------- #
# 注册表快照 / 还原
# --------------------------------------------------------------------------- #
class SnapshotError(Exception):
    pass


def _dump_key(hive: str, path: str, only: list[str] | None = None) -> list[dict]:
    rows = []
    try:
        with winreg.OpenKey(HIVES[hive], path) as k:
            count = winreg.QueryInfoKey(k)[1]
            for i in range(count):
                try:
                    name, value, vtype = winreg.EnumValue(k, i)
                except OSError:
                    continue
                if only is not None and name not in only:
                    continue
                if vtype == winreg.REG_BINARY:
                    continue
                rows.append({
                    "hive": hive, "path": path, "name": name,
                    "type": vtype, "type_name": REG_TYPE_NAME.get(vtype, str(vtype)),
                    "value": value,
                })
    except OSError:
        pass
    return rows


def capture_registry() -> list[dict]:
    """拍下所有可能被改动的注册表值。"""
    rows: list[dict] = []
    rows += _dump_key("HKCU", USER_SHELL)
    rows += _dump_key("HKCU", SHELL_FOLDERS)
    rows += _dump_key("HKCU", ENVIRONMENT)
    pf_names = [name for _, name in PROGRAM_DIR_VALUES]
    rows += _dump_key("HKLM", WIN_CURRENT, only=pf_names)
    rows += _dump_key("HKLM", WOW_CURRENT, only=pf_names)
    return rows


def save_snapshot(entries: list[dict], note: str = "") -> str:
    ensure_dir(BACKUP_DIR)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    path = os.path.join(BACKUP_DIR, f"snapshot-{stamp}.json")
    payload = {
        "app": APP_NAME,
        "version": APP_VERSION,
        "created_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "computer": os.environ.get("COMPUTERNAME", ""),
        "user": os.environ.get("USERNAME", ""),
        "note": note,
        "admin": is_admin(),
        "registry": capture_registry(),
        "entries": entries,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return path


def list_snapshots() -> list[str]:
    if not os.path.isdir(BACKUP_DIR):
        return []
    files = [os.path.join(BACKUP_DIR, f) for f in os.listdir(BACKUP_DIR) if f.endswith(".json")]
    return sorted(files, reverse=True)


def load_snapshot(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def restore_snapshot(path: str, move_files_back: bool = False, log=None) -> None:
    """按快照还原注册表；可选把文件搬回原处。"""
    log = log or (lambda m: None)
    data = load_snapshot(path)
    entries = data.get("entries", [])

    if move_files_back:
        for e in reversed(entries):
            kind = e.get("kind")
            old, new = e.get("old"), e.get("new")
            if not (old and new) or norm(old) == norm(new):
                continue
            log(f"  搬回文件: {new}  ->  {old}")
            if kind == "junction":
                # 联接在旧路径（old），数据在新路径（new）
                if is_reparse_point(old):
                    remove_junction(old)
                    move_tree(os.path.abspath(new), old, log)
                elif os.path.isdir(new) and not os.path.isdir(old):
                    move_tree(new, old, log)
            else:
                if os.path.isdir(new):
                    move_tree(new, old, log)

    log("  还原注册表值…")
    failed = 0
    for row in data.get("registry", []):
        try:
            current, _ = read_reg(row["hive"], row["path"], row["name"])
            if current == row["value"]:
                continue
            write_reg(row["hive"], row["path"], row["name"], row["value"], row["type"])
        except OSError as e:
            failed += 1
            log(f"  ! 还原失败 {row['path']}\\{row['name']}: {e}")
    broadcast_change()
    log(f"  注册表还原完成，失败 {failed} 项")


# --------------------------------------------------------------------------- #
# 高层动作（供 UI 调用）
# --------------------------------------------------------------------------- #
def apply_shell_folder(folder: KnownFolder, dst: str, move_files: bool, log=None) -> dict:
    log = log or (lambda m: None)
    src = folder.current()
    dst = os.path.abspath(os.path.expandvars(dst.strip().strip('"')))
    ensure_dir(dst)
    if move_files and os.path.isdir(src) and norm(src) != norm(dst):
        r = move_tree(src, dst, log)
        if not r.ok:
            raise MoveFailed(f"{folder.label} 搬运未完成：{r.detail}", r.leftovers)
    set_known_path(folder.guid, dst)
    broadcast_change()
    log(f"  {folder.label}: 已指向 {dst}")
    return {"kind": "shellfolder", "key": folder.key, "label": folder.label,
            "guid": folder.guid, "old": src, "new": dst, "moved": move_files}


class MoveFailed(Exception):
    def __init__(self, message, leftovers=None):
        super().__init__(message)
        self.leftovers = leftovers or []


def apply_junction_move(label: str, src: str, dst: str, log=None) -> dict:
    """把 src 的内容搬到 dst，再在 src 位置建指向 dst 的联接。"""
    log = log or (lambda m: None)
    src = os.path.abspath(os.path.expandvars(src))
    dst = os.path.abspath(os.path.expandvars(dst))
    if norm(src) == norm(dst):
        raise ValueError("目标与源相同")

    existed = os.path.isdir(src) and not is_reparse_point(src)
    ensure_dir(dst)
    if existed:
        r = move_tree(src, dst, log)
        if not r.ok:
            raise MoveFailed(f"{label} 搬运未完成：{r.detail}", r.leftovers)
    if os.path.isdir(src) and not is_reparse_point(src):
        try:
            os.rmdir(src)
        except OSError as e:
            raise MoveFailed(f"{label} 源目录无法清空: {e}")
    if not os.path.isdir(src):
        ensure_dir(src)
    create_junction(src, dst)
    log(f"  {label}: {src} => {dst}（目录联接已建立）")
    return {"kind": "junction", "label": label, "old": src, "new": dst}


def apply_temp_dir(new_dir: str, move_files: bool, log=None) -> dict:
    log = log or (lambda m: None)
    old = get_temp_dir()
    new_dir = os.path.abspath(os.path.expandvars(new_dir.strip().strip('"')))
    ensure_dir(new_dir)
    # Temp 里有大量被占用的文件，默认只改环境变量，不搬
    if move_files and os.path.isdir(old) and norm(old) != norm(new_dir):
        r = move_tree(old, new_dir, log)
        if not r.ok:
            log(f"  ! 部分临时文件被占用，已跳过：{r.detail}")
        ensure_dir(old)
    set_env_var("TEMP", new_dir)
    set_env_var("TMP", new_dir)
    os.environ["TEMP"] = os.environ["TMP"] = new_dir
    log(f"  临时目录: {old}  ->  {new_dir}（新开的程序立即生效）")
    return {"kind": "env", "label": "临时目录", "old": old, "new": new_dir}


def appdata_current() -> tuple[str, str]:
    local = os.path.expandvars(get_env_var("LOCALAPPDATA") or os.environ.get("LOCALAPPDATA", ""))
    roaming = os.path.expandvars(get_env_var("APPDATA") or os.environ.get("APPDATA", ""))
    if not roaming:
        roaming = os.path.join(os.environ.get("USERPROFILE", ""), "AppData", "Roaming")
    return local, roaming


def apply_appdata_env_only(local: str, roaming: str, log=None) -> dict:
    """只改环境变量：半生效，必须重新登录。见 EFFECTS['appdata_root']。"""
    log = log or (lambda m: None)
    old_local, old_roam = appdata_current()
    local = os.path.abspath(os.path.expandvars(local.strip().strip('"')))
    roaming = os.path.abspath(os.path.expandvars(roaming.strip().strip('"')))
    ensure_dir(local)
    ensure_dir(roaming)
    set_env_var("LOCALAPPDATA", local)
    set_env_var("APPDATA", roaming)
    log(f"  LOCALAPPDATA: {old_local} -> {local}")
    log(f"  APPDATA: {old_roam} -> {roaming}")
    log("  ! 只改环境变量，Windows 自身仍按注册表走原路径，需要重新登录，且会分裂成两处。")
    return {"kind": "env", "label": "AppData 环境变量",
            "old": f"{old_local}|{old_roam}", "new": f"{local}|{roaming}"}


APPDATA_SCRIPT_TEMPLATE = r"""@echo off
chcp 65001 >nul
setlocal
title PathMover - AppData 离线替换

REM ===================================================================
REM  由 PathMover 生成，用于把 AppData 整体换成指向新盘的目录联接。
REM
REM  为什么不能在线做：登录状态下 %SRC% 被大量进程占用，
REM  重命名/删除必然失败，硬来会造成登录异常。
REM
REM  正确姿势（任选其一）：
REM    1. 建一个临时管理员账户，登录它，对目标用户的目录操作；
REM    2. 进 WinPE / 恢复环境，加载目标系统盘后操作；
REM    3. 登录前用「命令提示符（管理员）」在本机执行（离线 profile）。
REM
REM  执行前请确认：目标用户已注销、目标盘剩余空间够、盘符固定。
REM ===================================================================

set "SRC={src}"
set "DST={dst}"

echo.
echo   源   : %SRC%
echo   目标 : %DST%
echo.
pause
if not exist "%SRC%" ( echo [错误] 源目录不存在 & pause & exit /b 1 )

echo [1/3] 复制（保留 ACL，跳过联接，避免递归）...
robocopy "%SRC%" "%DST%" /E /COPYALL /XJ /R:1 /W:1 /NFL /NDL /NP
if errorlevel 8 ( echo [错误] 复制失败，未做任何改动。 & pause & exit /b 1 )

echo [2/3] 重命名原目录为 .old ...
ren "%SRC%" "{oldname}.old"
if errorlevel 1 ( echo [错误] 重命名失败：目录仍被占用，请确认该用户已注销。 & pause & exit /b 1 )

echo [3/3] 建立目录联接...
mklink /J "%SRC%" "%DST%"
if errorlevel 1 (
    echo [错误] 联接创建失败，正在回滚...
    ren "%SRC%.old" "{oldname}"
    pause & exit /b 1
)

echo.
echo   完成。验证：读一下 %SRC% 里的文件是否正常。
echo   确认无误后可以删除 %SRC%.old
echo   （注意：AppData 里有正在使用的配置，建议先保留几天）
echo.
pause
"""


def write_appdata_offline_script(local_dst: str, roaming_dst: str, out_dir: str | None = None) -> list[str]:
    """生成两份离线替换脚本（Local / Roaming），返回文件路径。"""
    out_dir = ensure_dir(out_dir or BACKUP_DIR)
    paths = []
    pairs = [
        ("AppData-Local", os.path.expandvars(os.environ.get("LOCALAPPDATA", "")),
         os.path.abspath(os.path.expandvars(local_dst))),
        ("AppData-Roaming", os.path.expandvars(os.environ.get("APPDATA", "")),
         os.path.abspath(os.path.expandvars(roaming_dst))),
    ]
    for name, src, dst in pairs:
        if not src:
            continue
        body = APPDATA_SCRIPT_TEMPLATE.format(
            src=src, dst=dst, oldname=os.path.basename(src.rstrip("\\"))
        )
        path = os.path.join(out_dir, f"{name}-offline.cmd")
        with open(path, "w", encoding="utf-8-sig", newline="\r\n") as f:
            f.write(body)
        paths.append(path)
    return paths


def apply_program_dirs(x64: str, x86: str, log=None) -> dict:
    log = log or (lambda m: None)
    before = get_program_dirs()
    set_program_dirs(x64, x86)
    log(f"  ProgramFilesDir: {before.get('ProgramFilesDir')} -> {os.path.abspath(x64)}")
    log(f"  ProgramFilesDir (x86): {before.get('ProgramFilesDir (x86)')} -> {os.path.abspath(x86)}")
    log("  仅影响之后新安装的程序，已装程序不会移动；Windows 更新类组件可能受影响。")
    return {"kind": "programfiles", "label": "程序安装默认目录",
            "old": before.get("ProgramFilesDir"), "new": os.path.abspath(x64)}


# --------------------------------------------------------------------------- #
# 自检（命令行运行 python engine.py）
# --------------------------------------------------------------------------- #
def _selftest() -> int:
    print("=" * 68)
    print(f"{APP_NAME} {APP_VERSION} 自检")
    print("=" * 68)
    print(f"管理员权限 : {is_admin()}")
    print(f"备份目录   : {BACKUP_DIR}")
    print(f"robocopy   : {'存在' if os.path.isfile(ROBOCOPY) else '缺失!'}")

    print("\n[用户文件夹]")
    for kf in KNOWN_FOLDERS:
        p = kf.current()
        print(f"  {kf.label:<6} {p}   {'✓' if os.path.isdir(p) else '✗ 不存在'}")

    print("\n[临时目录]")
    print(f"  TEMP = {get_env_var('TEMP')}  ->  {get_temp_dir()}")
    print(f"  TMP  = {get_env_var('TMP')}")

    print("\n[程序安装目录]")
    for k, v in get_program_dirs().items():
        print(f"  {k:<22} {v}")

    print("\n[磁盘]")
    for d in "CDEFG":
        root = f"{d}:\\"
        if os.path.isdir(root):
            print(f"  {root} 类型={drive_type(root):<9} NTFS={is_ntfs(root)!s:<5} "
                  f"剩余={human_size(free_space(root))}")

    print("\n[联接测试]")
    base = os.path.join(os.environ.get("TEMP", "."), "pathmover_selftest")
    try:
        shutil.rmtree(base, ignore_errors=True)
        src, dst = os.path.join(base, "src"), os.path.join(base, "dst")
        ensure_dir(src)
        ensure_dir(dst)
        with open(os.path.join(src, "hello.txt"), "w", encoding="utf-8") as f:
            f.write("hi")
        r = move_tree(src, dst, print)
        print(f"  搬运结果 ok={r.ok} code={r.code} 剩余={r.leftovers}")
        ensure_dir(src)
        create_junction(src, dst)
        print(f"  读回联接目标: {read_junction(src)}")
        got = open(os.path.join(src, "hello.txt"), encoding="utf-8").read()
        print(f"  通过联接读取文件: {got!r}")
        remove_junction(src)
        print(f"  删除联接后目标仍在: {os.path.isfile(os.path.join(dst, 'hello.txt'))}")
    except Exception as e:
        print(f"  ✗ 联接测试失败: {e!r}")
        return 1
    finally:
        shutil.rmtree(base, ignore_errors=True)

    print("\n自检结束。")
    return 0


if __name__ == "__main__":
    sys.exit(_selftest())
