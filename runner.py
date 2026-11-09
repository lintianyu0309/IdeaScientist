"""三 Agent Harness：状态、节点执行、LangGraph 调度、检查点与命令行。"""
import argparse
import hashlib
import json
import re
import time
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from state import ResearchState
from uuid import uuid4

from langgraph.graph import StateGraph, START, END
import client
import tools
import roles
from client import fingerprint, load_json, save_json, safe_error, model_settings, ask_json
from tools import (
    check_ids, check_known_mentions, check_body_ids, compact_papers,
    nonempty, require_fields, InsufficientEvidence, validate_stage_update, prepare_research_input,
)
from corpus import collect_papers, normalize_queries

PROJECT_DIR = Path(__file__).resolve().parent

# ===== state =====


# ===== gap_finder =====
def generate_search_queries(state):
    """按问题和挑战生成4—8个查询，优先找最接近的现有方法。"""

    def validate(result):
        require_fields(result, ['queries'])
        # 检查查询数量，清理重复项和不支持的搜索语法。
        result['queries'] = normalize_queries(result['queries'], 4, 8)

        # 检索词要同时包含“研究对象”和“技术问题”，避免只搜通用机制。
    instruction = roles.GENERATE_SEARCH_QUERIES_INSTRUCTION

    result = ask_json(
        state,
        'plan_search',
        instruction,
        {
            'problem_definition': state['problem_definition'],
            'challenge': state['challenge'],
        },
        {'queries': ['query 1', 'query 2', 'query 3', 'query 4']},
        validate,
    )
    return {'search_queries': result['queries']}

def retrieve_literature(state):
    """使用现有检索函数获取目标问题的论文。"""
    papers, history = collect_papers(
        state, state['search_queries'], 'target'
    )
    return {'papers': papers, 'search_history': history}

def gap_finder(state):
    """先比较最接近的方法，再总结最多两个具体研究空白。"""

    # 保留原来的输出字段，后面的 Innovator 不需要修改。
    schema = deepcopy(roles.ROLES["gap_finder"].schema)

    papers = compact_papers(state['papers'])

    def validate(result):
        # Gap 输出需要这些字段，供后面的 Innovator 读取。
        require_fields(result, schema)

        # 证据不足时明确停止，避免生成没有文献依据的 Gap。
        if result['status'] == 'insufficient_evidence':
            raise InsufficientEvidence(result['summary'])

        if result['status'] != 'supported':
            raise ValueError('status 应为 supported 或 insufficient_evidence。')

        if not isinstance(result['gaps'], list) or not result['gaps']:
            raise ValueError('supported 状态下需要提供研究 Gap。')

        # 只检查 Gap 字段和引用，不检查查询数量或单词数量。
        for gap in result['gaps']:
            require_fields(gap, schema['gaps'][0])
            check_ids(gap['supporting_paper_ids'], papers)

        check_known_mentions(result, papers)
    instruction = roles.ROLES["gap_finder"].prompt

    result = ask_json(
        state,
        'find_gap',
        instruction,
        {
            'problem_definition': state['problem_definition'],
            'challenge': state['challenge'],
            'papers': papers,
        },
        schema,
        validate,
    )
    return {'research_gap': result}


# ===== innovator =====
def plan_innovation(state):
    """选择一个已有 Gap，生成6—10个机制检索查询。"""

    def validate(result):
        require_fields(
            result, ['selected_gap', 'selection_reason', 'queries']
        )

        # 只能选择 Gap Finder 实际提出的 Gap。
        gap_ids = {
            gap['gap_id'] for gap in state['research_gap']['gaps']
        }
        if result['selected_gap'] not in gap_ids:
            raise ValueError('所选 Gap 必须真实存在于上一步结果中。')

        nonempty(result['selection_reason'], 'selection_reason')

        # 检查查询数量，并清理重复项和不支持的搜索语法。
        result['queries'] = normalize_queries(
            result['queries'], 6, 10
        )

    # 查询规划必须覆盖至少三个不同的来源子领域。
    instruction = roles.PLAN_INNOVATION_INSTRUCTION

    result = ask_json(
        state,
        'plan_innovation',
        instruction,
        {
            'problem_definition': state['problem_definition'],
            'challenge': state['challenge'],
            'research_gap': state['research_gap'],
        },
        {
            'selected_gap': 'gap_1',
            'selection_reason': 'why this gap is worth pursuing',
            'queries': [
                'query 1',
                'query 2',
                'query 3',
                'query 4',
                'query 5',
                'query 6',
            ],
        },
        validate,
    )

    return {
        'selected_gap': result['selected_gap'],
        'gap_selection_reason': result['selection_reason'],
        'cross_domain_queries': result['queries'],
    }

