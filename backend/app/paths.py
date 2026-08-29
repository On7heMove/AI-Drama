"""统一数据路径解析：源码 / standalone / onefile 三种环境自动适配。

- app_root()：只读资源根（config/ library/）
    - 源码：仓库根（app/paths.py 的 parents[2]）
    - standalone：exe 所在 dist 的上级（模块 __file__ 在 <dist>\app\ 下）
    - onefile：解压临时目录（模块 __file__ 在 <tmp>\onefile_xxx\app\ 下，config/library 在解压目录根）
- runtime_dir()：可写目录（data/ archive/ .env）
    - 优先环境变量 AIDRAMA_RUNTIME（启动脚本指向交付目录，保证 onefile 下存档/密钥落盘到真实目录）
    - onefile/standalone：sys.executable 所在目录（onefile 下 = 原始 exe 目录）
    - 源码：backend/
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def app_root() -> Path:
    here = Path(__file__).resolve()
    if "__compiled__" in globals() or getattr(sys, "frozen", False):
        # Nuitka(onefile/standalone) 或 PyInstaller(onefile/onedir) 打包态：
        # - Nuitka onefile: here=<tmp>\onefile_xxx\app\paths.py → parents[1]=解压目录（含 config）
        # - Nuitka standalone: here=<dist>\app\paths.py → parents[1]=dist（无 config），parents[2]=交付目录
        # - PyInstaller onefile: sys._MEIPASS=解压目录（含 config）
        # - PyInstaller onedir: here=<dist>\app\paths.py → parents[1]=dist（无 config），parents[2]=交付目录
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass and (Path(meipass) / "config").exists():
            return Path(meipass)
        cand = here.parents[1]
        if (cand / "config").exists():
            return cand
        return here.parents[2]
    return here.parents[2]


def data_root() -> Path:
    """只读数据根（config/ library/）。"""
    return app_root()


def runtime_dir() -> Path:
    """可写数据根（data/ archive/ .env）。"""
    env = os.environ.get("AIDRAMA_RUNTIME")
    if env:
        return Path(env).resolve()
    if "__compiled__" in globals() or getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]
