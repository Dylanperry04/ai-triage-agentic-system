# ALTER React frontend

HSE-styled React UI for the ALTER ED triage backend. Served in production by
the FastAPI app itself (`app/main.py` serves `frontend-react/dist` at `/`), so
one Azure App Service runs the whole system. Replaces the retired Streamlit
presentation service (`frontend/`, kept for reference).

## Development
```bash
cd frontend-react
npm install
npm run dev        # http://localhost:5173, proxies API calls to 127.0.0.1:8000
```
Run the backend beside it: `bash startup-backend.sh` (with the demo env from
`infrastructure/azure_deploy.md`).

## Production build
```bash
npm run build      # writes frontend-react/dist — COMMIT this folder
```
`dist/` is committed on purpose: Azure App Service (Oryx/Python) then needs no
Node toolchain. The GitHub Actions workflow also rebuilds it on every push.

## How it talks to the backend
- Same-origin `fetch` — no CORS needed for the built-in UI.
- `GET /auth/session` drives sign-in, navigation (`visible_tabs`) and
  permissions; the UI renders only what the backend authorises, and the
  backend enforces every action again server-side.
- In demo profiles the selected role/persona is sent as `X-Demo-Role` /
  `X-Demo-User` (ASCII-folded). Real-auth profiles ignore both headers.
