# -*- coding: utf-8 -*-
"""pytest 收集：把 backend 根加入 sys.path，使 app.* 可导入。"""
import os, sys

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)
