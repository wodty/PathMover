@echo off
rem ============================================================
rem  PathMover 打包脚本：生成 dist\PathMover.exe（单文件、无控制台）
rem  依赖：pip install pyinstaller
rem ============================================================
setlocal
cd /d "%~dp0"

python -m PyInstaller --noconfirm --clean --onefile --windowed ^
  --name PathMover ^
  --exclude-module unittest ^
  --exclude-module pydoc_data ^
  main.py

if errorlevel 1 (
  echo.
  echo [失败] 请先安装 PyInstaller：python -m pip install pyinstaller
  pause
  exit /b 1
)

echo.
echo [完成] dist\PathMover.exe
echo 提示：希望每次启动都弹 UAC，把上面的参数换成 --uac-admin；
echo       默认是普通权限运行，需要时程序内点「以管理员重启」。
echo.
pause
