"""
Checks whether Azure OpenAI environment variables are present.

Loads variables from the local .env file first.

Do not commit real keys to GitHub.
Do not print real keys to terminal.
"""

from pathlib import Path
import os

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = PROJECT_ROOT / ".env"

REQUIRED = [
    "AZURE_OPENAI_ENDPOINT",
    "AZURE_OPENAI_API_KEY",
    "AZURE_OPENAI_DEPLOYMENT",
    "AZURE_OPENAI_API_VERSION",
    "AZURE_OPENAI_MODEL",
]


def mask_value(name: str, value: str) -> str:
    if name == "AZURE_OPENAI_API_KEY":
        return "***KEY_PRESENT_BUT_HIDDEN***"

    if len(value) <= 12:
        return "***"

    return value[:8] + "..." + value[-6:]


def main():
    load_dotenv(dotenv_path=ENV_PATH)

    print("\nAUTOGEN / AZURE OPENAI ENV CHECK")
    print("=" * 40)
    print(f"Project root: {PROJECT_ROOT}")
    print(f".env path: {ENV_PATH}")
    print(f".env found: {ENV_PATH.exists()}")
    print("=" * 40)

    missing = []

    for name in REQUIRED:
        value = os.getenv(name)

        if value:
            print(f"[FOUND] {name}={mask_value(name, value)}")
        else:
            print(f"[MISSING] {name}")
            missing.append(name)

    print("=" * 40)

    if missing:
        print("Missing Azure OpenAI config. AutoGen imports can work, but model calls will not work yet.")
        return 1

    print("Azure OpenAI config found. Ready for safe model smoke test.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())