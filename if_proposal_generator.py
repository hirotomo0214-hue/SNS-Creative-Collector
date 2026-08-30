import argparse
import json
from pathlib import Path


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--recommendations', required=True)
    ap.add_argument('--output', default='if_proposal_pack.json')
    args = ap.parse_args()

    data = load(args.recommendations)
    project = data.get('project', '')
    recs = data.get('recommendations', [])
    signals = data.get('signals_used', [])

    signal_names = [s.get('name') for s in signals if s.get('name')]
    primary = recs[0] if recs else {}
    secondary = recs[1] if len(recs) > 1 else {}

    pack = {
        'project': project,
        'status': 'proposal_candidate',
        'evidence_mode': data.get('mode', 'initial_signal'),
        'guardrail': '市場内の出現頻度を元にした参考提案。CV実績がある型とは断定せず、IF側の追加管理を必須にしない。',
        'proposal_summary': {
            'recommended_direction': primary.get('hypothesis', ''),
            'secondary_direction': secondary.get('hypothesis', ''),
            'signals': signal_names,
        },
        'if_message': (
            f"{project}について、最近の投稿傾向を見ている中で、\n"
            f"『{primary.get('hypothesis', '商品紹介にオファー要素を組み合わせる')}』流れが複数アカウントで見られました。\n\n"
            "そのまま真似する必要はないのですが、もし投稿する際の参考になれば、\n"
            "・最初にキャンペーンやお得感で興味を引く\n"
            "・その後に商品特徴や自分の使い方を入れる\n"
            "・最後に期間や条件を分かりやすく整理する\n"
            "という流れは取り入れやすそうです！\n\n"
            "アカウントの雰囲気に合う形で自由にアレンジいただいて大丈夫です◎"
        ),
        'structure_outline': [
            {'step': 1, 'role': 'hook', 'instruction': 'オファー・キャンペーン・価格差など、見る理由を最初に置く'},
            {'step': 2, 'role': 'product', 'instruction': '商品特徴を1〜2点に絞って紹介する'},
            {'step': 3, 'role': 'personal_context', 'instruction': '自分の生活・使用場面・感想を入れて広告感を弱める'},
            {'step': 4, 'role': 'close', 'instruction': '期間・条件・購入導線を簡潔に整理する'},
        ],
        'optional_tracking': {
            'proposal_id': None,
            'proposal_sent_at': None,
            'if_account': None,
            'note': '既存運用で自然に取れる場合のみ記録。実投稿・CV追跡は必須にしない。',
        },
    }

    Path(args.output).write_text(json.dumps(pack, ensure_ascii=False, indent=2), encoding='utf-8')
    print('project=', project)
    print('status=', pack['status'])
    print('recommended_direction=', pack['proposal_summary']['recommended_direction'])
    print('structure_steps=', len(pack['structure_outline']))


if __name__ == '__main__':
    main()
