"""读取公开 test，固定五个输入；把参考答案另存，避免提前看到答案。"""
import argparse
import io
import json
import random
import re
from pathlib import Path
from client import fingerprint, save_json

# 这三个题目也出现在论文附录，便于生成后做同题对照。
APPENDIX_IDS = ['2606.18668', '2603.16210', '2601.20357']


def read_rows(path):
    path = Path(path)
    if path.suffix == '.zst':
        import zstandard
        with zstandard.ZstdDecompressor().stream_reader(io.BytesIO(path.read_bytes())) as reader:
            text = reader.read().decode('utf-8')
    else:
        text = path.read_text(encoding='utf-8-sig')
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def to_input(row):
    problem = row.get('problem_definition', '').strip()
    # 公开版没单列 Challenge；直接取问题描述已有的 Problem statement。
    match = re.search(r'Problem statement:\s*(.+?)(?=\s*Why it matters:|$)', problem, re.S)
    if row.get('challenge'):
        challenge, origin = row['challenge'], 'dataset.challenge'
    elif match:
        challenge, origin = match.group(1).strip(), 'dataset.problem_definition.Problem statement'
    else:
        raise ValueError(f"{row.get('arxiv_id')} 没有明确 Challenge，不能从答案中补写。")
    if not problem or not challenge:
        raise ValueError('问题或 Challenge 为空。')
    return {'sample_id': row['arxiv_id'], 'problem_definition': problem, 'challenge': challenge,
            'challenge_source': origin, 'target_arxiv_id': row['arxiv_id'], 'target_title': row['title']}


def write_jsonl(path, rows):
    Path(path).write_text(''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in rows), encoding='utf-8')


def prepare(source, destination, count=5, seed=20261031):
    rows = read_rows(source)
    if any(row.get('split') != 'test' for row in rows):
        raise ValueError('只允许从 test 集选择样本。')
    by_id = {row['arxiv_id']: row for row in rows}
    if len(by_id) != len(rows):
        raise ValueError('test 中出现重复 ID。')
    # 先固定附录案例，再随机补足；在运行前选好，之后不按好坏换题。
    chosen = [by_id[x] for x in APPENDIX_IDS if x in by_id][:count]
    remaining = sorted((r for r in rows if r not in chosen), key=lambda r: r['arxiv_id'])
    random.Random(seed).shuffle(remaining)
    for row in remaining:
        if len(chosen) == count:
            break
        try:
            to_input(row)
        except ValueError:
            continue
        chosen.append(row)
    if len(chosen) != count:
        raise ValueError('符合预先条件的样本数量不足。')
    inputs = [to_input(row) for row in chosen]
    references = [{k: r.get(k) for k in ('arxiv_id', 'title', 'arxiv_url', 'intuition', 'proposal_masked')}
                  for r in chosen]
    destination = Path(destination)
    if (destination / 'manifest.json').exists():
        raise ValueError('样本清单已存在；请使用原清单，或选择新目录。')
    destination.mkdir(parents=True, exist_ok=True)
    write_jsonl(destination / 'inputs.jsonl', inputs)
    # 生成程序只读取 inputs，不会读取这个文件。
    write_jsonl(destination / 'references_for_review_only.jsonl', references)
    save_json(destination / 'manifest.json', {
        'dataset': 'Jerry999/SvalbardIdeaVault', 'source_rows': len(rows),
        'source_content_hash': fingerprint(rows), 'seed': seed,
        'selection_rule': 'three verified appendix test IDs first, then seeded shuffle; explicit challenge required',
        'selected_ids': [r['sample_id'] for r in inputs],
        'input_hash': fingerprint(inputs), 'challenge_rule': 'extract Problem statement without reading solution',
    })
    return inputs


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='准备固定五例，不调用模型。')
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, default=Path('data/benchmark'))
    args = parser.parse_args()
    for row in prepare(args.source, args.output):
        print(row['sample_id'], row['target_title'])
