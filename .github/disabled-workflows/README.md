# Archived deployment workflow

`main_ai-triage-agentic-system.yml` is retained as source history. It must remain
outside `.github/workflows`: it deploys to the same app on every main push without
the tests, React build, package checks, notification safety checks, or deployment
concurrency used by `../workflows/deploy-azure.yml`. Two independent deployments
could race and overwrite the verified release.

Use `../workflows/deploy-azure.yml` as the single application deployment workflow.
