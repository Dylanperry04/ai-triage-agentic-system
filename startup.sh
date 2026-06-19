#!/bin/bash
set -e

cd /home/site/wwwroot

python -m streamlit run frontend/app.py \
  --server.address 0.0.0.0 \
  --server.port ${PORT:-8000}
