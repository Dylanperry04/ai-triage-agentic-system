from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


REQUIRED_PATHS = [
    "app/main.py",
    "app/api/health_routes.py",
    "app/api/triage_routes.py",
    "app/api/audit_routes.py",
    "app/api/review_routes.py",
    "app/api/governance_routes.py",
    "frontend/streamlit_app.py",
    "scripts/demo_readiness_check.py",
    "scripts/export_responsible_ai_evidence.py",
    "scripts/create_demo_release_package.py",
    "requirements.txt",
    "startup.sh",
    "data/processed/triage_cases_sample.jsonl",
    "data/processed/dataset_audit_report.json",
    "data/processed/missing_triage_inputs_report.json",
    "data/processed/responsible_ai_evidence_package.json",
]


REQUIRED_REQUIREMENTS = [
    "fastapi",
    "uvicorn",
    "gunicorn",
    "pydantic",
    "orjson",
    "pytest",
]


def check_path(relative_path: str) -> bool:
    return (PROJECT_ROOT / relative_path).exists()


def read_requirements() -> str:
    path = PROJECT_ROOT / "requirements.txt"

    if not path.exists():
        return ""

    return path.read_text(encoding="utf-8").lower()


def print_check(name: str, passed: bool, detail: str = ""):
    status = "PASS" if passed else "FAIL"
    print(f"[{status}] {name}")
    if detail:
        print(f"       {detail}")


def main():
    print("\nAZURE DEPLOYMENT PREFLIGHT CHECK")
    print("=" * 45)

    all_passed = True

    for relative_path in REQUIRED_PATHS:
        exists = check_path(relative_path)
        print_check(relative_path, exists)
        all_passed = all_passed and exists

    requirements_text = read_requirements()

    for package_name in REQUIRED_REQUIREMENTS:
        exists = package_name in requirements_text
        print_check(f"requirements contains {package_name}", exists)
        all_passed = all_passed and exists

    startup_path = PROJECT_ROOT / "startup.sh"

    if startup_path.exists():
        startup_text = startup_path.read_text(encoding="utf-8")
        startup_ok = "gunicorn app.main:app" in startup_text
        print_check(
            "startup.sh launches FastAPI app.main:app",
            startup_ok,
            "Expected command to include: gunicorn app.main:app",
        )
        all_passed = all_passed and startup_ok

    print("=" * 45)

    if all_passed:
        print("Azure preflight check passed.")
        print("Project is ready for Azure deployment preparation.")
    else:
        print("Azure preflight check failed.")
        print("Fix the failed items before deployment.")

    print()


if __name__ == "__main__":
    main()