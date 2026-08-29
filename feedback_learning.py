import argparse
import json
from collections import defaultdict
from pathlib import Path


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def normalize_list(value):
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v) for v in value if str(v).strip()]
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, list):
                return [str(v) for v in parsed if str(v).strip()]
        except Exception:
            pass
        return [value] if value.strip() else []
    return [str(value)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--input', required=True)
    ap.add_argument('--output', default='feedback_learning_report.json')
    args = ap.parse_args()

    rows = load(args.input)
    if isinstance(rows, dict):
        rows = rows.get('results', rows.get('rows', []))

    usable = []
    skipped = []
    for row in rows:
        actual_url = row.get('actual_post_url')
        cv = row.get('cv')
        if not actual_url or cv is None:
            skipped.append({'proposal_id': row.get('proposal_id'), 'reason': 'missing_actual_or_cv'})
            continue
        actual_structure = row.get('actual_structure')
        actual_appeals = normalize_list(row.get('actual_appeals'))
        usable.append({
            'proposal_id': row.get('proposal_id'),
            'project': row.get('project'),
            'if_account': row.get('if_account'),
            'proposal_direction': row.get('proposal_direction'),
            'adopted': row.get('adopted'),
            'actual_post_url': actual_url,
            'actual_structure': actual_structure,
            'actual_appeals': actual_appeals,
            'cv': float(cv),
        })

    stats = defaultdict(lambda: {'posts': 0, 'cv_sum': 0.0, 'accounts': set(), 'examples': []})
    for row in usable:
        signals = []
        if row.get('actual_structure'):
            signals.append(('structure', row['actual_structure']))
        for appeal in row.get('actual_appeals', []):
            signals.append(('appeal', appeal))
        for kind, name in signals:
            key = f'{kind}:{name}'
            s = stats[key]
            s['posts'] += 1
            s['cv_sum'] += row['cv']
            if row.get('if_account'):
                s['accounts'].add(row['if_account'])
            if len(s['examples']) < 5:
                s['examples'].append({'proposal_id': row.get('proposal_id'), 'url': row['actual_post_url'], 'cv': row['cv']})

    signals = []
    for key, s in stats.items():
        kind, name = key.split(':', 1)
        avg_cv = s['cv_sum'] / s['posts'] if s['posts'] else 0
        confidence = 'insufficient'
        if s['posts'] >= 5 and len(s['accounts']) >= 3:
            confidence = 'medium'
        if s['posts'] >= 10 and len(s['accounts']) >= 5:
            confidence = 'higher'
        signals.append({
            'kind': kind,
            'name': name,
            'posts': s['posts'],
            'accounts': len(s['accounts']),
            'cv_sum': round(s['cv_sum'], 2),
            'avg_cv_per_post': round(avg_cv, 3),
            'confidence': confidence,
            'examples': s['examples'],
        })
    signals.sort(key=lambda x: (x['avg_cv_per_post'], x['posts']), reverse=True)

    payload = {
        'mode': 'actual_post_cv_feedback',
        'guardrail': '提案内容ではなく実投稿の構成・訴求だけを成果学習に使用する。低母数では因果を断定しない。',
        'input_rows': len(rows),
        'usable_rows': len(usable),
        'skipped_rows': skipped,
        'signals': signals,
        'promotion_rule': {
            'market_signal_only': 'initial_signal',
            'actual_posts_min': 5,
            'accounts_min': 3,
            'meaning': '5投稿/3アカウント未満は成果型として昇格させず参考扱い',
        },
    }
    Path(args.output).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    print('input_rows=', payload['input_rows'])
    print('usable_rows=', payload['usable_rows'])
    print('signals=', len(signals))
    for s in signals[:10]:
        print(s['kind'], s['name'], 'posts=', s['posts'], 'accounts=', s['accounts'], 'avg_cv=', s['avg_cv_per_post'], 'confidence=', s['confidence'])


if __name__ == '__main__':
    main()
