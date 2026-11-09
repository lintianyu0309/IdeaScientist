# ============================================================
# 第一块：导入依赖、配置路径和历史案例。
# ============================================================

import json
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import streamlit as st


st.set_page_config(
    page_title="Research ideation system",
    layout="wide",
)

# app.py 与 runner.py 放在同一级目录。
ROOT = Path(__file__).resolve().parent
RESULTS = ROOT / "results"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import prepare_research_input


# 五个已有案例。案例一的补全报告位于独立子目录。
CASES = {
    "Case 1: Composable safety rules": (
        "case1_dispatch_fixed_01",
        "proposal_sections_v1",
    ),
    "Case 2: Sub-agent abstention": (
        "case2_ears_01",
        "",
    ),
    "Case 3: Multimodal speculative decoding": (
        "case3_tabed_01",
        "",
    ),
    "Case 4: Mathematical strategy selection": (
        "case4_02",
        "",
    ),
    "Case 5: Utility of additional computation": (
        "case5_02",
        "",
    ),
}

STEPS = {
    "plan_search": "Plan target-domain queries",
    "retrieve_literature": "Retrieve target-domain literature",
    "find_gap": "Gap Finder: Identify research gaps",
    "plan_innovation": "Select a gap and plan source searches",
    "retrieve_cross_domain": "Retrieve source mechanisms",
    "generate_idea": "Innovator: Generate a research idea",
    "write_proposal": "Report Writer: Develop the proposal",
}

HEADINGS = {
    "title": "Title",
    "topic_relevance": "Research area",
    "one_sentence_thesis": "One-sentence thesis",
    "core_problem": "Core problem",
    "key_novelty": "Key novelty",
    "problem_definition": "Problem definition",
    "key_terms": "Key terms",
    "related_work": "Related work",
    "method": "Method",
    "model_details": "Model and training details",
    "comparison_to_state_of_the_art": "Comparison with prior methods",
    "proposed_evaluation": "Proposed evaluation",
    "falsifiable_predictions": "Falsifiable predictions",
    "limitations": "Limitations",
}

LABELS = {
    "near_miss_analysis": "Closest prior methods",
    "deficiency": "Methodological deficiency",
    "why_it_matters": "Why it matters",
    "evidence_route": "Evidence route",
    "uncertainty": "Evidence limitations",
    "source_domain": "Source domain",
    "source_mechanism": "Source mechanism",
    "structural_mapping": "Structural mapping",
    "adaptation": "Adaptation",
    "transfer_assumption": "Transfer assumption",
    "novelty_vs_prior": "Difference from prior work",
    "testable_prediction": "Testable prediction",
    "main_risk": "Main risk",
}


# ============================================================
# 第二块：读取已有结果，不调用模型、不修改历史数据。
# ============================================================

def read_json(path):
    """文件尚未生成时返回空字典。"""
    if not path.exists():
        return {}

    try:
        return json.loads(
            path.read_text(encoding="utf-8-sig")
        )
    except (OSError, ValueError):
        st.warning(f"Unable to read: {path.name}")
        return {}


def load_result(run_dir, proposal_dir):
    """读取共享状态、最终报告和原始研究输入。"""
    state = read_json(run_dir / "state.json")

    # 案例一还需要读取补全目录中的状态。
    if proposal_dir != run_dir:
        state.update(
            read_json(proposal_dir / "state.json")
        )

    proposal = read_json(
        proposal_dir / "research_proposal.json"
    )

    if not proposal:
        proposal = state.get("research_proposal", {})

    initial = read_json(run_dir / "input.json")

    if not initial:
        manifest = read_json(run_dir / "manifest.json")
        initial = manifest.get("input", {})

    return state, proposal, initial


def show_fields(data, keys, citations=None):
    """将数据字段按英文标签展示。"""
    for key in keys:
        value = data.get(key)

        if value:
            st.markdown(
                f"**{LABELS.get(key, key)}**"
            )
            st.markdown(format_citations(value, citations) if citations is not None else str(value))


