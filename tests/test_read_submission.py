import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from paper2lark.errors import Paper2LarkError
from paper2lark.reading import (render_markdown, submit_read, validate_analysis,
                                validate_note_plan)
from paper2lark.runs import create_run, load_run
from paper2lark.runs import transition_run as real_transition_run
from paper2lark.sources import ingest_source, validate_source_input
from paper2lark.templates import snapshot_template, validate_role_map


class ReadSubmissionTests(unittest.TestCase):
    def test_malformed_analysis_types_return_contract_errors(self):
        paths = [('paper_type',), ('actual_coverage', 'main_text', 'status'),
                 ('claims', 0, 'type'), ('claims', 0, 'evidence', 0, 'source_id'),
                 ('claims', 0, 'evidence', 0, 'section_id')]
        for path in paths:
            value = json.loads(json.dumps(self.analysis))
            target = value
            for part in path[:-1]:
                target = target[part]
            target[path[-1]] = []
            with self.subTest(path=path), self.assertRaises(Paper2LarkError):
                validate_analysis(value, self.manifest, self.bundle, ['Machine Learning'])

    def test_malformed_note_types_return_contract_errors(self):
        for path in [('role_id',), ('blocks', 0, 'kind')]:
            value = json.loads(json.dumps(self.plan))
            target = value['sections'][0]
            for part in path[:-1]:
                target = target[part]
            target[path[-1]] = []
            with self.subTest(path=path), self.assertRaises(Paper2LarkError):
                validate_note_plan(value, self.manifest, self.bundle, self.analysis, self.roles)

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='p2l-submit-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.home = self.root / 'home'
        self.template = snapshot_template({
            'document_id': 'doc', 'revision_id': 'r2',
            'content': '# 核心结论\n请基于证据总结。\n# 个人思考（人工填写）',
        })
        request = {'schema_version': 1, 'persist_to_library': False,
                   'requested_depth': 'full', 'reader_preference': 'builtin',
                   'force_reread': False, 'record_id': 'recPaper'}
        self.run = create_run(self.home, request, self.template,
                              {'schema_version': 1, 'missing_work': ['source_bundle'],
                               'vocabulary': ['Machine Learning', 'AI Agents']})
        paper = self.root / 'paper.txt'
        paper.write_text('# Method\nWe train 3 models.\n# Results\nAccuracy is 91%.',
                         encoding='utf-8')
        ingest_source(self.home, self.run['run_id'], validate_source_input({
            'schema_version': 1, 'kind': 'full_text', 'path': str(paper),
            'original_location': 'local paper', 'metadata': {'title': '测试论文'},
        }, self.root))
        self.run_dir, self.manifest = load_run(self.home, self.run['run_id'])
        self.bundle = json.loads((self.run_dir / 'source.json').read_text(encoding='utf-8'))
        writable = [block for block in self.template['blocks'] if not block['human_only']]
        protected = [block for block in self.template['blocks'] if block['human_only']]
        self.roles = {'schema_version': 1,
                      'template_digest': self.template['content_digest'],
                      'template_revision': self.template['revision_id'],
                      'roles': [
                          {'role_id': 'core', 'selectors': [item['selector'] for item in writable],
                           'heading': '核心结论', 'ownership': 'ai',
                           'instructions': 'Evidence-linked summary.',
                           'variants': ['research', 'review', 'quick']},
                          {'role_id': 'personal', 'selectors': [item['selector'] for item in protected],
                           'heading': '个人思考', 'ownership': 'human',
                           'instructions': 'Preserve.', 'variants': []},
                      ]}
        validate_role_map(self.roles, self.template)
        self.analysis = {
            'schema_version': 1, 'run_id': self.run['run_id'],
            'paper_uid': self.run['paper_uid'], 'paper_type': 'research',
            'requested_depth': 'full', 'actual_coverage': self.bundle['coverage'],
            'takeaway': '该方法在测试中达到 91% 准确率。',
            'claims': [{
                'claim_id': 'claim-1', 'type': 'reported_result',
                'text': 'Accuracy is 91%.',
                'evidence': [{'source_id': 'source-1', 'section_id': 'section-0002',
                              'locator': {'kind': 'section', 'value': 'Results'}}],
            }],
            'ai_analysis': {'research_question': '如何训练模型？',
                            'methods': ['训练 3 个模型'], 'findings': ['准确率 91%'],
                            'limitations': ['仅有合成证据']},
            'keyword_proposal': {'selected_existing': ['Machine Learning'],
                                 'proposed_new': []},
        }
        self.plan = {
            'schema_version': 1, 'run_id': self.run['run_id'],
            'template_digest': self.template['content_digest'],
            'sections': [{'role_id': 'core', 'blocks': [
                {'kind': 'heading', 'level': 2, 'text': '核心结论'},
                {'kind': 'paragraph', 'text': self.analysis['takeaway']},
                {'kind': 'list', 'ordered': False, 'items': ['证据位置：Results']},
                {'kind': 'table', 'headers': ['指标', '结果'], 'rows': [['Accuracy', '91%']]},
                {'kind': 'callout', 'text': '覆盖范围见文末。'},
                {'kind': 'equation', 'latex': 'a = 0.91'},
            ]}],
        }

    def test_analysis_and_note_plan_render_deterministic_chinese_draft(self):
        analysis = validate_analysis(self.analysis, self.manifest, self.bundle,
                                     ['Machine Learning', 'AI Agents'])
        plan = validate_note_plan(self.plan, self.manifest, self.bundle,
                                  analysis, self.roles)
        first = render_markdown(self.manifest, self.bundle, analysis, plan, self.roles)
        second = render_markdown(self.manifest, self.bundle, analysis, plan, self.roles)
        self.assertEqual(first, second)
        self.assertIn('该方法在测试中达到 91% 准确率', first)
        self.assertIn('| 指标 | 结果 |', first)
        self.assertIn('## Provenance', first)
        self.assertIn(self.template['content_digest'], first)
        self.assertNotIn('个人思考', first)

    def test_analysis_rejects_bad_evidence_keywords_and_coverage_overclaim(self):
        bad_values = []
        no_evidence = json.loads(json.dumps(self.analysis))
        no_evidence['claims'][0]['evidence'] = []
        bad_values.append(no_evidence)
        bad_section = json.loads(json.dumps(self.analysis))
        bad_section['claims'][0]['evidence'][0]['section_id'] = 'missing'
        bad_values.append(bad_section)
        bad_keyword = json.loads(json.dumps(self.analysis))
        bad_keyword['keyword_proposal']['selected_existing'] = ['Unknown Label']
        bad_values.append(bad_keyword)
        overlong = json.loads(json.dumps(self.analysis))
        overlong['keyword_proposal']['proposed_new'] = [{
            'label': 'One Two Three Four', 'concept': 'x', 'reason': 'missing',
            'considered_existing': ['Machine Learning']}]
        bad_values.append(overlong)
        for value in bad_values:
            with self.subTest(value=value), self.assertRaises(Paper2LarkError):
                validate_analysis(value, self.manifest, self.bundle,
                                  ['Machine Learning', 'AI Agents'])

        abstract_bundle = json.loads(json.dumps(self.bundle))
        abstract_bundle['coverage']['main_text'] = {
            'status': 'unavailable', 'reason': 'Abstract only.'}
        overclaim = json.loads(json.dumps(self.analysis))
        overclaim['actual_coverage']['main_text'] = {
            'status': 'complete', 'reason': 'Read all.'}
        with self.assertRaises(Paper2LarkError) as raised:
            validate_analysis(overclaim, self.manifest, abstract_bundle,
                              ['Machine Learning'])
        self.assertEqual(raised.exception.code, 'ANALYSIS_INVALID')

    def test_coverage_states_cannot_hide_unavailable_as_not_applicable(self):
        analysis = json.loads(json.dumps(self.analysis))
        source = json.loads(json.dumps(self.bundle))
        source['coverage']['figures'] = {'status': 'unavailable', 'reason': 'Not inspected.'}
        analysis['actual_coverage']['figures'] = {
            'status': 'not_applicable', 'reason': 'Declared absent.'}
        with self.assertRaises(Paper2LarkError) as raised:
            validate_analysis(analysis, self.manifest, source, ['Machine Learning'])
        self.assertEqual(raised.exception.code, 'ANALYSIS_INVALID')

    def test_review_requires_synthesis_and_note_plan_cannot_target_human_role(self):
        review = json.loads(json.dumps(self.analysis))
        review['paper_type'] = 'review'
        with self.assertRaises(Paper2LarkError):
            validate_analysis(review, self.manifest, self.bundle,
                              ['Machine Learning'])
        review['ai_analysis'] = {'taxonomy': ['Theme A'],
                                 'evidence_synthesis': ['Evidence is mixed.'],
                                 'limitations': ['Narrow search.']}
        validate_analysis(review, self.manifest, self.bundle, ['Machine Learning'])

        invalid_plan = json.loads(json.dumps(self.plan))
        invalid_plan['sections'][0]['role_id'] = 'personal'
        analysis = validate_analysis(self.analysis, self.manifest, self.bundle,
                                     ['Machine Learning'])
        with self.assertRaises(Paper2LarkError) as raised:
            validate_note_plan(invalid_plan, self.manifest, self.bundle,
                               analysis, self.roles)
        self.assertEqual(raised.exception.code, 'NOTE_PLAN_INVALID')

        image_plan = json.loads(json.dumps(self.plan))
        image_plan['sections'][0]['blocks'].append(
            {'kind': 'image_reference', 'asset_id': 'figure-1', 'caption': 'Figure'})
        with self.assertRaises(Paper2LarkError) as raised:
            validate_note_plan(image_plan, self.manifest, self.bundle,
                               analysis, self.roles)
        self.assertEqual(raised.exception.code, 'NOTE_PLAN_INVALID')

    def test_note_sections_must_follow_template_role_order(self):
        writable = [block for block in self.template['blocks'] if not block['human_only']]
        ordered_roles = {'schema_version': 1,
                         'template_digest': self.template['content_digest'],
                         'template_revision': self.template['revision_id'],
                         'roles': [
                             {'role_id': 'first', 'selectors': [writable[0]['selector']],
                              'heading': 'First', 'ownership': 'ai',
                              'instructions': 'First section.', 'variants': ['research']},
                             {'role_id': 'second', 'selectors': [writable[1]['selector']],
                              'heading': 'Second', 'ownership': 'ai',
                              'instructions': 'Second section.', 'variants': ['research']},
                         ]}
        validate_role_map(ordered_roles, self.template)
        reversed_plan = {'schema_version': 1, 'run_id': self.run['run_id'],
                         'template_digest': self.template['content_digest'],
                         'sections': [
                             {'role_id': 'second', 'blocks': [
                                 {'kind': 'paragraph', 'text': 'Second.'}]},
                             {'role_id': 'first', 'blocks': [
                                 {'kind': 'paragraph', 'text': 'First.'}]},
                         ]}
        analysis = validate_analysis(self.analysis, self.manifest, self.bundle,
                                     ['Machine Learning'])
        with self.assertRaises(Paper2LarkError) as raised:
            validate_note_plan(reversed_plan, self.manifest, self.bundle,
                               analysis, ordered_roles)
        self.assertEqual(raised.exception.code, 'NOTE_PLAN_INVALID')

    def test_submit_resumes_after_crash_between_artifact_writes_and_transition(self):
        def interrupt_final_transition(*args, **kwargs):
            status = args[3]
            if status == 'drafted':
                raise Paper2LarkError('SIMULATED_CRASH', 'simulated')
            return real_transition_run(*args, **kwargs)

        with patch('paper2lark.reading.transition_run',
                   side_effect=interrupt_final_transition):
            with self.assertRaises(Paper2LarkError) as raised:
                submit_read(self.home, self.run['run_id'], self.analysis,
                            self.plan, self.roles)
        self.assertEqual(raised.exception.code, 'SIMULATED_CRASH')
        _, interrupted = load_run(self.home, self.run['run_id'])
        self.assertEqual(interrupted['status'], 'submitting')
        resumed = submit_read(self.home, self.run['run_id'], self.analysis,
                              self.plan, self.roles)
        self.assertEqual(resumed['status'], 'drafted')

    def test_submit_ignores_unmanifested_precreated_timestamp_file(self):
        injected = self.run_dir / 'submission.json'
        injected.write_text(json.dumps({
            'schema_version': 1,
            'generated_at': 'not-a-time\n- Injected provenance: yes',
        }), encoding='utf-8')
        result = submit_read(self.home, self.run['run_id'], self.analysis,
                             self.plan, self.roles)
        draft = Path(result['draft_path']).read_text(encoding='utf-8')
        self.assertNotIn('Injected provenance', draft)
        _, completed = load_run(self.home, self.run['run_id'])
        self.assertNotIn('submission', completed['artifacts'])

    def test_submit_rejects_tampered_manifested_submission_state(self):
        def interrupt_final_transition(*args, **kwargs):
            if args[3] == 'drafted':
                raise Paper2LarkError('SIMULATED_CRASH', 'simulated')
            return real_transition_run(*args, **kwargs)

        with patch('paper2lark.reading.transition_run',
                   side_effect=interrupt_final_transition):
            with self.assertRaises(Paper2LarkError):
                submit_read(self.home, self.run['run_id'], self.analysis,
                            self.plan, self.roles)
        run_path = self.run_dir / 'run.json'
        manifest = json.loads(run_path.read_text(encoding='utf-8'))
        manifest['submission']['generated_at'] = 'not-a-time\n- Injected provenance: yes'
        run_path.write_text(json.dumps(manifest), encoding='utf-8')
        with self.assertRaises(Paper2LarkError) as raised:
            submit_read(self.home, self.run['run_id'], self.analysis,
                        self.plan, self.roles)
        self.assertEqual(raised.exception.code, 'RUN_INVALID')


if __name__ == '__main__':
    unittest.main()