def retrieve_cross_domain(state):
    """使用 Semantic Scholar 检索候选来源论文。"""
    papers, history = collect_papers(
        state, state['cross_domain_queries'], 'cross_domain'
    )
    return {
        'cross_domain_papers': papers,
        'cross_domain_search_history': history,
    }

def innovator(state):
    """把来源机制映射到选定 Gap，形成一个研究直觉。"""

    # 保留原有字段，后面的 Report Writer 可以继续读取。
    schema = deepcopy(roles.ROLES["innovator"].schema)

    target = compact_papers(state['papers'])
    sources = compact_papers(state['cross_domain_papers'])

    def validate(result):
        require_fields(result, schema)

        # 找不到可靠机制时停止，不强迫模型拼出一个构想。
        if result['status'] == 'insufficient_evidence':
            raise InsufficientEvidence(
                result.get('main_risk', '没有可迁移机制的证据。')
            )

        if (
            result['status'] != 'supported'
            or result['selected_gap'] != state['selected_gap']
        ):
            raise ValueError('Idea 必须针对已经选定的 Gap。')

        for key in set(schema) - {'source_paper_ids'}:
            nonempty(result[key], key)

        # 来源机制必须引用本轮检索提供的候选来源论文。
        check_ids(result['source_paper_ids'], sources)

        # 文中出现的论文 ID 必须来自已提供的文献。
        check_known_mentions(result, target + sources)
        check_body_ids(
            result['source_mechanism'],
            result['source_paper_ids'],
        )

    instruction = roles.ROLES["innovator"].prompt

    result = ask_json(
        state,
        'generate_idea',
        instruction,
        {
            'problem_definition': state['problem_definition'],
            'challenge': state['challenge'],
            'research_gap': state['research_gap'],
            'selected_gap': state['selected_gap'],
            'target_papers': target,
            'cross_domain_papers': sources,
        },
        schema,
        validate,
    )
    return {'research_idea': result}


# ===== report_writer =====
PROPOSAL_FIELDS = {
    'title': 'Title', 'topic_relevance': 'Topic relevance',
    'one_sentence_thesis': 'One-sentence thesis', 'core_problem': 'Core problem',
    'key_novelty': 'Key novelty', 'problem_definition': 'Problem definition',
    'key_terms': 'Key terms', 'related_work': 'Related work', 'method': 'Method',
    'model_details': 'Model details', 'comparison_to_state_of_the_art': 'Comparison to state of the art',
    'proposed_evaluation': 'Proposed evaluation', 'falsifiable_predictions': 'Falsifiable predictions',
    'limitations': 'Limitations',
}

def validate_proposal(proposal, papers):
    # 兼容模型偶尔使用的旧字段名，其余多余字段仍报错。
    if isinstance(proposal, dict) and 'comparison_to_state_of_art' in proposal and 'comparison_to_state_of_the_art' not in proposal:
        proposal['comparison_to_state_of_the_art'] = proposal.pop('comparison_to_state_of_art')
    require_fields(proposal, list(PROPOSAL_FIELDS) + ['cited_paper_ids'])
    for key in PROPOSAL_FIELDS:
        nonempty(proposal[key], key)
    if len(proposal['one_sentence_thesis'].split()) >= 30:
        raise ValueError('one_sentence_thesis 必须少于 30 个英文单词。')
    check_ids(proposal['cited_paper_ids'], papers)
    check_body_ids('\n'.join(proposal[k] for k in PROPOSAL_FIELDS), proposal['cited_paper_ids'])

GROUPS = (
    # 第一组：简要说明问题和贡献。
    (
        'overview',
        (
            'title',
            'topic_relevance',
            'one_sentence_thesis',
            'core_problem',
            'key_novelty',
            'problem_definition',
            'key_terms',
        ),
        350,
    ),

    # 第二组：重点展开新方法。
    # 1400 是本组所有字段合计的英文词数预算。
    (
        'method',
        (
            'related_work',
            'method',
            'model_details',
            'comparison_to_state_of_the_art',
        ),
        1400,
    ),

    # 第三组：提出验证计划和失败条件。
    (
        'evaluation',
        (
            'proposed_evaluation',
            'falsifiable_predictions',
            'limitations',
        ),
        450,
    ),
)