# ============================================================
# 第三块：结果展示区。
# ============================================================



# 名称只保存在 ui_meta.json 中，原目录、manifest 和模型输入保持原样。
from ui.run_management import get_run_name, set_run_name, trash_run, restore_run
from ui.citation_display import build_catalog, format_citations, references_for, reference_markdown, proposal_markdown


def show_references(catalog, content):
    entries = references_for(catalog, content)
    if not entries:
        return
    st.subheader("References")
    for entry in entries:
        st.markdown(reference_markdown(entry))
    with st.expander("Reference identifiers"):
        st.json({f"[{item['number']}]": item["paperId"] for item in entries}, expanded=False)



def display_name(run_dir):
    fallback = next(
        (label for label, (folder, _) in CASES.items() if folder == run_dir.name),
        run_dir.name,
    )
    return get_run_name(run_dir, fallback)


def active_job_dir():
    future = st.session_state.get("job")
    if future is not None and not future.done():
        return st.session_state.get("job_dir") or None
    return None


def render_run_management(run_dir):
    with st.popover("Manage run", width="stretch"):
        with st.form(f"rename_form_{run_dir.name}"):
            new_name = st.text_input(
                "Run name", value=display_name(run_dir), max_chars=80,
                key=f"rename_input_{run_dir.name}",
            )
            rename = st.form_submit_button("Save name", key=f"save_name_{run_dir.name}")
        if rename:
            try:
                set_run_name(RESULTS, run_dir, new_name)
            except (ValueError, OSError) as error:
                st.error(str(error) if isinstance(error, ValueError) else "Unable to save the name.")
            else:
                st.session_state["flash_message"] = "Run renamed."
                st.rerun()

        active = active_job_dir()
        locked = (run_dir / ".running.lock").exists()
        running_here = bool(active and Path(active).resolve() == run_dir.resolve())
        if st.button(
            "Delete run", icon=":material/delete:",
            key=f"delete_run_{run_dir.name}", disabled=locked or running_here,
            width="stretch",
        ):
            try:
                record = trash_run(RESULTS, run_dir, active_run=active)
            except (ValueError, OSError) as error:
                st.error(str(error) if isinstance(error, ValueError) else "Unable to delete this run.")
            else:
                st.session_state["undo_delete"] = record
                st.session_state["flash_message"] = "Run deleted."
                if st.session_state.get("job_dir") == str(run_dir):
                    st.session_state["job"] = None
                    st.session_state["job_dir"] = ""
                    st.session_state["job_finished_seen"] = False
                st.rerun()


