"""Static pickle scanner: reads opcodes, never unpickles.

Pickle files run code on load through GLOBAL/STACK_GLOBAL + REDUCE (the
attack behind malicious model uploads on public model hubs). We list every
imported callable and flag the dangerous ones.
"""

from __future__ import annotations

import pickletools
from pathlib import Path

DANGEROUS_MODULES = {"os", "posix", "nt", "subprocess", "builtins", "__builtin__", "sys", "socket", "shutil",
                     "importlib", "runpy", "pty", "webbrowser", "requests", "urllib", "httpx", "ctypes", "marshal"}
DANGEROUS_NAMES = {"eval", "exec", "compile", "open", "__import__", "getattr", "system", "popen", "Popen", "call",
                   "check_output", "run", "spawn", "execv", "execve"}


def scan_pickle(path: str | Path) -> dict:
    data = Path(path).read_bytes()
    imports: list[str] = []
    strings: list[str] = []
    try:
        for opcode, arg, _pos in pickletools.genops(data):
            if opcode.name in ("SHORT_BINUNICODE", "BINUNICODE", "UNICODE", "BINUNICODE8"):
                strings.append(str(arg))
            elif opcode.name == "GLOBAL":
                module, _, name = str(arg).partition(" ")
                imports.append(f"{module}.{name}")
            elif opcode.name == "STACK_GLOBAL" and len(strings) >= 2:
                imports.append(f"{strings[-2]}.{strings[-1]}")
    except Exception as err:  # malformed pickle is itself suspicious
        return {"ok": False, "imports": imports, "dangerous": [], "error": f"unparseable pickle: {err}"}
    dangerous = [i for i in imports if i.split(".")[0] in DANGEROUS_MODULES or i.rsplit(".", 1)[-1] in DANGEROUS_NAMES]
    return {"ok": not dangerous, "imports": imports, "dangerous": dangerous, "error": None}
