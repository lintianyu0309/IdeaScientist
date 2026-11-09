"""这些测试不联网，重点检查恢复、隔离和引用边界。"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from experiments import dataset_adapter
import runner as gap_finder
import runner as innovator
import corpus as literature
import client as runtime
import tools
import runner as workflow
from runner import PROPOSAL_FIELDS, validate_proposal

PID = 'a' * 40
PAPER = {'paperId': PID, 'title': 'Prior work', 'year': 2024, 'abstract': 'A documented mechanism.'}


class HarnessTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def test_resume_keeps_completed_inner_steps(self):
        calls = []
        fail = [True]
        def plan(state):
            calls.append('plan')
            return {'selected_gap': 'gap_1'}
        def retrieve(state):
            calls.append('retrieve')
            return {'cross_domain_papers': [PAPER]}
        def idea(state):
            calls.append('idea')
            if fail[0]:
                raise ConnectionError('simulated disconnect')
            self.assertEqual(state['selected_gap'], 'gap_1')
            return {'research_idea': {'intuition': 'real upstream idea'}}
        steps = [('plan', plan), ('retrieve', retrieve), ('idea', idea)]
        initial = {'problem_definition': 'question', 'challenge': 'difficulty'}
        graph = workflow.build_workflow(self.root, steps)
        with self.assertRaises(ConnectionError):
            graph.invoke(initial)
        saved = runtime.load_json(self.root / 'state.json')
        self.assertEqual(saved['cross_domain_papers'], [PAPER])
        self.assertTrue((self.root / 'steps/idea/input.json').exists())
        fail[0] = False
        final = graph.invoke(initial)
        self.assertIn('research_idea', final)
        self.assertEqual(calls, ['plan', 'retrieve', 'idea', 'idea'])

    def test_checkpoint_rejects_changed_input(self):
        graph = workflow.build_workflow(self.root, [('plan', lambda s: {'search_queries': ['query']})])
        graph.invoke({'challenge': 'first'})
        with self.assertRaises(ValueError):
            graph.invoke({'challenge': 'changed'})

    def test_two_runs_do_not_share_outputs(self):
        for name in ('one', 'two'):
            graph = workflow.build_workflow(self.root / name, [('find', lambda s: {'selected_gap': s['challenge']})])
            graph.invoke({'challenge': name})
        for name in ('one', 'two'):
            self.assertEqual(runtime.load_json(self.root / name / 'state.json')['selected_gap'], name)

    def test_same_directory_cannot_run_twice(self):
        with workflow.run_lock(self.root):
            with self.assertRaises(ValueError):
                with workflow.run_lock(self.root):
                    pass
        self.assertFalse((self.root / '.running.lock').exists())

    def test_normalize_plain_search(self):
        self.assertEqual(literature.normalize_query('"cross-domain" OR retrieval'), 'cross domain retrieval')
        with self.assertRaises(ValueError):
            literature.normalize_queries(['same'] * 4)

    def test_target_and_future_papers_are_filtered(self):
        state = {'target_arxiv_id': '2601.00001', 'target_title': 'Exact Target',
                 'settings': {'cutoff': '2025-12-31'}}
        self.assertFalse(literature.paper_allowed({**PAPER, 'year': 2026}, state))
        self.assertFalse(literature.paper_allowed({**PAPER, 'externalIds': {'ArXiv': '2601.00001v2'}}, state))
        self.assertFalse(literature.paper_allowed({**PAPER, 'title': 'EXACT: Target'}, state))
        self.assertTrue(literature.paper_allowed(PAPER, state))

    def test_search_cache_avoids_second_request(self):
        class Response:
            status_code = 200
            def json(self):
                return {'data': [PAPER]}
        with patch('corpus.get_config', return_value={'SEMANTIC_SCHOLAR_API_KEY': 'test'}), \
             patch('corpus.requests.get', return_value=Response()) as get:
            first = literature.search_papers('plain query', cache_dir=self.root, pause=0)
            second = literature.search_papers('plain query', cache_dir=self.root, pause=0)
        self.assertEqual(first, second)
        self.assertEqual(get.call_count, 1)

    def test_partial_retrieval_reuses_success(self):
        path = self.root / 'retrieval/target.json'
        settings = {'cache_dir': str(self.root / 'cache'), 'min_abstracts': 1, 'cutoff': '2025-12-31'}
        state = {'run_dir': str(self.root), 'settings': settings}
        queries = ['first query', 'second query']
        signature = runtime.fingerprint([queries, settings, None, None])
        runtime.save_json(path, {'signature': signature, 'history': [
            {'query': 'first query', 'status': 'success', 'papers': [PAPER]}]})
        with patch('corpus.search_papers', return_value={'papers': [PAPER], 'retrieved_on': '2026-10-31'}) as search:
            literature.collect_papers(state, queries, 'target')
        self.assertEqual(search.call_count, 1)
        self.assertEqual(search.call_args.args[0], 'second query')

    def test_strict_json_does_not_accept_truncation(self):
        with self.assertRaises(ValueError):
            tools.parse_json('{"title": "unfinished')

    def test_model_raw_answer_can_be_reused_without_client(self):
        state = {'run_dir': str(self.root)}
        schema = {'value': 'text'}
        signature = runtime.fingerprint(['instruction', {}, schema, runtime.model_settings()])
        folder = self.root / 'model_calls/test'
        runtime.save_json(folder / 'request.json', {'signature': signature})
        (folder / 'answer_0.txt').write_text('{"value":"cached"}', encoding='utf-8')
        with patch('client.get_llm', side_effect=AssertionError('must not call API')):
            result = runtime.ask_json(state, 'test', 'instruction', {}, schema,
                                      lambda result: tools.require_fields(result, ['value']))
        self.assertEqual(result['value'], 'cached')

    def test_false_citations_are_rejected(self):
        proposal = {k: 'plain content' for k in PROPOSAL_FIELDS}
        proposal['related_work'] = '[' + PID + ']'
        proposal['cited_paper_ids'] = [PID]
        validate_proposal(proposal, [PAPER])
        proposal['cited_paper_ids'] = ['b' * 40]
        with self.assertRaises(ValueError):
            validate_proposal(proposal, [PAPER])
        with self.assertRaises(ValueError):
            tools.check_ids([PID], [{**PAPER, 'abstract': None}])

    def test_missing_body_citation_is_rejected(self):
        proposal = {k: 'plain content' for k in PROPOSAL_FIELDS}
        proposal['cited_paper_ids'] = [PID]
        with self.assertRaises(ValueError):
            validate_proposal(proposal, [PAPER])

    def test_dataset_uses_problem_not_answer(self):
        row = {'arxiv_id': 'id', 'title': 'paper', 'problem_definition':
               'Formal setting: task. Problem statement: observable failure. Why it matters: important.',
               'intuition': 'SECRET_SOLUTION', 'proposal_masked': 'SECRET_METHOD'}
        result = dataset_adapter.to_input(row)
        self.assertEqual(result['challenge'], 'observable failure.')
        self.assertNotIn('SECRET', json.dumps(result))
        row['problem_definition'] = 'no challenge provided'
        with self.assertRaises(ValueError):
            dataset_adapter.to_input(row)

    def test_single_problem_extracts_challenge_without_changing_input(self):
        problem = ('Formal setting: task. Inputs: query. Outputs: answer.\n\n'
                   'Problem statement: Observable failure..\nAnother limitation. '
                   'Why it matters: important.')
        original = {'problem_definition': problem}
        result = tools.prepare_research_input(original)
        self.assertEqual(result['problem_definition'], problem)
        self.assertEqual(result['challenge'], 'Observable failure..\nAnother limitation.')
        self.assertEqual(result['challenge_source'], 'problem_definition.Problem statement')
        self.assertNotIn('challenge', original)

    def test_single_problem_accepts_missing_motivation_and_heading_case(self):
        result = tools.prepare_research_input({
            'problem_definition': 'Formal setting: task.\nPROBLEM STATEMENT: Failure.'})
        self.assertEqual(result['challenge'], 'Failure.')

    def test_single_problem_rejects_missing_or_empty_statement(self):
        for problem in ('', 'Formal setting: task.',
                        'Problem statement: Why it matters: important.'):
            with self.subTest(problem=problem), self.assertRaises(ValueError):
                tools.prepare_research_input({'problem_definition': problem})

    def test_explicit_legacy_challenge_is_preserved(self):
        initial = {'problem_definition': 'task', 'challenge': 'custom challenge',
                   'challenge_source': 'user_provided'}
        self.assertEqual(tools.prepare_research_input(initial), initial)

    def test_cli_reads_single_pasted_problem_and_legacy_input(self):
        problem = 'Formal setting: task.\n\nProblem statement: failure. Why it matters: important.'
        with patch('builtins.input', side_effect=[*problem.splitlines(), 'END']):
            result = workflow.read_research_task()
        self.assertEqual(result['problem_definition'], problem)
        self.assertEqual(result['challenge'], 'failure.')
        with patch('builtins.input', side_effect=[
            'Problem definition: task', 'Challenge:', 'custom challenge', 'END',
        ]):
            legacy = workflow.read_research_task()
        self.assertEqual(legacy['problem_definition'], 'task')
        self.assertEqual(legacy['challenge'], 'custom challenge')

    def test_run_research_normalizes_single_input_before_graph_and_resume(self):
        problem = 'Formal setting: task. Problem statement: failure. Why it matters: important.'
        initial = {'problem_definition': problem}
        graph = workflow.build_workflow(self.root, [
            ('plan_search', lambda state: {'search_queries': [state['challenge']]}),
        ])
        with patch.object(workflow, 'build_workflow', return_value=graph), \
             patch.object(workflow, 'export_proposal'):
            first = workflow.run_research(initial, self.root)
            resumed = workflow.run_research(initial, self.root, resume=True)
        self.assertEqual(first['challenge'], 'failure.')
        self.assertEqual(resumed, first)
        self.assertEqual(runtime.load_json(self.root / 'input.json')['challenge'], 'failure.')

    def test_extra_answer_fields_are_refused(self):
        with self.assertRaises(ValueError):
            workflow.run_research({'problem_definition': 'q', 'challenge': 'c', 'intuition': 'answer'}, self.root)

    def test_insufficient_evidence_stops_without_repair_call(self):
        class Response:
            content = '{"status":"insufficient_evidence"}'
            usage_metadata = {}
            response_metadata = {}
        class Client:
            def invoke(self, messages):
                return Response()
        def validate(result):
            raise tools.InsufficientEvidence('not enough evidence')
        with patch('client.get_llm', return_value=Client()) as client:
            with self.assertRaises(tools.InsufficientEvidence):
                runtime.ask_json({'run_dir': str(self.root)}, 'test', 'instruction', {}, {}, validate)
        self.assertEqual(client.call_count, 1)


if __name__ == '__main__':
    unittest.main()
