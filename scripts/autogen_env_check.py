"""
Checks whether Azure OpenAI environment variables are present.

Do not commit real keys to GitHub.
Use a local .env file or Azure App Service application settings later.
"""

import os


REQUIRED = [
    "AZURE_OPENAI_ENDPOINT",
    "AZURE_OPENAI_API_KEY",
    "AZURE_OPENAI_DEPLOYMENT",
    "AZURE_OPENAI_API_VERSION",
    "AZURE_OPENAI_MODEL",
]


def main():
    print("\nAUTOGEN / AZURE OPENAI ENV CHECK")
    print("=" * 40)

    missing = []

    for name in REQUIRED:
        value = os.getenv(name)
        if value:
            masked = value[:6] + "..." if len(value) > 8 else "***"
            print(f"[FOUND] {name}={masked}")
        else:
            print(f"[MISSING] {name}")
            missing.append(name)

    print("=" * 40)

    if missing:
        print("Missing Azure OpenAI config. AutoGen imports can work, but model calls will not work yet.")
    else:
        print("Azure OpenAI config found. Ready for AutoGen model test.")

    print()


if __name__ == "__main__":
    main()