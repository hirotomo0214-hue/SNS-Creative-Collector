import argparse
import json
from pathlib import Path
from urllib.parse import urlparse


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def norm_url(url):
    if not url:
        return ''
    try:
        p = urlparse(str(url).strip())
        host = p.netloc.lower().replace('www.', '')
        path = p.path.rstrip('/').lower()
        return f'{host}{path}'
    except Exception:
        return str(url).strip().lower().rstrip('/')


def norm_project(value):
    value = (value or '').strip().lower()
    aliases = {
        'teaflex(ティーフレックス)': 'teaflex',
        'ティーフレックス スリムクレンズグリーンティー': 'teaflex',
        'ティーフレックス': 'teaflex',
        'moon up(ムーンアップ)ショーツ': 'ムーンアップショーツ',
        'ムーンアップショーツ': 'ムーンアップショーツ',
    }
    return aliases.get(value, value)


def index(rows, key_fn):
    out = {}
    for row in rows:
        key = key_fn(row)
        if not key or any(not part for part in key if isinstance(key, tuple)):
            continue
        out.setdefault(key, []).append(row)
    return out


def first(row, *keys):
    for k in keys:
        v = row.get(k)
        if v not in (None, ''):
            return v
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--input', required=True)
    ap.add_argument('--output', default='outcome_join_report.json')
    args = ap.parse_args()

    data = load(args.input)
    proposals = data.get('proposals', [])
    posts = data.get('posts', [])
    onead = data.get('onead', [])
    project_daily = data.get('project_daily', [])

    proposal_idx = index(proposals, lambda r: (str(first(r, '管理ID', 'management_id') or '').strip(), norm_project(first(r, '案件', 'project'))))
    post_idx = index(posts, lambda r: (str(first(r, 'メディア管理ID', 'management_id') or '').strip(), norm_project(first(r, '案件名', 'project'))))
    onead_idx = index(onead, lambda r: (str(first(r, 'メディア管理ID', 'management_id') or '').strip(), norm_project(first(r, '案件名', 'project'))))

    joins = []
    rejected = []

    keys = sorted(set(proposal_idx) | set(post_idx) | set(onead_idx))
    for key in keys:
        ps = proposal_idx.get(key, [])
        actuals = post_idx.get(key, [])
        cvs = onead_idx.get(key, [])
        if not ps:
            rejected.append({'key': key, 'reason': 'no_proposal'})
            continue
        if not actuals:
            rejected.append({'key': key, 'reason': 'no_actual_post_record'})
            continue
        if not cvs:
            rejected.append({'key': key, 'reason': 'no_account_level_cv'})
            continue

        # Account/project-level CV is valid context for the influencer+project pair,
        # but never claim it belongs to one specific post unless the source has a post ID.
        cv_sum = 0.0
        for r in cvs:
            raw = first(r, '社内：CV', 'cv', 'conversions')
            try:
                cv_sum += float(raw or 0)
            except Exception:
                pass

        joins.append({
            'management_id': key[0],
            'project_key': key[1],
            'proposal_ids': [first(r, '提案ID', 'proposal_id') for r in ps if first(r, '提案ID', 'proposal_id')],
            'account_url': first(actuals[0], 'メディアURL', 'account_url') or first(ps[0], 'アカウントURL', 'account_url'),
            'notion_urls': [first(r, 'notionURL', 'notion_url') for r in actuals if first(r, 'notionURL', 'notion_url')],
            'account_project_cv': cv_sum,
            'attribution_granularity': 'account_project',
            'creative_cv': None,
            'creative_attribution_allowed': False,
            'reason': 'ONE-AD CV is attributable to management_id+project, not to an individual post URL.',
        })

    # Project-only daily aggregates are context only and can never be promoted to creative CV.
    project_context = []
    for row in project_daily:
        project_context.append({
            'date': first(row, 'date'),
            'project': first(row, 'project'),
            'conversions': first(row, 'conversions'),
            'attribution_granularity': 'project_day',
            'creative_attribution_allowed': False,
        })

    payload = {
        'mode': 'conservative_outcome_join',
        'guardrail': '案件日次CVやIF×案件CVを特定投稿のCVとして割り当てない。投稿単位の成果は投稿固有ID/URLまで追跡できる成果ソースがある場合のみ許可する。',
        'joined_account_project_rows': joins,
        'rejected_rows': rejected,
        'project_context_only': project_context,
        'summary': {
            'proposal_keys': len(proposal_idx),
            'post_keys': len(post_idx),
            'onead_keys': len(onead_idx),
            'joined': len(joins),
            'creative_cv_assignments': sum(1 for r in joins if r['creative_cv'] is not None),
        },
    }
    Path(args.output).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    print('proposal_keys=', payload['summary']['proposal_keys'])
    print('post_keys=', payload['summary']['post_keys'])
    print('onead_keys=', payload['summary']['onead_keys'])
    print('joined=', payload['summary']['joined'])
    print('creative_cv_assignments=', payload['summary']['creative_cv_assignments'])


if __name__ == '__main__':
    main()
