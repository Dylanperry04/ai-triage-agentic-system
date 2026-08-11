"""Smoke-test AutoGen imports for the app runtime venv.

Run this in the app venv, not the ML training venv:
    python scripts/check_autogen_imports.py
"""
from __future__ import annotations

import importlib
import sys


REQUIRED_MODULES = (
    "autogen_core",
    "autogen_agentchat",
)


def main() -> int:
    missing = []
    for module in REQUIRED_MODULES:
        try:
            importlib.import_module(module)
        except Exception as exc:
            missing.append(f"{module}: {type(exc).__name__}: {exc}")
    if missing:
        sys.stderr.write(
            "AutoGen unavailable - likely dependency conflict. Use the app "
            "runtime venv, not the ML training venv.\n"
        )
        for item in missing:
            sys.stderr.write(f"- {item}\n")
        return 1
    print("AutoGen imports OK: autogen_core and autogen_agentchat are available.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
