"""把界面与 Markdown 下载中的论文 ID 转成短引用；不修改原始研究数据。"""
import re
from collections.abc import Mapping
from urllib.parse import quote, urlsplit

ID_PATTERN = r"[0-9a-fA-F]{40}"
ID_RE = re.compile(r"\b" + ID_PATTERN + r"\b")
ID_GROUP = r"\s*" + ID_PATTERN + r"(?:\s*[,;]\s*" + ID_PATTERN + r")*\s*"
TOKEN_RE = re.compile(
    r"(?P<citation_link>\[(?:" + ID_GROUP + r")\]\(https?://[^\s)]+\))"
    r"|(?P<url>https?://[^\s<>]+)"
    r"|(?P<bracket>\[(?:" + ID_GROUP + r")\])"
    r"|(?P<bare>\b" + ID_PATTERN + r"\b)"
)
ID_LIST_KEYS = {"cited_paper_ids", "supporting_paper_ids", "source_paper_ids"}


def _texts(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for key, item in value.items():
            if key not in ID_LIST_KEYS:
                yield from _texts(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _texts(item)


def cited_ids(value):
    return list(dict.fromkeys(
        match.group().lower()
        for text in _texts(value)
        for match in ID_RE.finditer(text)
    ))


def _paper_url(paper, paper_id):
    url = paper.get("url")
    if isinstance(url, str):
        try:
            parsed = urlsplit(url)
            if parsed.scheme in ("http", "https") and parsed.netloc and not parsed.username:
                return quote(url, safe=":/?&=%#@+~!,$;")
        except ValueError:
            pass
    return f"https://www.semanticscholar.org/paper/{paper_id}"


def build_catalog(papers, content):
    metadata = {}
    for paper in papers:
        paper_id = str(paper.get("paperId") or "").lower()
        if not ID_RE.fullmatch(paper_id):
            continue
        merged = metadata.setdefault(paper_id, {})
        merged.update({key: value for key, value in paper.items() if value})

    catalog = {}
    for number, paper_id in enumerate(cited_ids(content), 1):
        paper = metadata.get(paper_id, {})
        catalog[paper_id] = {
            "number": number,
            "paperId": paper_id,
            "title": paper.get("title"),
            "year": paper.get("year"),
            "url": _paper_url(paper, paper_id),
        }
    return catalog


def format_citations(text, catalog):
    """单个、成组和未加括号的引用统一显示成 [1]；已有 URL 不改写。"""
    def replace(match):
        if match.lastgroup == "url":
            return match.group()
        source = match.group()
        if match.lastgroup == "citation_link":
            source = source.split("](", 1)[0]
        numbers = []
        for paper_id in ID_RE.findall(source):
            entry = catalog.get(paper_id.lower())
            if entry is None:
                return match.group()
            number = entry["number"]
            if number not in numbers:
                numbers.append(number)
        return "".join(f"[{number}]" for number in numbers)

    return TOKEN_RE.sub(replace, str(text))


def references_for(catalog, content):
    used = set(cited_ids(content))
    return sorted(
        (entry for paper_id, entry in catalog.items() if paper_id in used),
        key=lambda entry: entry["number"],
    )


def reference_markdown(entry):
    title = entry.get("title")
    if not title:
        return f"**[{entry['number']}]** Metadata unavailable for this stored citation."
    # 防止论文标题中的方括号等字符被解释成 Markdown 控制符。
    title = re.sub(r"([\\`*_{}\[\]<>])", r"\\\1", " ".join(str(title).split()))
    year = f" ({entry['year']})" if entry.get("year") else ""
    return f"**[{entry['number']}]** [{title}]({entry['url']}){year}"


def proposal_markdown(proposal, headings, catalog):
    blocks = [
        f"## {heading}\n\n{format_citations(proposal[key], catalog)}"
        for key, heading in headings.items() if proposal.get(key)
    ]
    references = references_for(catalog, {key: proposal.get(key) for key in headings})
    if references:
        blocks.append("## References\n\n" + "\n\n".join(map(reference_markdown, references)))
    return "\n\n".join(blocks)