def show_result(run_dir, proposal_dir):
    if not run_dir.exists():
        st.info("No run files have been saved yet.")
        return

    state, proposal, initial = load_result(
        run_dir,
        proposal_dir,
    )

    status = read_json(
        run_dir / "run_status.json"
    )

    gap = state.get("research_gap", {})
    idea = state.get("research_idea", {})

    # 正文短引用先按 Proposal 的出现顺序编号；其他页面复用同一编号。
    # 只创建展示映射，不替换 state 和原始 JSON 中的 paperId。
    catalog = build_catalog(
        state.get("papers", []) + state.get("cross_domain_papers", []),
        [{key: proposal.get(key) for key in HEADINGS}, gap, idea],
    )

    name_col, action_col = st.columns([4, 1], vertical_alignment="bottom")
    with name_col:
        st.subheader(display_name(run_dir))
    with action_col:
        render_run_management(run_dir)

    # 展示真实候选文献数量和报告完整度。
    cols = st.columns(3)

    cols[0].metric(
        "Target-domain candidates",
        len(state.get("papers", [])),
    )

    cols[1].metric(
        "Source candidates",
        len(state.get("cross_domain_papers", [])),
    )

    filled = sum(
        bool(proposal.get(key))
        for key in HEADINGS
    )

    cols[2].metric(
        "Nonempty proposal fields",
        f"{filled}/14",
    )

    # 根据真实检查点计算进度，不使用模拟进度。
    completed = 0
    rows = []

    for name, label in STEPS.items():
        saved = (
            run_dir
            / "steps"
            / name
            / "output.json"
        )

        if saved.exists():
            completed += 1
            progress = "Output saved"

        elif (
            name == "write_proposal"
            and proposal
            and proposal_dir != run_dir
        ):
            progress = "Report completed in a separate directory"

        elif status.get("active_step") == name:
            if status.get("status") == "running":
                progress = "Running"
            else:
                progress = "Interrupted here"

        else:
            progress = "No saved output"

        rows.append({
            "Stage": label,
            "Status": progress,
        })

    st.progress(
        completed / len(STEPS),
        text=(
            f"Saved main-workflow steps: "
            f"{completed}/{len(STEPS)}"
        ),
    )

    with st.expander("View stage status"):
        st.table(
            rows,
            alt="Actual saved status of each workflow stage",
        )

    view = st.selectbox(
        "View",
        [
            "Research input",
            "Retrieved literature",
            "Research Gap",
            "Research Idea",
            "Research Proposal",
            "Stage inputs and outputs",
            "Comparison with published examples",
        ],
        key=f"view_{run_dir.name}",
    )

    # ---------- 1. 原始研究输入 ----------
    if view == "Research input":
        st.subheader("Research problem")

        st.markdown(
            initial.get(
                "problem_definition",
                state.get(
                    "problem_definition",
                    "No input available",
                ),
            )
        )

        st.subheader("Challenge")

        st.markdown(
            initial.get(
                "challenge",
                state.get(
                    "challenge",
                    "No input available",
                ),
            )
        )

    # ---------- 2. 检索文献 ----------
    elif view == "Retrieved literature":
        group = st.segmented_control(
            "Literature group",
            [
                "Target domain",
                "Source mechanisms",
            ],
            default="Target domain",
            required=True,
        )

        if group == "Target domain":
            papers = state.get("papers", [])
        else:
            papers = state.get(
                "cross_domain_papers",
                [],
            )

        st.caption(
            "This is the retrieved candidate pool. "
            "See Stage inputs and outputs for the materials "
            "actually sent to the model."
        )

        if not papers:
            st.info(
                "No papers have been saved for this group yet."
            )

        for number, paper in enumerate(papers, 1):
            title = (
                paper.get("title")
                or "Untitled paper"
            )
            year = (
                paper.get("year")
                or "Year unknown"
            )

            with st.expander(
                f"{number}. {title} ({year})"
            ):
                st.markdown(
                    paper.get("abstract")
                    or "No abstract available."
                )

                st.caption(
                    f"paperId: "
                    f"{paper.get('paperId', '')}"
                )

                # 兼容接口返回 url=None 的情况。
                url = paper.get("url") or ""

                if url.startswith(
                    ("https://", "http://")
                ):
                    st.link_button(
                        "Open paper",
                        url,
                    )

    # ---------- 3. Research Gap ----------
    elif view == "Research Gap":
        if not gap:
            st.info(
                "Gap Finder has not saved its output yet."
            )

        st.markdown(format_citations(gap.get('summary', ''), catalog))

        for item in gap.get("gaps", []):
            with st.container(border=True):
                st.subheader(
                    item.get("gap_id", "Gap")
                )

                st.markdown(format_citations(item.get('statement', ''), catalog))

                show_fields(
                    item,
                    [
                        "near_miss_analysis",
                        "deficiency",
                        "why_it_matters",
                        "evidence_route",
                        "uncertainty",
                    ],
                    citations=catalog,
                )

    # ---------- 4. Research Idea ----------
    elif view == "Research Idea":
        if not idea:
            st.info(
                "Innovator has not saved its output yet."
            )

        else:
            selected = idea.get(
                "selected_gap",
                state.get("selected_gap", ""),
            )

            st.caption(
                f"Selected gap: {selected}"
            )

            st.subheader("Central idea")

            st.markdown(format_citations(idea.get('intuition', ''), catalog))

            show_fields(
                idea,
                [
                    "source_domain",
                    "source_mechanism",
                    "structural_mapping",
                    "adaptation",
                    "transfer_assumption",
                    "novelty_vs_prior",
                    "testable_prediction",
                    "main_risk",
                ],
                citations=catalog,
            )

    # ---------- 5. Research Proposal ----------
    elif view == "Research Proposal":
        if not proposal:
            st.info(
                "Report Writer has not saved "
                "a complete proposal yet."
            )

        else:
            st.download_button(
                "Download proposal JSON",
                json.dumps(
                    proposal,
                    ensure_ascii=False,
                    indent=2,
                ),
                file_name="research_proposal.json",
                mime="application/json",
                on_click="ignore",
            )

            st.download_button(
                "Download proposal Markdown",
                proposal_markdown(proposal, HEADINGS, catalog),
                file_name="research_proposal_readable.md",
                mime="text/markdown",
                on_click="ignore",
            )

            for key, heading in HEADINGS.items():
                if proposal.get(key):
                    st.subheader(heading)
                    st.markdown(format_citations(proposal[key], catalog))

    # ---------- 6. 节点与模型的真实输入输出 ----------
    elif view == "Stage inputs and outputs":
        stage = st.selectbox(
            "Select a stage",
            list(STEPS),
            format_func=lambda x: STEPS[x],
        )

        folder = run_dir / "steps" / stage

        st.caption(
            "The node receives the shared state. "
            "Its output update contains the fields "
            "passed to the next node."
        )

        with st.expander(
            "Shared state received by the node"
        ):
            st.json(
                read_json(folder / "input.json"),
                expanded=False,
            )

        with st.expander(
            "Fields returned by the node"
        ):
            st.json(
                read_json(folder / "output.json"),
                expanded=False,
            )

        # request.json 展示实际传给模型的 materials 和 schema。
        if stage == "write_proposal":
            call_root = proposal_dir
            pattern = "proposal_*/request.json"
        else:
            call_root = run_dir
            pattern = f"{stage}/request.json"

        requests = sorted(
            (call_root / "model_calls").glob(pattern)
        )

        if requests:
            request = st.selectbox(
                "Select a recorded model call",
                requests,
                format_func=lambda x: x.parent.name,
            )

            with st.expander(
                "Actual model materials and output schema"
            ):
                st.json(
                    read_json(request),
                    expanded=False,
                )

        else:
            st.caption(
                "No model-call record was found here. "
                "Literature retrieval nodes do not "
                "call the language model."
            )

    # ---------- 7. 论文已公开案例的机制对照 ----------
    elif view == "Comparison with published examples":
        comparisons = {
            "case1_dispatch_fixed_01": [
                {
                    "Approach": "Human paper: MOSAIC",
                    "Mechanism": (
                        "Learn control tokens for each rule "
                        "category and compose the tokens "
                        "for the active rules."
                    ),
                },
                {
                    "Approach": "IdeaScientist Base",
                    "Mechanism": (
                        "Model rule interactions with a "
                        "Set Transformer and modulate "
                        "hidden signals with FiLM."
                    ),
                },
                {
                    "Approach": "IdeaScientist Trained",
                    "Mechanism": (
                        "Learn per-rule boundaries and move "
                        "activations toward the joint feasible "
                        "region of the active rules."
                    ),
                },
            ],
            "case2_ears_01": [
                {
                    "Approach": "Human paper: EARS",
                    "Mechanism": (
                        "Train sub-agents to return an answer "
                        "or structured abstention "
                        "with a rationale."
                    ),
                },
                {
                    "Approach": "IdeaScientist Base",
                    "Mechanism": (
                        "Check and revise abstention reasons "
                        "for necessity and relevance."
                    ),
                },
                {
                    "Approach": "IdeaScientist Trained",
                    "Mechanism": (
                        "Match request requirements against "
                        "a live capability inventory "
                        "before generation."
                    ),
                },
            ],
            "case3_tabed_01": [
                {
                    "Approach": "Human paper: TABED",
                    "Mechanism": (
                        "Retain multiple drafting paths "
                        "and update mixture weights online "
                        "from verification feedback."
                    ),
                },
                {
                    "Approach": "IdeaScientist Base",
                    "Mechanism": (
                        "Compare visual and text-only draft "
                        "distributions and use their divergence "
                        "to gate visual information."
                    ),
                },
                {
                    "Approach": "IdeaScientist Trained",
                    "Mechanism": (
                        "Select a drafting path using "
                        "the target model's visual attention "
                        "during prefill."
                    ),
                },
            ],
        }

        reference = comparisons.get(
            run_dir.name
        )

        if reference:
            st.caption(
                "These mechanism summaries come from "
                "Appendix Tables 13, 15 and 16 and "
                "support qualitative comparison."
            )

            st.table(
                reference,
                alt=(
                    "Core mechanisms of the published "
                    "same-task proposals"
                ),
            )

            st.subheader(
                "Idea generated by this system"
            )

            st.markdown(format_citations(idea.get('intuition', 'No idea available yet'), catalog))

            show_fields(
                idea,
                [
                    "adaptation",
                    "main_risk",
                ],
                citations=catalog,
            )

            st.markdown(
                "[Read the IdeaScientist paper]"
                "(https://arxiv.org/abs/2610.04074)"
            )

        else:
            st.info(
                "The appendix does not show a same-task "
                "output for this example. It is included "
                "as an additional case study."
            )

    # 参考文献附在对应结果正文之后，标题、年份和链接来自已有检索记录。
    if view == "Research Gap":
        show_references(catalog, gap)
    elif view in ("Research Idea", "Comparison with published examples"):
        show_references(catalog, idea)
    elif view == "Research Proposal":
        show_references(catalog, {key: proposal.get(key) for key in HEADINGS})


