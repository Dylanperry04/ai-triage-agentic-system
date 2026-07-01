#!/usr/bin/env bash
# DEPRECATED single-service startup. The final architecture is two services:
# use startup-backend.sh (FastAPI) and startup-frontend.sh (Streamlit), selected
# in the image via SERVICE_ROLE. Defaulting to the backend here.
exec sh startup-backend.sh
