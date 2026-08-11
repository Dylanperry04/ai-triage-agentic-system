#!/usr/bin/env bash
# DEPRECATED. The current architecture is a SINGLE service: the FastAPI backend
# serves the built React UI. Use startup-backend.sh. The retired two-service
# Streamlit shape (startup-backend.sh + startup-frontend.sh, selected
# in the image via SERVICE_ROLE. Defaulting to the backend here.
exec sh startup-backend.sh
