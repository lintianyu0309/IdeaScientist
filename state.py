"""所有步骤共用的数据。节点只返回自己新增的字段。"""
from typing import TypedDict


class ResearchState(TypedDict, total=False):
    sample_id: str
    problem_definition: str
    challenge: str
    challenge_source: str
    target_arxiv_id: str
    target_title: str
    settings: dict
    run_dir: str
    search_queries: list[str]
    papers: list[dict]
    search_history: list[dict]
    research_gap: dict
    selected_gap: str
    gap_selection_reason: str
    cross_domain_queries: list[str]
    cross_domain_papers: list[dict]
    cross_domain_search_history: list[dict]
    research_idea: dict
    research_proposal: dict

