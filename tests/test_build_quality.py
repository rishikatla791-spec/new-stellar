"""Offline acceptance gates: plans, failure recovery, browser evidence and research."""
import json
import sqlite3
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app as A
from agent_workflow import BuildRun, is_build_task
from build_quality import validate_plan


def brief(interface='web'):
    return json.dumps({'interface':interface,'requirements':[
        {'acceptance':'A second booking cannot take an occupied slot',
         'check':'Run concurrent booking requests and assert one success'}],
        'design':{key:'Product-specific design decision' for key in
                  ('direction','layout','typography','states','accessibility','motion')}})


class QualityTests(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:');self.db.row_factory=sqlite3.Row
        self.addCleanup(self.db.close)
        self.run=BuildRun(self.db,'quality',1,1,'Build and deploy a booking website')
        self.project='abcdef123456'

    def planned(self):
        self.run.observe('build_plan',{'plan':brief()},'{}',False)
        self.run.observe('design_research',{'category':'booking'},'{}',False)
        self.run.evidence['projects'][self.project]={'tests':True,'served':True,'verified':True}

    def test_malformed_and_oversized_plans_rejected(self):
        for value in (None, [], 'x'*16001, '{}', '[]', '{',
                      json.dumps({'interface':'none','requirements':[{'acceptance':True,'check':'test'}]})):
            with self.subTest(value=str(value)[:40]), self.assertRaises((ValueError,TypeError)):
                validate_plan(value)
        self.assertIsInstance(A.build_plan('{}','Planning'),A.ToolFailure)

    def test_polite_build_requests_activate_workflow_but_advice_does_not(self):
        for request in ('Can you build a booking website?', 'Could you please create a dashboard?',
                        'Please redesign the frontend', 'Develop an application'):
            self.assertTrue(is_build_task(request),request)
        for request in ('Can New Stellar build a website?', 'How would you build this app?',
                        'Explain how to develop a frontend', 'What should I create?'):
            self.assertFalse(is_build_task(request),request)

    def test_web_design_required_but_api_needs_no_visual_direction(self):
        plan=json.loads(brief());del plan['design']['accessibility']
        with self.assertRaises(ValueError):validate_plan(json.dumps(plan))
        self.assertEqual(validate_plan(brief('none'))['design'],{})

    def test_model_claims_cannot_set_test_evidence(self):
        plan=json.loads(brief());plan['tests']=True;plan['verified']=True
        self.assertNotIn('tests',validate_plan(json.dumps(plan)))
        self.run.observe('build_plan',{'plan':json.dumps(plan)},'{}',False)
        self.assertFalse(self.run.evidence['tests'])
        self.assertIn('attempt design_research', ' '.join(self.run.missing()))

    def test_plan_and_browser_receipts_survive_resume(self):
        self.planned();self.run.evidence['browser_available']=True
        self.assertEqual(len(self.run.missing()),2)
        for mobile in (False,True):
            self.run.observe('browser_test',{'app_id':self.project,'mobile':mobile},'{"passed":true}',False)
        self.assertEqual(self.run.missing(),[])
        resumed=BuildRun(self.db,'resumed',1,1,self.run.request,self.run.evidence)
        self.assertEqual(resumed.missing(),[])

    def test_failed_or_malformed_browser_result_never_passes(self):
        self.planned();self.run.evidence['browser_available']=True
        for result,failed in [('{"passed":false}',False),('{"passed":true}',True),('[]',False),('not json',False),('{"passed":1}',False)]:
            self.run.observe('browser_test',{'app_id':self.project},result,failed)
            self.assertIn(self.project+': run browser_test for the primary journey on desktop',self.run.missing())

    def test_source_change_invalidates_browser_evidence_even_on_error(self):
        self.planned();self.run.evidence['browser_available']=True
        self.run.observe('browser_test',{'app_id':self.project},'{"passed":true}',False)
        self.run.observe('repo_control',{'app_id':self.project,'action':'execute','command':'edit'},'failed',True)
        self.assertEqual(self.run.evidence['projects'][self.project]['browser'],{})

    def test_repeat_failures_ignore_progress_label_and_clear_on_success(self):
        for index in range(3):
            self.run.observe('repo_control',{'action':'execute','command':'same','status':str(index)},'error',True)
        self.assertIn('REPEATED FAILURE',self.run.context_note())
        self.run.observe('repo_control',{'action':'execute','command':'same'},'done',False)
        self.assertNotIn('REPEATED FAILURE',self.run.context_note())
        self.assertNotIn('Last failed',self.run.context_note())

    def test_failure_memory_is_bounded_and_does_not_store_commands(self):
        for index in range(40):self.run.observe('repo_control',{'command':'private '+str(index)},'error',True)
        self.assertEqual(len(self.run.evidence['failures']),32)
        self.assertNotIn('private',json.dumps(self.run.evidence))

    def test_no_browser_and_unavailable_research_are_reported_without_deadlock(self):
        self.planned()
        self.run.observe('design_research',{'category':'booking'},'unavailable',True)
        self.assertFalse(self.run.missing())
        self.assertIn('Browser checks unavailable',self.run.context_note())

    def test_successful_research_receipt_is_persisted_immediately(self):
        self.run.observe('design_research',{'category':'booking'},'{}',False)
        saved=json.loads(self.db.execute('SELECT evidence FROM agent_build_runs WHERE query_id=?',('quality',)).fetchone()[0])
        self.assertTrue(saved['design_research']['available'])

    def test_legacy_runs_are_resumable_without_new_plan_gate(self):
        legacy=BuildRun(self.db,'legacy',1,1,'old',{'projects':{self.project:{'tests':True,'served':True,'verified':True}}})
        self.assertFalse(legacy.missing())

    def test_mixed_test_results_are_not_a_pass(self):
        for result in ('Ran 2 tests\nFAILED (failures=1)', '2 passed, 1 failed', '# pass 2\n# fail 1'):
            self.run.observe('repo_control',{'app_id':self.project,'action':'execute','command':'pytest'},result,False)
            self.assertFalse(self.run.evidence['projects'][self.project]['tests'])

    def test_research_reports_partial_access_and_uses_guarded_fetch(self):
        with patch.object(A,'fetch_url',side_effect=['Source: dribbble\n'+'design '*30,A.ToolFailure('offline'),'Source: collectui\n'+'design '*30]) as fetch:
            report=json.loads(A.design_research('booking calendar','Researching'))
        self.assertEqual(fetch.call_count,3)
        self.assertEqual(len(report['references']),2)
        self.assertEqual(report['unavailable'],['https://collectui.in/'])
        self.assertIn('not screenshot',report['scope'])

    def test_research_all_unavailable_and_invalid_input_fail_honestly(self):
        with patch.object(A,'fetch_url',return_value=A.ToolFailure('offline')):
            self.assertIsInstance(A.design_research('calendar','Researching'),A.ToolFailure)
        with patch.object(A,'fetch_url') as fetch:
            self.assertIsInstance(A.design_research([], 'Researching'),A.ToolFailure)
            fetch.assert_not_called()

    def test_new_tools_respect_modes_and_generate_provider_schemas(self):
        self.assertIn('design_research',A.turn_tool_names('plan'))
        self.assertNotIn('build_plan',A.turn_tool_names('plan'))
        self.assertNotIn('design_research',A.turn_tool_names('chat'))
        self.assertIn('build_plan',A.turn_tool_names('develop'))
        self.assertIn('design_research',A.UNTRUSTED_TOOLS)
        names={x['function']['name'] for x in A._openrouter_tools()}
        self.assertIn('build_plan',names);self.assertIn('design_research',names)


if __name__=='__main__':unittest.main(verbosity=2)