# ============================================================
# 第四块：后台执行原有工作流，并读取真实进度。
# ============================================================

# ============================================================
# 第四块：执行新任务，或从原目录恢复任务。
# ============================================================

def execute_job(initial, run_dir, resume=False):
    """恢复时读取原 manifest，保持相同输入和设置。"""
    try:
        from workflow import run_research

        if resume:
            # 恢复必须沿用原输入、原目录和原检索设置。
            manifest = json.loads(
                (run_dir / "manifest.json").read_text(
                    encoding="utf-8-sig"
                )
            )

            run_research(
                manifest["input"],
                run_dir,
                resume=True,
                settings=manifest["settings"],
            )

        else:
            run_research(initial, run_dir)

        return {"ok": True}

    except Exception as error:
        try:
            from client import safe_error
            detail = safe_error(error)

        except Exception:
            detail = {
                "type": type(error).__name__,
                "message": (
                    "Please check the terminal "
                    "used to start this app."
                ),
            }

        return {
            "ok": False,
            "error": detail,
        }


def start_job(initial, run_dir, resume=False, run_name=""):
    """明确点击按钮后，才提交一次后台任务。"""
    future = st.session_state.get("job")

    if future is not None and not future.done():
        st.warning(
            "A task is already running in this session."
        )
        return

    if not resume:
        try:
            # 新名称不参与目录命名，也不会传入 Agent。
            root = RESULTS.resolve()
            if run_dir.resolve().parent != root or run_dir.exists():
                raise ValueError("Choose a new run directory inside results.")
            run_dir.mkdir(parents=True, exist_ok=False)
            default_name = "Research run " + datetime.now(
                timezone(timedelta(hours=8))
            ).strftime("%Y-%m-%d %H:%M:%S")
            set_run_name(RESULTS, run_dir, run_name.strip() or default_name)
        except (ValueError, OSError) as error:
            st.error(str(error) if isinstance(error, ValueError) else "Unable to create this run.")
            return

    if "executor" not in st.session_state:
        st.session_state["executor"] = (
            ThreadPoolExecutor(max_workers=1)
        )

    st.session_state["job_dir"] = str(run_dir)
    st.session_state["job_finished_seen"] = False

    st.session_state["job"] = (
        st.session_state["executor"].submit(
            execute_job,
            initial,
            run_dir,
            resume,
        )
    )

    st.rerun()