def select_evidence(materials):
    """保留上游引用的论文，再补到 16 篇；不截断摘要。"""
    cited = set(re.findall(
        r'\b[0-9a-f]{40}\b',
        json.dumps([materials['research_gap'], materials['research_idea']]),
    ))
    papers = materials['papers']
    selected = [paper for paper in papers if paper['paperId'] in cited]
    selected_ids = {paper['paperId'] for paper in selected}
    for paper in papers:
        if len(selected) >= 16:
            break
        if paper['paperId'] not in selected_ids:
            selected.append(paper)
            selected_ids.add(paper['paperId'])
    return selected

def write_in_sections(state, stage, instruction, materials, schema, validate):
    """接收原 Report Writer 的提示词和字段要求，分组完成同一份报告。"""
    if stage != 'write_proposal':
        raise ValueError('分组写作只处理报告阶段。')

    evidence = select_evidence(materials)
    materials = {**materials, 'papers': evidence}
    proposal = {}

    for number, (name, fields, word_budget) in enumerate(GROUPS, 1):
        part_schema = {field: schema[field] for field in fields}

        def validate_part(result):
            tools.require_fields(result, fields)
            for field in fields:
                tools.nonempty(result[field], field)
            if 'one_sentence_thesis' in result:
                if len(result['one_sentence_thesis'].split()) >= 30:
                    raise ValueError('Keep one_sentence_thesis below 30 English words.')

            # 仅接受本次提供的论文引用，不替模型补写证据。
            text = '\n'.join(result[field] for field in fields)
            ids = sorted(set(re.findall(r'\b[0-9a-f]{40}\b', text)))
            if ids:
                tools.check_ids(ids, evidence)

        part_instruction = instruction + f'''

This call writes only part {number} of 3 of the SAME proposal.
Return only these fields: {', '.join(fields)}.
The output_schema for this call defines the complete required output.
Keep this entire part within approximately {word_budget} English words.
Be concise while covering the requirements. Do not repeat previous sections.
Use previous_sections as consistency context, not as fields to output again.
Each requested field must be one JSON string, not an object or an array.
Escape quotation marks and newlines inside JSON strings correctly.
If you cite a supplied paper, use its complete [paperId].
Do not output cited_paper_ids; the program will collect actual citations.
Return a fully closed JSON object.
'''
        print(f'报告第 {number}/3 组：{name}（已完成的组会复用）', flush=True)
        update = client.ask_json(
            state, f'proposal_{number}_{name}', part_instruction,
            {**materials, 'previous_sections': dict(proposal)},
            part_schema, validate_part,
        )
        proposal.update(update)
        client.save_json(Path(state['run_dir']) / 'proposal_progress.json', proposal)
        print(f'已完成报告第 {number}/3 组', flush=True)

    # 从正文收集实际引用；原来的完整报告校验仍会核对文献 ID。
    body = '\n'.join(proposal[field] for field in PROPOSAL_FIELDS)
    proposal['cited_paper_ids'] = sorted(set(re.findall(r'\b[0-9a-f]{40}\b', body)))
    validate(proposal)
    return proposal

def report_writer(state):
    """把已有科研直觉展开成完整方案，并保持原任务和核心机制。"""

    # 汇总两个阶段提供的文献，按论文 ID 去重。
    papers = list({
        paper['paperId']: paper
        for paper in (
            compact_papers(state['papers'])
            + compact_papers(state['cross_domain_papers'])
        )
    }.values())

    # 按论文 Table 5，为每个输出字段提供具体要求。
    # 所有内容字段仍使用字符串，兼容现有校验和 Markdown 导出。
    schema = deepcopy(roles.ROLES["report_writer"].schema)

    instruction = roles.ROLES["report_writer"].prompt
    # 三组生成并保存，统一返回原来的 research_proposal 字段。
    result = write_in_sections(
        state,
        'write_proposal',
        instruction,
        {
            'problem_definition': state['problem_definition'],
            'challenge': state['challenge'],
            'research_gap': state['research_gap'],
            'selected_gap': state['selected_gap'],
            'research_idea': state['research_idea'],
            'papers': papers,
        },
        schema,
        lambda result: validate_proposal(result, papers),
    )
    return {'research_proposal': result}

