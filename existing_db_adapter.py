import argparse
import json
from pathlib import Path

REQUIRED = {
    'proposals': ['提案ID', '管理ID', 'アカウントURL', '案件'],
    'posts': ['メディア管理ID', 'メディアURL', '案件名', 'notionURL'],
    'onead': ['メディア管理ID', 'メディアURL', '案件名', '社内：CV'],
    'project_daily': ['date', 'project', 'conversions'],
}


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def validate_rows(name, rows):
    if not isinstance(rows, list):
        raise ValueError(f'{name} must be a list')
    missing = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError(f'{name} rows must be objects')
        for key in REQUIRED[name]:
            if key not in row:
                missing.add(key)
    return sorted(missing)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--input', required=True, help='JSON generated outside the public repository')
    ap.add_argument('--output', default='existing_db_normalized.json')
    args = ap.parse_args()

    data = load(args.input)
    normalized = {}
    errors = {}
    for name in REQUIRED:
        rows = data.get(name, [])
        missing = validate_rows(name, rows)
        if missing:
            errors[name] = missing
        normalized[name] = rows

    if errors:
        raise SystemExit('schema mismatch: ' + json.dumps(errors, ensure_ascii=False))

    payload = {
        'mode': 'existing_operational_db_adapter',
        'data_policy': 'raw operational rows must be supplied at runtime and must not be committed to this public repository',
        'sources': {
            'proposals': 'IFマスターシート / 投稿提案ログ',
            'posts': 'IFマスターシート / 投稿実績ログ',
            'onead': '【SAd】AI KPI DB / onead_raw',
            'project_daily': '【SAd】AI KPI DB / project_cv_summary',
        },
        **normalized,
    }
    Path(args.output).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    print('adapter validation=OK')
    for name in REQUIRED:
        print(name, len(normalized[name]))


if __name__ == '__main__':
    main()