def check_resume(run_dir):
    """检查能否恢复，不删除锁、不修改配置、不调用 API。"""
    manifest = read_json(
        run_dir / "manifest.json"
    )

    status = read_json(
        run_dir / "run_status.json"
    )

    if status.get("status") == "complete":
        return False, "This run is already complete."

    # 目录正在被使用时，不允许再次启动。
    if (run_dir / ".running.lock").exists():
        return False, (
            "This run is active, or a lock remains "
            "after an interrupted process. "
            "Do not remove the lock while a process "
            "is still using this directory."
        )

    required = (
        "input",
        "settings",
        "model",
        "code_hash",
    )

    if not all(key in manifest for key in required):
        return False, (
            "This directory does not contain "
            "a compatible manifest."
        )

    # 证据不足属于内容问题，不能当作普通网络错误重试。
    if status.get("status") == "insufficient_evidence":
        return False, (
            "The run stopped because evidence was insufficient. "
            "Review the retrieval results before "
            "starting a revised run."
        )

    try:
        from runner import code_fingerprint
        from client import model_settings

        # 保留原有版本校验，避免混合不同实验。
        if manifest["code_hash"] != code_fingerprint():
            return False, (
                "The research code has changed. "
                "Restore the original version to resume "
                "this run, or start a new run."
            )

        current = model_settings()
        saved = manifest["model"]

        changed = sorted(
            key
            for key in set(saved) | set(current)
            if saved.get(key) != current.get(key)
        )

        if changed:
            message = (
                "Restore the saved model configuration. "
                "Changed fields: "
                + ", ".join(changed)
                + "."
            )

            if "max_tokens" in changed:
                message += (
                    f" Saved max_tokens="
                    f"{saved.get('max_tokens')}; "
                    f"current="
                    f"{current.get('max_tokens')}."
                )

            return False, message

    except Exception as error:
        return False, (
            "Unable to check resume settings "
            f"({type(error).__name__})."
        )

    return True, (
        "Saved steps will be reused; execution continues "
        "at the first unfinished step."
    )


