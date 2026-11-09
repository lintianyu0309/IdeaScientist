"""公共操作：结构校验、引用校验、证据压缩，以及每阶段的完成契约。

检索与模型调用由固定流程执行，不启用模型自主工具调用或新增 Agent。
"""
import json
import re

class InsufficientEvidence(ValueError):
    """证据不够：保留原因，不继续编写成功报告。"""

def nonempty(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{label} 必须是非空文字。')

def prepare_research_input(initial):
    """接受完整问题定义；缺少 Challenge 时，从 Problem statement 原样提取。

    兼容旧的显式 Challenge，不修改调用者的字典，也不调用模型补写输入。
    """
    if not isinstance(initial, dict):
        raise ValueError('Research input must be a JSON object.')
    allowed = {'sample_id', 'problem_definition', 'challenge', 'challenge_source',
               'target_arxiv_id', 'target_title'}
    if set(initial) - allowed:
        raise ValueError('输入含非问题字段；请将参考答案与运行状态单独保存。')
    problem = initial.get('problem_definition')
    if not isinstance(problem, str) or not problem.strip():
        raise ValueError('Please enter the complete problem definition.')
    result = dict(initial)
    if 'challenge' in result:
        nonempty(result['challenge'], 'Challenge')
        return result
    match = re.search(
        r'\bProblem\s+statement\s*:\s*(.*?)(?=\s*\bWhy\s+it\s+matters\s*:|\Z)',
        problem, re.I | re.S,
    )
    if not match or not match.group(1).strip():
        raise ValueError(
            "Please include a non-empty 'Problem statement:' section in the problem "
            "definition (before 'Why it matters:', if present)."
        )
    result['challenge'] = match.group(1).strip()
    result['challenge_source'] = 'problem_definition.Problem statement'
    return result

def require_fields(value, fields):
    if not isinstance(value, dict) or set(value) != set(fields):
        raise ValueError(f'字段必须为：{", ".join(fields)}')

def check_ids(ids, papers):
    allowed = {p['paperId'] for p in papers if p.get('abstract') and p.get('paperId')}
    if not isinstance(ids, list) or not ids:
        raise ValueError('需要至少一个支持该结论的论文 ID。')
    if any(not isinstance(x, str) or x not in allowed for x in ids):
        raise ValueError('引用必须来自本次提供的、有摘要的论文。')
    if len(ids) != len(set(ids)):
        raise ValueError('引用列表中有重复 ID。')

def check_body_ids(text, ids):
    found = set(re.findall(r'\b[0-9a-f]{40}\b', text))
    if found != set(ids):
        raise ValueError('正文中的论文 ID 必须与引用清单完全一致。')

def check_known_mentions(value, papers):
    # 连藏在说明文字里的假 ID 也检查，不能只检查引用清单。
    mentioned = set(re.findall(r'\b[0-9a-f]{40}\b', json.dumps(value, ensure_ascii=False)))
    allowed = {p['paperId'] for p in papers if p.get('abstract') and p.get('paperId')}
    if mentioned - allowed:
        raise ValueError('说明文字中出现了未提供的论文 ID。')

def compact_papers(papers, maximum=16):
    # 只传分析需要的内容；不重复传作者信息和整个检索历史。
    return [
        {k: p.get(k) for k in ('paperId', 'title', 'year', 'abstract')}
        for p in papers if p.get('abstract')
    ][:maximum]

def parse_json(text):
    text = text.strip()
    if text.startswith('```'):
        text = re.sub(r'^```(?:json)?\s*|\s*```$', '', text)
    # 使用严格解析，不接受被截断后自动补齐的 JSON。
    return json.loads(text)

# 固定流程每个节点必须交付的状态字段。校验成功后 runner 才保存检查点。
STAGE_OUTPUT_FIELDS = {
    'plan_search': ('search_queries',),
    'retrieve_literature': ('papers', 'search_history'),
    'find_gap': ('research_gap',),
    'plan_innovation': ('selected_gap', 'gap_selection_reason', 'cross_domain_queries'),
    'retrieve_cross_domain': ('cross_domain_papers', 'cross_domain_search_history'),
    'generate_idea': ('research_idea',),
    'write_proposal': ('research_proposal',),
}


def validate_stage_update(stage, update):
    """检查节点是否完成约定输出；各角色还会执行具体的内容和引用校验。"""
    if stage in STAGE_OUTPUT_FIELDS:
        require_fields(update, STAGE_OUTPUT_FIELDS[stage])
