# Azure deployment notes

For a student subscription, deploy the FastAPI backend as a container to Azure Container Apps.

Suggested order:

1. Build locally.
2. Run tests.
3. Build Docker image.
4. Push to Azure Container Registry.
5. Deploy to Azure Container Apps.
6. Add managed identity and monitoring later.

Do not upload real UHL patient data to a student subscription unless governance, storage, approval, and data protection controls are explicitly approved.