# 页面定时刷新只读取结果，不会重复提交任务。
@st.fragment(run_every="2s")
def monitor_job():
    future = st.session_state.get("job")

    if future is None:
        return

    run_dir = Path(
        st.session_state["job_dir"]
    )

    if future.done():
        if not st.session_state["job_finished_seen"]:
            st.session_state["job_finished_seen"] = True
            st.rerun()

        result = future.result()

        if result["ok"]:
            st.success(
                "The run is complete and "
                "its results have been saved."
            )

        else:
            error = result["error"]

            st.error(
                f"Run did not complete "
                f"({error['type']})."
            )

            with st.expander("Original error details"):
                st.json(
                    error,
                    expanded=False,
                )

            st.caption(
                "Saved progress is preserved. "
                "Select 'Resume a saved run' in the sidebar "
                "to continue from the checkpoint."
            )

    else:
        status = read_json(
            run_dir / "run_status.json"
        )

        stage = status.get("active_step")

        st.info(
            f"Running: "
            f"{STEPS.get(stage, 'Initializing')}. "
            "The view below updates from saved results."
        )

    show_result(
        run_dir,
        run_dir,
    )


# ============================================================
# 第五块：页面入口，增加第三种“恢复任务”模式。
# ============================================================


st.session_state.setdefault("job", None)
st.session_state.setdefault("job_dir", "")
st.session_state.setdefault("job_finished_seen", False)
st.session_state.setdefault("undo_delete", None)