def export_proposal(state, run_dir):
    run_dir = Path(run_dir)
    proposal = state['research_proposal']
    save_json(run_dir / 'research_proposal.json', proposal)
    sections = [f'## {heading}\n\n{proposal[key]}' for key, heading in PROPOSAL_FIELDS.items()]
    lookup = {p['paperId']: p for p in state['papers'] + state['cross_domain_papers']}
    references = [f"- [{pid}] {lookup[pid].get('title')} — {lookup[pid].get('url')}" for pid in proposal['cited_paper_ids']]
    sections.append('## References\n\n' + '\n'.join(references))
    (run_dir / 'research_proposal.md').write_text('\n\n'.join(sections), encoding='utf-8')
    # 结构化结果也导出成易读文档，答辩时可以按步骤查看。
    for key in ('research_gap', 'research_idea'):
        (run_dir / f'{key}.md').write_text('```json\n' + json.dumps(state[key], ensure_ascii=False, indent=2) + '\n```\n', encoding='utf-8')


# ===== LangGraph 调度与命令行 =====
STEPS = [
    ('plan_search', generate_search_queries),
    ('retrieve_literature', retrieve_literature),
    ('find_gap', gap_finder),
    ('plan_innovation', plan_innovation),
    ('retrieve_cross_domain', retrieve_cross_domain),
    ('generate_idea', innovator),
    ('write_proposal', report_writer),
]

DEFAULT_SETTINGS = {'cutoff': '2025-12-31', 'papers_per_query': 5,
                    'min_abstracts': 3, 'request_pause': 2}

def code_fingerprint():
    names = ('roles.py', 'corpus.py', 'tools.py', 'client.py', 'runner.py', 'state.py', 'workflow.py')
    return fingerprint({name: hashlib.sha256((PROJECT_DIR / name).read_bytes()).hexdigest() for name in names})

@contextmanager
def run_lock(run_dir):
    # 同一个目录一次只能由一个程序使用，避免互相覆盖。
    path = Path(run_dir) / '.running.lock'
    try:
        file = path.open('x', encoding='utf-8')
    except FileExistsError:
        raise ValueError('该目录正在运行，或上次被强制关闭。确认无运行进程后再删除 .running.lock。') from None
    try:
        file.write(datetime.now(timezone.utc).isoformat())
        file.close()
        yield
    finally:
        path.unlink(missing_ok=True)

def build_workflow(run_dir, steps=None):
    steps = STEPS if steps is None else steps
    graph = StateGraph(ResearchState)
    run_dir = Path(run_dir)

    def wrap(name, function):
        def execute(state):
            folder = run_dir / 'steps' / name
            saved = folder / 'output.json'
            signature = fingerprint(state)
            if saved.exists():
                checkpoint = load_json(saved)
                if checkpoint['input_hash'] != signature:
                    raise ValueError(f'{name} 的输入已变化，请新建实验目录。')
                print(f'复用已完成步骤：{name}', flush=True)
                return checkpoint['update']
            # 在执行前保存，失败的那一步也有输入可查。
            save_json(folder / 'input.json', state)
            save_json(run_dir / 'run_status.json', {'status': 'running', 'active_step': name})
            start = time.monotonic()

            # 先显示当前步骤，等待模型回答时就知道程序正在做什么。
            print(f'正在执行：{name}', flush=True)

            update = function(state)
            validate_stage_update(name, update)
            save_json(saved, {'input_hash': signature, 'update': update,
                              'seconds': round(time.monotonic() - start, 2)})
            save_json(run_dir / 'state.json', {**state, **update})
            print(f'已完成：{name}', flush=True)
            return update
        return execute

    previous = START
    for name, function in steps:
        graph.add_node(name, wrap(name, function))
        graph.add_edge(previous, name)
        previous = name
    graph.add_edge(previous, END)
    return graph.compile()

