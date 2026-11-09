"""检索、缓存和过滤。不会把目标论文当作参考答案检索回来。"""
import re
import time
from datetime import date
from pathlib import Path
import requests
from client import get_config
from tools import InsufficientEvidence
from client import fingerprint, load_json, save_json, safe_error

FIELDS = 'paperId,title,year,abstract,url,citationCount,publicationDate,externalIds'
URL = 'https://api.semanticscholar.org/graph/v1/paper/search'

#把输入的检索词整理成适合接口使用的普通关键词。它会去掉大写的 AND、`OR`、`NOT`，将标点等字符替换为空格，再统一转成小写、合并多余空格。
def normalize_query(query):
    if not isinstance(query, str):
        raise ValueError('查询词必须是文字。')
    # 该接口用普通关键词，不用 OR、引号或连字符语法。
    query = re.sub(r'\b(?:AND|OR|NOT)\b', ' ', query)
    query = re.sub(r'[^a-zA-Z0-9\s]', ' ', query)
    return ' '.join(query.lower().split())

#输入必须是列表；默认需要 4～8 条清理后不同的查询；不能有空查询；每条最多十个英文单词。
def normalize_queries(queries, minimum=4, maximum=8):
    if not isinstance(queries, list):
        raise ValueError('queries 必须是列表。')
    clean = list(dict.fromkeys(normalize_query(q) for q in queries))
    if not minimum <= len(clean) <= maximum or any(not q for q in clean):
        raise ValueError(f'需要 {minimum}–{maximum} 条不同的查询。')
    if any(len(q.split()) > 10 for q in clean):
        raise ValueError('每条查询最多十个英文单词。')
    return clean

#这个函数负责向 Semantic Scholar 发出实际请求。它会先清理查询词，并指定需要获取的论文信息，包括标题、年份、摘要、链接、引用数、发表日期以及外部标识等。默认每条查询获取 5 篇论文。
def search_papers(query, limit=5, cutoff='2025-12-31', cache_dir=None, pause=2):
    params = {'query': normalize_query(query), 'limit': limit, 'fields': FIELDS}
    if cutoff:
        date.fromisoformat(cutoff)
        params['publicationDateOrYear'] = ':' + cutoff
    cache = Path(cache_dir) / (fingerprint([URL, params]) + '.json') if cache_dir else None
    if cache and cache.exists():
        return load_json(cache)
    key = get_config().get('SEMANTIC_SCHOLAR_API_KEY', '').strip()
    if not key:
        raise ValueError('缺少 SEMANTIC_SCHOLAR_API_KEY。')
    # 请求失败最多重试两次，每次等待有上限。
    for attempt in range(3):
        time.sleep(pause)
        response = requests.get(URL, headers={'x-api-key': key}, params=params, timeout=30)
        if response.status_code == 200:
            payload = response.json()

            # 正常返回应当是一个 JSON 对象。
            if not isinstance(payload, dict):
                raise RuntimeError('Semantic Scholar 返回内容不是 JSON 对象。')

            if isinstance(payload.get('data'), list):
                # 找到论文时，从 data 读取结果。
                papers = payload['data']

            elif payload.get('total') == 0 and 'data' not in payload:
                # 没搜到论文时，接口可能只返回 total 和 offset。
                # 将它作为空结果，程序会继续搜索其他查询词。
                papers = []

            else:
                # 真正异常的返回交给外层记录，继续尝试其他查询。
                fields = ', '.join(payload.keys())
                raise RuntimeError(
                    f'Semantic Scholar 返回结构异常，包含字段：{fields}'
                )

            result = {
                'papers': papers,
                'retrieved_on': date.today().isoformat(),
                'params': params,
            }

            # 空结果也保存，避免重复请求同一个查询。
            if cache:
                save_json(cache, result)

            return result
        if response.status_code not in (429, 500, 502, 503, 504) or attempt == 2:
            raise RuntimeError(f'Semantic Scholar HTTP {response.status_code}')
        try:
            delay = float(response.headers.get('Retry-After', 5 * (attempt + 1)))
        except ValueError:
            delay = 10
        time.sleep(min(30, max(0, delay)))

#判断一篇论文能否保留
def paper_allowed(paper, state):
    external = paper.get('externalIds') or {}
    arxiv = re.sub(r'v\d+$', '', str(external.get('ArXiv', ''))).lower()
    target = re.sub(r'v\d+$', '', state.get('target_arxiv_id', '')).lower()
    if target and (arxiv == target or target in (paper.get('url') or '').lower()):
        return False
    title = lambda s: re.sub(r'\W+', '', s.casefold())
    if state.get('target_title') and title(paper.get('title') or '') == title(state['target_title']):
        return False
    cutoff = state.get('settings', {}).get('cutoff', '2025-12-31')
    if cutoff:
        published = paper.get('publicationDate')
        if published:
            if published > cutoff:
                return False
        else:
            year = paper.get('year')
            # 年份不够精确时，只接收能确定早于截止日的论文。
            last_year = int(cutoff[:4]) if cutoff.endswith('12-31') else int(cutoff[:4]) - 1
            if not isinstance(year, int) or year > last_year:
                return False
    return bool(paper.get('paperId'))

#组织整个检索流程
def collect_papers(state, queries, stage):
    settings = state['settings']
    path = Path(state['run_dir']) / 'retrieval' / f'{stage}.json'
    signature = fingerprint([queries, settings, state.get('target_arxiv_id'), state.get('target_title')])
    previous = load_json(path) if path.exists() else {}
    if previous and previous['signature'] != signature:
        raise ValueError('检索条件已变化，请使用新的运行目录。')
    history = previous.get('history', [])
    successful = {r['query']: r for r in history if r['status'] in ('success', 'empty')}
    history = []
    unique = {}
    # 主查询不足时，最多再试一组更短的查询；每条结果立即保存。
    fallback = list(dict.fromkeys(' '.join(q.split()[:3]) for q in queries))
    planned = list(queries)
    minimum = settings.get('min_abstracts', 3)
    for round_number, group in enumerate((planned, [q for q in fallback if q not in planned])):
        if round_number and sum(bool(p.get('abstract')) for p in unique.values()) >= minimum:
            break
        for query in group:
            record = successful.get(query)
            if record is None:
                print(f'检索 {stage}：{query}', flush=True)
                record = {'query': query, 'status': 'failed', 'papers': [], 'fallback': bool(round_number)}
                try:
                    response = search_papers(query, settings.get('papers_per_query', 5),
                                             settings.get('cutoff'), settings['cache_dir'],
                                             settings.get('request_pause', 2))
                    allowed = [p for p in response['papers'] if paper_allowed(p, state)]
                    record.update(status='success' if allowed else 'empty', papers=allowed,
                                  retrieved_on=response['retrieved_on'],
                                  filtered_count=len(response['papers']) - len(allowed))
                except (requests.RequestException, RuntimeError) as error:
                    record['error'] = safe_error(error)
            history.append(record)
            for paper in record['papers']:
                key = paper['paperId']
                if key not in unique or (not unique[key].get('abstract') and paper.get('abstract')):
                    unique[key] = paper
            save_json(path, {'signature': signature, 'history': history, 'papers': list(unique.values())})
    papers = list(unique.values())
    if not history or all(r['status'] == 'failed' for r in history):
        raise RuntimeError('全部检索失败，请检查接口和限流记录后恢复。')
    if sum(bool(p.get('abstract')) for p in papers) < minimum:
        raise InsufficientEvidence(f'{stage} 的有效摘要不足 {minimum} 篇；补检后仍不足。')
    return papers, history
