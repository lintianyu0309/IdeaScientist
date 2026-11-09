"""生成后才读取参考材料，整理同题机制对照，不自动宣称谁更好。"""
import argparse
from pathlib import Path
from experiments.dataset_adapter import read_rows
from client import load_json

# 来自用户提供论文附录 I 的机制摘要；不传给生成角色。
APPENDIX = {
    '2606.18668': {
        'table': 'Table 15，论文第 33–34 页',
        'human': 'EARS：把有效回答和带原因的弃答一起作为训练目标，让子 Agent 学会识别不能完成的请求。',
        'base': '将弃答原因转成可检查条件，反复核验和修订原因，强调解释是否必要、相关。',
        'trained': '生成前把请求拆成工具、字段、权限等要求，与实时能力清单核对；不满足时返回分类弃答。',
        'question': '方案检查的是能力缺失、回答置信度，还是弃答理由？能力清单和请求解析出错怎么办？',
    },
    '2603.16210': {
        'table': 'Table 13，论文第 29–30 页',
        'human': 'MOSAIC：为安全类别学习控制 token，只拼接当前启用规则的 token，主模型参数冻结。',
        'base': '用 Set Transformer 学习规则组合关系，再用 FiLM 调整部分隐藏信号。',
        'trained': '每条规则学习可接受状态边界，生成时将激活移向启用边界的交集。',
        'question': '方案怎样处理规则冲突和未见组合？是否只是加权折中？联合可行性是否有保证？',
    },
    '2601.20357': {
        'table': 'Table 16，论文第 34–36 页',
        'human': 'TABED：保留视觉和文本 drafter 的混合，用过去的目标验证信号在线调整权重。',
        'base': '比较文本和视觉条件下的 draft 分布，用 KL 差异控制视觉信息注入。',
        'trained': '用目标模型 prefill 阶段的视觉注意力信号，在每轮选择视觉或纯文本 drafter。',
        'question': '适应信号是什么？它是否可靠？额外 drafting 和路由成本是否计入总延迟？',
    },
}


def build_report(batch_dir, references):
    batch_dir = Path(batch_dir)
    manifest = load_json(batch_dir / 'batch_manifest.json')
    refs = {r['arxiv_id']: r for r in read_rows(references)}
    sections = ['# 五例案例材料与论文机制对照',
        '本文只整理实际生成产物与参考机制，不表示方案已经通过科研实验，也不自动判断优劣。'
        '生成程序不读取参考答案；本脚本仅在运行后整理对照。不同模型和检索环境下，不能直接比较论文分数。']
    for sid in manifest['sample_ids']:
        folder = batch_dir / sid
        status = load_json(folder / 'run_status.json') if (folder / 'run_status.json').exists() else {'status': 'not_started'}
        ref = refs[sid]
        sections += [f"## {sid}：{ref['title']}", f"运行状态：`{status['status']}`。"]
        state = load_json(folder / 'state.json') if (folder / 'state.json').exists() else {}
        gap = state.get('research_gap', {})
        idea = state.get('research_idea', {})
        if gap:
            selected = next((g for g in gap['gaps'] if g['gap_id'] == state.get('selected_gap')), gap['gaps'][0])
            sections += ['### 实际识别的 Gap', selected['statement'], '证据局限：' + selected['uncertainty']]
        if idea:
            sections += ['### 实际生成的核心构想', idea['intuition'],
                         '源领域：' + idea['source_domain'], '源机制：' + idea['source_mechanism'],
                         '适配方式：' + idea['adaptation'], '迁移假设：' + idea['transfer_assumption'],
                         '可证伪预测：' + idea['testable_prediction'], '主要风险：' + idea['main_risk']]
        else:
            sections.append('尚未生成经过校验的 Idea，不能补写为成功案例。')
        if sid in APPENDIX:
            entry = APPENDIX[sid]
            sections += ['### 论文附录机制', entry['table'],
                         '**人类论文：**' + entry['human'], '**IdeaScientist-Base：**' + entry['base'],
                         '**IdeaScientist-Trained：**' + entry['trained'], '**对照重点：**' + entry['question']]
        else:
            sections += ['### 公开数据参考构想（生成结束后查看）', ref['intuition']]
        sections += ['### 人工核查项',
                     '- 抽查 Gap 的关键事实是否被实际摘要支持。',
                     '- 抽查源机制是否被引用摘要支持，迁移假设是否明确。',
                     '- 对比是否只是换词、常见拼接，或者存在具体方法区别。',
                     '- 检查 Proposal 是否保留同一个机制，是否写出反驳条件。',
                     '- 不把拟开展实验写成已经取得的结果。']
    output = batch_dir / 'case_comparison.md'
    output.write_text('\n\n'.join(sections), encoding='utf-8')
    return output


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='整理生成后的案例材料；不调用模型。')
    parser.add_argument('--batch-dir', type=Path, default=Path('results/test5'))
    parser.add_argument('--references', type=Path, default=Path('data/benchmark/references_for_review_only.jsonl'))
    args = parser.parse_args()
    print(build_report(args.batch_dir, args.references))