def run_research(initial_state, run_dir, resume=False, settings=None):
    run_dir = Path(run_dir).resolve()
    # 统一页面、JSON 文件与直接函数调用的输入；自动提取后再保存检查点。
    initial_state = prepare_research_input(initial_state)
    options = {**DEFAULT_SETTINGS, 'cache_dir': str(PROJECT_DIR / 'cache'), **(settings or {})}
    options['cache_dir'] = str(Path(options['cache_dir']).resolve())
    initial_state = {**initial_state, 'sample_id': initial_state.get('sample_id', run_dir.name)}
    manifest = {'input': initial_state, 'settings': options, 'model': model_settings(), 'code_hash': code_fingerprint()}
    run_dir.mkdir(parents=True, exist_ok=True)
    with run_lock(run_dir):
        existing = run_dir / 'manifest.json'
        if existing.exists():
            if not resume:
                raise ValueError('目录已存在，请使用 --resume 或换一个新目录。')
            if load_json(existing) != manifest:
                raise ValueError('代码、模型或输入已改变，请新建实验目录；旧结果不会覆盖。')
        else:
            if resume:
                raise ValueError('此目录没有新版检查点，不能直接恢复旧版实验。')
            save_json(existing, manifest)
            save_json(run_dir / 'input.json', initial_state)
        start = time.monotonic()
        state = {**initial_state, 'settings': options, 'run_dir': str(run_dir)}
        try:
            final = build_workflow(run_dir).invoke(state)
            export_proposal(final, run_dir)
            save_json(run_dir / 'run_status.json', {'status': 'complete', 'last_completed_step': 'write_proposal',
                                                   'invocation_seconds': round(time.monotonic() - start, 2)})
            return final
        except Exception as error:
            status = load_json(run_dir / 'run_status.json') if (run_dir / 'run_status.json').exists() else {}
            save_json(run_dir / 'run_status.json', {
                **status, 'status': 'insufficient_evidence' if isinstance(error, InsufficientEvidence) else 'failed',
                'error': safe_error(error), 'invocation_seconds': round(time.monotonic() - start, 2)})
            raise

def read_multiline(label, required=True):
    while True:
        print(f'{label}（独立输入 END 结束）')
        lines = []
        while (line := input()).strip().upper() != 'END':
            lines.append(line)
        value = '\n'.join(lines).strip()
        if value or not required:
            return value
        print('这项不能为空。')

def main():
    parser = argparse.ArgumentParser(
        description='运行一个科研构想任务；失败后可以恢复。'
    )
    parser.add_argument('--input', type=Path, help='问题 JSON 文件')
    parser.add_argument('--run-dir', type=Path)
    parser.add_argument('--resume', action='store_true')
    args = parser.parse_args()

    # 三种启动方式：恢复任务、读取文件、手动输入。
    if args.resume:
        if not args.run_dir:
            parser.error('--resume 需要 --run-dir')

        manifest = load_json(args.run_dir / 'manifest.json')
        initial = manifest['input']
        settings = manifest['settings']

    elif args.input:
        initial = load_json(args.input)
        settings = None

    else:
        # 一次粘贴完整任务，最后输入 END。
        initial = read_research_task()
        settings = None

    # 每次新任务使用独立目录，避免覆盖已有结果。
    run_name = (
        datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        + '_'
        + uuid4().hex[:6]
    )
    destination = args.run_dir or PROJECT_DIR / 'results' / run_name

    try:
        run_research(initial, destination, args.resume, settings)
        print(f'完成，结果在：{destination}')

    except Exception as error:
        print(
            f'未完成：{safe_error(error)}\n'
            '已有步骤已保存，修复问题后使用 --resume。'
        )
        raise SystemExit(1) from None

def read_research_task():
    """只需粘贴完整问题定义；仍兼容旧的双标题输入。"""
    while True:
        text = read_multiline(
            '粘贴完整问题定义（包含 Problem statement:；无需另填 Challenge）'
        )

        # 旧格式仍可读取；新格式无需 Problem definition: 外层标题。
        problem_part, separator, challenge = text.partition('\nChallenge:')
        prefix = 'Problem definition:'
        if problem_part.startswith(prefix):
            problem_part = problem_part[len(prefix):].strip()
        initial = {'problem_definition': problem_part}
        if separator:
            initial.update(challenge=challenge.strip(), challenge_source='user_provided')
        try:
            return prepare_research_input(initial)
        except ValueError as error:
            print(f'{error} 请重新输入。')

if __name__ == '__main__':
    main()
