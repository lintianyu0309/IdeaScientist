"""按固定清单顺序跑五例；一例失败也会记录并继续下一例。"""
import argparse
from pathlib import Path
from experiments.dataset_adapter import read_rows
from experiments.evaluation import summarize_batch
from client import fingerprint, load_json, save_json, safe_error
from workflow import run_research


def run_batch(input_file, output, resume=False, limit=None):
    rows = read_rows(input_file)
    ids = [row['sample_id'] for row in rows]
    if len(ids) != 5 or len(set(ids)) != 5:
        raise ValueError('批量实验需要五个不同的固定输入。')
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    manifest = {'input_hash': fingerprint(rows), 'sample_ids': ids}
    path = output / 'batch_manifest.json'
    if path.exists():
        if not resume or load_json(path) != manifest:
            raise ValueError('请使用原清单加 --resume，或选择新的批次目录。')
    else:
        save_json(path, manifest)
    try:
        for row in rows[:limit]:
            folder = output / row['sample_id']
            print(f"\n开始样本：{row['sample_id']}", flush=True)
            try:
                run_research(row, folder, resume=(folder / 'manifest.json').exists() and resume)
            except Exception as error:
                print(f"样本未完成：{safe_error(error)}", flush=True)
                save_json(output / f"{row['sample_id']}_batch_error.json", safe_error(error))
            summarize_batch(output, ids)
    finally:
        summarize_batch(output, ids)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='顺序运行五个固定 test 样本。')
    parser.add_argument('--inputs', type=Path, default=Path('data/benchmark/inputs.jsonl'))
    parser.add_argument('--output', type=Path, default=Path('results/test5'))
    parser.add_argument('--resume', action='store_true')
    parser.add_argument('--limit', type=int, choices=range(1, 6), help='先试跑前几例；汇总分母仍是五例')
    args = parser.parse_args()
    run_batch(args.inputs, args.output, args.resume, args.limit)
