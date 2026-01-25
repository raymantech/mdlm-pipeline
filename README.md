# MDLM 1.0 (Merged: Frontend + Backend)

This folder contains BOTH:
- `frontend/` : static dashboard served at https://mdlm.hawnlink.cn/
- `backend/`  : data pipeline that outputs `frontend/data/merged_events_latest.json`

## One-command run (local)

```bash
bash run.sh
```

It will:
1) fetch charts into SQLite (`backend/charts.db`)
2) analyze events
3) merge events
4) export JSON to: `frontend/data/merged_events_latest.json`

Then you only upload/replace that JSON on your server.

## Requirements

- Python 3.10+
- Dependencies:
  ```bash
  pip install -r backend/requirements.txt
  ```

Optional:
- `.env` can be placed at project root (`./.env`) or `backend/.env`.
- `python-dotenv` is NOT required; we parse `.env` ourselves.

## Move the folder anywhere

All scripts resolve paths from their own location, so you can move/rename this whole folder freely and it will still work.
