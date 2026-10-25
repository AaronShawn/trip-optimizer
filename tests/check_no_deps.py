#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""校验 scripts/ 下只用标准库，防止悄悄引入第三方依赖。

CI 用；本地也可跑：python tests/check_no_deps.py
"""
import ast
import pathlib
import sys

# 本项目模块
LOCAL = {"geo", "sources", "trip_opt", "pipeline", "render"}

# 标准库白名单（显式列出，避免依赖 sys.stdlib_module_names，
# 那个属性 Python 3.10 才有，而项目要支持 3.9）
STDLIB = {
    "abc", "argparse", "ast", "asyncio", "base64", "bisect", "calendar", "cmath",
    "codecs", "collections", "concurrent", "contextlib", "copy", "csv", "dataclasses",
    "datetime", "decimal", "difflib", "enum", "errno", "functools", "gc", "getpass",
    "glob", "gzip", "hashlib", "heapq", "hmac", "html", "http", "importlib", "inspect",
    "io", "itertools", "json", "logging", "math", "mimetypes", "numbers", "operator",
    "os", "pathlib", "pickle", "platform", "pprint", "queue", "random", "re", "secrets",
    "select", "shutil", "signal", "socket", "sqlite3", "ssl", "stat", "string",
    "struct", "subprocess", "sys", "tempfile", "textwrap", "threading", "time",
    "traceback", "types", "typing", "unicodedata", "unittest", "urllib", "uuid",
    "warnings", "weakref", "xml", "zipfile", "zlib",
}


def main() -> int:
    root = pathlib.Path(__file__).resolve().parent.parent
    bad = []
    checked = 0

    for path in sorted((root / "scripts").glob("*.py")):
        checked += 1
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            mods = []
            if isinstance(node, ast.Import):
                mods = [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.level:      # 相对导入，算本地
                    continue
                if node.module:
                    mods = [node.module.split(".")[0]]
            for m in mods:
                if m not in STDLIB and m not in LOCAL:
                    bad.append(f"{path.name}:{node.lineno}  第三方依赖 '{m}'")

    if bad:
        print("发现不允许的导入：")
        for b in bad:
            print("  " + b)
        print(f"\n提示：本项目坚持零第三方依赖。若确实需要，请先开 issue 讨论。")
        return 1

    print(f"OK: 检查了 {checked} 个文件，全部只用标准库")
    return 0


if __name__ == "__main__":
    sys.exit(main())
