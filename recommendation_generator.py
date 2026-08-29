import argparse
import json
from pathlib import Path

KIND_LABELS = {
    'structure': '構成',
    'appeal': '訴求',
    'format': '投稿形態',
    'genre': 'ジャンル',
}


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def project_rows(snapshot, project):
    rows = snapshot.get('results', []) if isinstance(snapshot, dict) else snapshot
    out = []
    for row in rows:
        raw = row.get('案件')
        if isinstance(raw, str):
            try:
                projects = json.loads(raw)
            except Exception:
                projects = [raw]
        elif isinstance(raw, list):
            projects = raw
        else:
            projects = []
        if project in projects:
            out.append(row)
    return out


def evidence_strength(signal):
    posts = signal.get('current_posts', 0)
    accounts = signal.get('current_accounts', 0)
    if posts >= 5 and accounts >= 5:
        return 'strong_initial'
    if posts >= 3 and accounts >= 3:
        return 'medium_initial'
    return 'weak_initial'


def generate(trend, snapshot, project):
    rows = project_rows(snapshot, project)
    project_accounts = {r.get('アカウントURL') for r in rows if r.get('アカウントURL')}
    signals = [s for s in trend.get('signals', []) if s.get('kind') in {'structure','appeal','format'}]
    signals.sort(key=lambda s: (s.get('trend_score', 0), s.get('current_posts', 0)), reverse=True)

    selected = []
    for s in signals:
        if len(selected) >= 6:
            break
        selected.append({
            'kind': s['kind'],
            'label': KIND_LABELS.get(s['kind'], s['kind']),
            'name': s['name'],
            'status': s['status'],
            'trend_score': s['trend_score'],
            'posts': s['current_posts'],
            'accounts': s['current_accounts'],
            'evidence_strength': evidence_strength(s),
            'note': '市場内の出現頻度シグナル。CV・売上への因果を示すものではない。',
        })

    structures = [x for x in selected if x['kind'] == 'structure']
    appeals = [x for x in selected if x['kind'] == 'appeal']
    formats = [x for x in selected if x['kind'] == 'format']

    recs = []
    if structures:
        s = structures[0]
        a = appeals[0] if appeals else None
        recs.append({
            'priority': 1,
            'hypothesis': f"{s['name']}を主軸に" + (f"{a['name']}を組み合わせる" if a else '商品理解を補強する'),
            'why': f"直近データで{s['name']}が{s['posts']}投稿/{s['accounts']}アカウント。" + (f"{a['name']}は{a['posts']}投稿/{a['accounts']}アカウント。" if a else ''),
            'test_design': 'IFへ強制せず参考構成として提示し、実際の投稿内容を後から別レイヤーで記録してCVと比較する。',
        })
    if len(appeals) >= 2:
        recs.append({
            'priority': 2,
            'hypothesis': f"{appeals[0]['name']}単独ではなく、{appeals[1]['name']}との複合訴求を比較テストする",
            'why': f"両訴求が複数アカウントで観測されているため、単一投稿の偶然より再利用候補として優先できる。",
            'test_design': '投稿前の提案内容と実際の採用内容を分離して保存し、採用された訴求だけを実績分析対象にする。',
        })
    if formats:
        recs.append({
            'priority': 3,
            'hypothesis': f"{formats[0]['name']}での展開を第一候補として扱う",
            'why': f"直近データで{formats[0]['posts']}投稿/{formats[0]['accounts']}アカウントに観測。",
            'test_design': '媒体・投稿形態の母数偏りを考慮し、形式自体が成果要因とは断定しない。',
        })

    return {
        'project': project,
        'mode': 'initial_signal',
        'warning': 'ベースライン期間の蓄積が不足しているため、現段階はトレンド確定ではなく初期シグナル。CV・売上との因果判定は行わない。',
        'project_rows_in_snapshot': len(rows),
        'project_accounts_in_snapshot': len(project_accounts),
        'signals_used': selected,
        'recommendations': recs,
        'next_validation': [
            '市場データを継続蓄積し28日ベースラインを形成',
            '提案と実際の投稿を別データとして保持',
            '実投稿の構成・訴求とCVを接続して成果側から検証',
        ],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--trend', required=True)
    ap.add_argument('--snapshot', required=True)
    ap.add_argument('--project', required=True)
    ap.add_argument('--output', default='campaign_recommendations.json')
    args = ap.parse_args()
    payload = generate(load(args.trend), load(args.snapshot), args.project)
    Path(args.output).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    print('project=', payload['project'])
    print('mode=', payload['mode'])
    print('project_rows=', payload['project_rows_in_snapshot'])
    print('signals_used=', len(payload['signals_used']))
    print('recommendations=', len(payload['recommendations']))
    for r in payload['recommendations']:
        print('-', r['priority'], r['hypothesis'])

if __name__ == '__main__':
    main()
