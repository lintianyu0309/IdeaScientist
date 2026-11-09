"""汇总真实记录；文献是否支持结论仍需人工核查。"""
import argparse
import re
from pathlib import Path
from client import load_json, save_json


def summarize_run(run_dir):
    run_dir = Path(run_dir)
    status_path = run_dir / 'run_status.json'
    status = load_json(status_path) if status_path.exists() else {'status': 'not_started'}
    state = load_json(run_dir / 'state.json') if (run_dir / 'state.json').exists() else {}
    histories = []
    for path in (run_dir / 'retrieval').glob('*.json'):
        histories.extend(load_json(path)['history'])
    papers = {p['paperId']: p for r in histories for p in r['papers']}
    proposal = state.get('research_proposal', {})
    body = '\n'.join(v for k, v in proposal.items() if k != 'cited_paper_ids' and isinstance(v, str))
    cited = set(proposal.get('cited_paper_ids', [])) | set(re.findall(r'\b[0-9a-f]{40}\b', body))
    valid = cited & {pid for pid, p in papers.items() if p.get('abstract')}
    usage = [load_json(p) for p in (run_dir / 'model_calls').glob('*/usage_*.json')]
    model_errors = list((run_dir / 'model_calls').glob('*/call_error_*.json'))
    steps = [load_json(p) for p in (run_dir / 'steps').glob('*/output.json')]
    return {
        'sample_id': run_dir.name, 'status': status['status'], 'active_step': status.get('active_step'),
        'query_count': len(histories), 'failed_queries': sum(r['status'] == 'failed' for r in histories),
        'empty_queries': sum(r['status'] == 'empty' for r in histories),
        'unique_papers': len(papers), 'papers_with_abstract': sum(bool(p.get('abstract')) for p in papers.values()),
        'successful_model_responses': len(usage), 'failed_model_attempts': len(model_errors),
        'input_tokens': sum(r['usage'].get('input_tokens', 0) for r in usage),
        'output_tokens': sum(r['usage'].get('output_tokens', 0) for r in usage),
        'successful_step_seconds': round(sum(s['seconds'] for s in steps), 2),
        'citation_identity_rate': len(valid) / len(cited) if cited else None,
        'citation_count': len(cited), 'evidence_support': 'requires_manual_review',
        'error': status.get('error'),
    }


def summarize_batch(batch_dir, sample_ids):
    folder = Path(batch_dir)
    rows = [summarize_run(folder / sid) for sid in sample_ids]
    completed = sum(row['status'] == 'complete' for row in rows)
    result = {'completed': completed, 'total': len(rows), 'runs': rows,
              'scope': '工程完成率与引用身份检查，不等于创新性或科研效果验证'}
    save_json(folder / 'summary.json', result)
    lines = [f'# 五例运行汇总\n\n完整完成：{completed}/{len(rows)}。失败和未开始的样本保留在分母中。',
             '| 样本 | 状态 | 有摘要论文 | 引用身份有效率 | 模型成功回答数 |',
             '|---|---|---:|---:|---:|']
    for row in rows:
        rate = row['citation_identity_rate']
        lines.append(f"| {row['sample_id']} | {row['status']} | {row['papers_with_abstract']} | "
                     f"{f'{rate:.0%}' if rate is not None else '无引用'} | {row['successful_model_responses']} |")
    lines.append('\n引用身份有效，仅说明 ID 来自文献池。结论是否被摘要支持、机制是否新颖，需要案例分析。')
    (folder / 'summary.md').write_text('\n\n'.join(lines[:1]) + '\n\n' + '\n'.join(lines[1:]), encoding='utf-8')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--batch-dir', type=Path, required=True)
    args = parser.parse_args()
    manifest = load_json(args.batch_dir / 'batch_manifest.json')
    summarize_batch(args.batch_dir, manifest['sample_ids'])