if message := st.session_state.pop("flash_message", None):
    st.toast(message)

st.title("Literature-grounded research ideation")

# 删除可撤销；只恢复到原位置，不覆盖已存在的任务。
if st.session_state["undo_delete"]:
    if st.sidebar.button("Undo delete", key="undo_delete_button"):
        try:
            restore_run(RESULTS, st.session_state["undo_delete"])
        except (ValueError, OSError) as error:
            st.sidebar.error(str(error) if isinstance(error, ValueError) else "Unable to restore this run.")
        else:
            st.session_state["undo_delete"] = None
            st.session_state["flash_message"] = "Run restored."
            st.rerun()

mode = st.sidebar.radio(
    "Demo mode", ["Browse saved cases", "Run a new task", "Resume a saved run"],
)
future = st.session_state["job"]
busy = future is not None and not future.done()

if mode == "Browse saved cases":
    # 过滤掉已删除的目录；显示名称与实际目录名分离。
    choices = {
        folder: subfolder for _, (folder, subfolder) in CASES.items()
        if (RESULTS / folder).is_dir()
    }
    for folder in sorted(RESULTS.glob("web_*"), reverse=True):
        if folder.is_dir():
            choices[folder.name] = ""
    if not choices:
        st.info("No saved runs found.")
    else:
        selected = st.sidebar.selectbox(
            "Select a case", list(choices),
            format_func=lambda name: display_name(RESULTS / name), key="history_case",
        )
        run_dir = RESULTS / selected
        proposal_dir = run_dir / choices[selected]
        if choices[selected]:
            st.caption(
                "Case 1 stopped at report writing in the original run. "
                "This view also includes its separately completed report."
            )
        show_result(run_dir, proposal_dir)

elif mode == "Run a new task":
    with st.form("research_input"):
        run_name = st.text_input(
            "Run name (optional)", max_chars=80, key="new_run_name",
            placeholder="e.g. MOSAIC method proposal",
        )
        problem = st.text_area(
            "Problem definition (English)", height=280,
            placeholder=(
                "Formal setting: ... Inputs: ... Outputs: ...\n\n"
                "Problem statement: ... Why it matters: ..."
            ),
            help="Paste the complete problem definition. The challenge is extracted from Problem statement automatically.",
            key="new_problem_definition",
        )
        submitted = st.form_submit_button("Start run", type="primary", disabled=busy)
    if submitted and not busy:
        try:
            initial = prepare_research_input({"problem_definition": problem.strip()})
        except ValueError as error:
            st.error(str(error))
        else:
            stamp = datetime.now(timezone(timedelta(hours=8))).strftime("%Y%m%d_%H%M%S")
            run_dir = RESULTS / f"web_{stamp}_{uuid4().hex[:6]}"
            initial["sample_id"] = run_dir.name
            start_job(initial, run_dir, run_name=run_name)
    monitor_job()

else:
    st.subheader("Resume a saved run")
    runs = [
        folder for folder in sorted(RESULTS.glob("web_*"), reverse=True)
        if (folder / "manifest.json").exists()
    ]
    if not runs:
        st.info("No app runs with a saved manifest were found.")
    else:
        selected = st.selectbox(
            "Select a saved run", runs, format_func=display_name, key="resume_case",
        )
        saved_status = read_json(selected / "run_status.json")
        st.caption(
            f"Saved status: {saved_status.get('status', 'unknown')} | "
            f"Last recorded stage: {saved_status.get('active_step', 'none')}"
        )
        allowed, reason = check_resume(selected)
        st.info(reason)
        if st.button("Resume this run", type="primary", disabled=busy or not allowed):
            start_job(None, selected, resume=True)
        if st.session_state.get("job_dir") == str(selected) and st.session_state.get("job") is not None:
            monitor_job()
        else:
            show_result(selected, selected)
