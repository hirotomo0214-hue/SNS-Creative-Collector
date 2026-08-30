import argparse
import json
import os
from pathlib import Path

from google.oauth2 import service_account
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]

IF_MASTER_RANGES = {
    "proposals": "'投稿提案ログ'!A:O",
    "posts": "'投稿実績ログ'!A:AB",
}

AI_KPI_RANGES = {
    "onead": "onead_raw!A:U",
    "project_daily": "project_cv_summary!A:I",
}


def _credentials_from_env():
    raw = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
    if not raw:
        raise SystemExit("GOOGLE_SERVICE_ACCOUNT_JSON is required for live mode")
    try:
        info = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SystemExit("GOOGLE_SERVICE_ACCOUNT_JSON is not valid JSON") from exc
    return service_account.Credentials.from_service_account_info(info, scopes=SCOPES)


def _rows_to_dicts(values):
    if not values:
        return []
    headers = [str(v).strip() for v in values[0]]
    rows = []
    for raw in values[1:]:
        if not any(str(v).strip() for v in raw):
            continue
        padded = list(raw) + [""] * max(0, len(headers) - len(raw))
        rows.append({headers[i]: padded[i] for i in range(len(headers)) if headers[i]})
    return rows


def _read_range(service, spreadsheet_id, a1_range):
    result = (
        service.spreadsheets()
        .values()
        .get(
            spreadsheetId=spreadsheet_id,
            range=a1_range,
            valueRenderOption="UNFORMATTED_VALUE",
            dateTimeRenderOption="FORMATTED_STRING",
        )
        .execute()
    )
    return _rows_to_dicts(result.get("values", []))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--if-master-id", default=os.environ.get("IF_MASTER_SPREADSHEET_ID", ""))
    parser.add_argument("--ai-kpi-id", default=os.environ.get("AI_KPI_SPREADSHEET_ID", ""))
    parser.add_argument("--output", default="runtime_data/existing_db_input.json")
    args = parser.parse_args()

    if not args.if_master_id:
        raise SystemExit("IF_MASTER_SPREADSHEET_ID is required")
    if not args.ai_kpi_id:
        raise SystemExit("AI_KPI_SPREADSHEET_ID is required")

    creds = _credentials_from_env()
    service = build("sheets", "v4", credentials=creds, cache_discovery=False)

    payload = {
        "proposals": _read_range(service, args.if_master_id, IF_MASTER_RANGES["proposals"]),
        "posts": _read_range(service, args.if_master_id, IF_MASTER_RANGES["posts"]),
        "onead": _read_range(service, args.ai_kpi_id, AI_KPI_RANGES["onead"]),
        "project_daily": _read_range(service, args.ai_kpi_id, AI_KPI_RANGES["project_daily"]),
    }

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    print("live sheets fetch=OK")
    for name, rows in payload.items():
        print(name, len(rows))


if __name__ == "__main__":
    main()
