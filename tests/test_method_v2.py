"""Real bundled zsspec script + framework integration (no model/provider calls)."""
from __future__ import annotations

import shutil
import sys
import copy
import threading
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from test_gate import GateFixture
from aidevflow.gate import GateError, read_json
from aidevflow.method_execution import execute, recover
from aidevflow.method_resolution import discover, source_for
from aidevflow.method_adapters.zsspec import ZsspecAdapter


class MethodV2Tests(GateFixture):
    def setUp(self):
        super().setUp()
        self.gate.active_path.unlink()
        self._write_json('.aidevflow/project.json',{'runtime':'omp','skills_root':'.claude/skills','python':sys.executable})
        source = Path(__file__).resolve().parents[1] / '.claude/skills'
        target = self.root / '.claude/skills'
        adapter = ZsspecAdapter()
        for name in {n for v in adapter.stages.values() for n in v} | set(adapter.support):
            shutil.copytree(source / name, target / name, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        shutil.copy2(source / 'README.md', target / 'README.md')
        self.contract.update(task_id='CURRENT', work_plan_required=True,
            method={'name':'zsspec', 'profile':adapter.profile, 'stages':adapter.stages,
                    'modules':['feature'], 'acceptance_map':{'REQ-1':['AC-feature-value-one']}},
            evidence_paths=['specs/checklist/reports'],
            design_artifacts=['specs/requirement/feature/REQ-feature.md',
                              'specs/design/feature/DES-feature-api.md',
                              'specs/task/feature/TASK-feature.md',
                              'specs/checklist/feature/CHK-feature.md'],
            test_commands={'red':[sys.executable,'-B','{target}'], 'green':[sys.executable,'-B','{target}']},
            required_checks=[{'id':'C-green','suite':'green','target':'tests/test_feature.py','acceptance_items':['REQ-1']},
                             {'id':'C-static','kind':'static','acceptance_items':['REQ-1']}])
        (self.root/'src/sample.py').write_text('def value():\n    return 0\n',encoding='utf-8')
        self._git('add','src/sample.py'); self._git('commit','-qm','Missing required behavior')
        (self.root/'tests/test_feature.py').write_text(
            'import sys,unittest\nfrom pathlib import Path\nsys.path.insert(0,str(Path(__file__).resolve().parents[1]))\n'
            'from src.sample import value\nclass Feature(unittest.TestCase):\n'
            '    def test_returns_one(self): self.assertEqual(value(),1)\n'
            'if __name__ == "__main__": unittest.main()\n',encoding='utf-8')
        bodies = [
            '## 功能点总览\n| 功能点 | 状态 |\n|---|---|\n| REQ-feature-value | 未开始 |\n'
            '## REQ-feature-value\n### 验收标准（AC）\n| AC 编号 | 验收标准 |\n|---|---|\n| AC-feature-value-one | Return one |\n',
            '## 设计要点表\n| REQ 功能点 | 承接设计实体 | AC 快照 |\n|---|---|---|\n'
            '| REQ-feature-value | API-feature-value | AC-feature-value-one（Return one） |\n## API-feature-value\nReturn one.\n',
            '## 任务总表\n| 编号 | 关联设计实体 | AC 快照 | 状态 |\n|---|---|---|---|\n'
            '| TASK-feature-value-implement | API-feature-value | AC-feature-value-one（Return one） | 未开始 |\n',
            '| 编号 | 关联 AC | 状态 |\n|---|---|---|\n'
            '| CHK-feature-FUNC-value | AC-feature-value-one | 待执行 |\n'
            '| CHK-feature-SEC-review | AC-feature-value-one | 待执行 |\n']
        for rel, body in zip(self.contract['design_artifacts'], bodies):
            p = self.root / rel; p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text('# Fixture\n**文档状态**: 草稿\n'+body, encoding='utf-8')
        self.report='specs/checklist/reports/design.md'
        p=self.root / self.report; p.parent.mkdir(parents=True,exist_ok=True)
        p.write_text('Integration fixture AI review evidence; no model execution claimed.',encoding='utf-8')
        self._write_json('current-contract.json', self.contract)
        self.gate.init('CURRENT','current-contract.json')

    def design(self):
        self._reach_design()
        plan={'schema':'aidevflow/work-plan-v1','task_id':'CURRENT',
              'items':[{'id':'W1','title':'Return one','done_criteria':['Returns one'],'acceptance_items':['REQ-1']}],
              'projections':[
                  {'path':self.contract['design_artifacts'][0],'row_id':'REQ-feature-value','source_kind':'acceptance','source_id':'REQ-1'},
                  {'path':self.contract['design_artifacts'][2],'row_id':'TASK-feature-value-implement','source_kind':'work_item','source_id':'W1'},
                  {'path':self.contract['design_artifacts'][3],'row_id':'CHK-feature-FUNC-value','source_kind':'check','source_id':'C-green'},
                  {'path':self.contract['design_artifacts'][3],'row_id':'CHK-feature-SEC-review','source_kind':'check','source_id':'C-static'}]}
        self._write_json('specs/aidevflow-intake/CURRENT/work-plan.json',plan)
        self.gate.register_work_plan('specs/aidevflow-intake/CURRENT/work-plan.json')
        for layer in ('req','des','task','chk'):
            execute(self.root,'baseline','feature',layer,[self.report])
        execute(self.root,'gate','feature')
        self.gate.record_design_review('fixture AI','Inspected specs and executable assertions',[self.report])
        self.gate.freeze_tests([])
        self.gate.run_tests('red','tests/test_feature.py')

    def build(self):
        self.design()
        self.gate.advance('build')
        (self.root/'src/sample.py').write_text('def value():\n    return 1\n',encoding='utf-8')
        self.gate.start_work_item('W1')
        self.gate.sync_projections()

    def test_real_native_baseline_gate_and_status_sync(self):
        self.build()
        self.assertIn('进行中',(self.root / self.contract['design_artifacts'][2]).read_text(encoding='utf-8'))
        self.gate.complete_work_item('W1',['src/sample.py'])
        self.gate.run_tests('green','tests/test_feature.py')
        self.gate.record_check('C-static','passed',self.gate.status()['candidate_sha256'],'fixture AI','Inspected source',[self.report])
        self.gate.sync_projections()
        self.assertIn('已完成',(self.root/self.contract['design_artifacts'][2]).read_text(encoding='utf-8'))
        self.assertIn('通过',(self.root/self.contract['design_artifacts'][3]).read_text(encoding='utf-8'))
        self.gate.integrity()
        self.gate.advance('verify')
        self.assertEqual(self.gate.status()['current_stage'],'verify')
        self.gate.sync_projections()
        bundle=self.gate.prepare_verifier()
        self.assertFalse(self.gate.status()['method_sync_pending'])
        from aidevflow.gate import VERDICT_SCHEMA
        verdict={'schema':VERDICT_SCHEMA,'task_id':'CURRENT','spec_version':bundle['spec_version'],
                 'bundle_sha256':bundle['sha256'], 'items':[{'id':'REQ-1','verdict':'pass','evidence':['diff.patch'],
                 'reason':'Protocol fixture verdict; not an actual model review.'}],'risks':[]}
        self._write_json('verdict.json',verdict)
        self.gate.record_verdict('verdict.json')
        self.gate.advance('delivery_ready')
        self.assertIn('未开始',(self.root/self.contract['design_artifacts'][0]).read_text(encoding='utf-8'))
        result=execute(self.root,'close','feature',by='fixture operator',utterance='Accept fixture')
        self.assertEqual(result['status'],'applied')
        self.assertIn('已完成',(self.root/self.contract['design_artifacts'][0]).read_text(encoding='utf-8'))
        self.assertTrue((self.root/result['generated']['native_index']['path']).is_file())
        self.gate.integrity()
        self.assertTrue(self.gate.status()['verdict_current'])

    def test_snapshot_survives_host_updates_but_not_snapshot_corruption(self):
        self._git('add','.claude/skills/zsspec-plan/SKILL.md','.claude/skills/README.md')
        self._git('commit','-qm','Version installed method assets')
        before=self.gate.status()['candidate_sha256']
        p=self.root/'.claude/skills/zsspec-plan/SKILL.md'
        p.write_text('host updated',encoding='utf-8')
        (self.root/'.claude/skills/README.md').write_text('host README updated',encoding='utf-8')
        from aidevflow.method_resources import load_resource
        loaded=load_resource(self.root,{'path':'.claude/skills/README.md'})
        self.assertEqual(Path(loaded['input']['path']),source_for(self.root)/'README.md')
        self.assertEqual(self.gate.status()['candidate_sha256'],before)
        self.gate.integrity()
        (source_for(self.root)/'zsspec-plan/SKILL.md').write_text('corrupt',encoding='utf-8')
        with self.assertRaises(GateError): self.gate.integrity()

    def test_cache_is_preparation_only_and_content_addressed(self):
        first=discover(self.root); second=discover(self.root)
        self.assertTrue(second['cache_hit'])
        self.assertFalse(first['auto_activation'])
        self.assertEqual(first['package'],second['package'])

    def test_claude_native_loader_keeps_conservative_host_drift_protection(self):
        self.gate.active_path.unlink()
        self._write_json('.aidevflow/project.json',{'runtime':'claude-code','skills_root':'.claude/skills','python':sys.executable})
        contract=copy.deepcopy(self.contract); contract['task_id']='CONSERVATIVE'
        self._write_json('conservative.json',contract)
        self.gate.init('CONSERVATIVE','conservative.json')
        (self.root/'.claude/skills/zsspec-plan/SKILL.md').write_text('changed native loader source',encoding='utf-8')
        with self.assertRaisesRegex(GateError,'integrity mismatch'): self.gate.integrity()

    def test_baseline_requires_review_and_explicit_layer(self):
        self._reach_design()
        with self.assertRaisesRegex(GateError,'evidence'): execute(self.root,'baseline','feature','req')
        with self.assertRaisesRegex(GateError,'explicit layer'): execute(self.root,'baseline','feature','all',[self.report])
        with self.assertRaisesRegex(GateError,'pinned method.modules'): execute(self.root,'gate','other')

    def test_interrupted_commit_is_blocked_and_can_finish(self):
        self._reach_design()
        from aidevflow.method_execution import _replace
        def partial(path, data):
            _replace(path,data)
            raise OSError('simulated interruption after actual file replacement')
        with patch('aidevflow.method_execution._replace',side_effect=partial):
            with self.assertRaises(OSError): execute(self.root,'baseline','feature','req',[self.report])
        with self.assertRaisesRegex(GateError,'Method operation pending'): self.gate.status()
        with self.assertRaisesRegex(GateError,'method transaction'): self.gate.rebuild('CURRENT')
        result=recover(self.root,'finish')
        self.assertEqual(result['status'],'applied')
        self.assertFalse(recover(self.root,'finish')['recovered'])
        self.gate.integrity()

    def test_interrupted_commit_can_rollback_and_retry(self):
        self._reach_design()
        with patch('aidevflow.method_execution._replace',side_effect=OSError('interrupt')):
            with self.assertRaises(OSError): execute(self.root,'baseline','feature','req',[self.report])
        self.assertEqual(recover(self.root,'rollback')['status'],'rolled_back')
        self.assertEqual(execute(self.root,'baseline','feature','req',[self.report])['status'],'applied')

    def test_gate_rejects_legacy_docs_and_wrong_ac_mapping(self):
        self.design()
        p=self.root/self.contract['design_artifacts'][0]
        p.write_text(p.read_text(encoding='utf-8').replace('**文档状态**: 已定稿\n',''),encoding='utf-8')
        with self.assertRaises(GateError): execute(self.root,'gate','feature')

    def test_close_never_runs_before_human_acceptance_checkpoint(self):
        with self.assertRaisesRegex(GateError,'business acceptance'):
            execute(self.root,'close','feature',by='user',utterance='accept')

    def test_recovery_cannot_steal_a_live_worker_and_cli_releases_writer_lock(self):
        self._reach_design()
        from aidevflow.method_execution import _run
        from aidevflow.locking import writer_lock
        from aidevflow.cli import main
        acquired=[]
        def native(*args):
            def another_writer():
                with writer_lock(self.root/'.aidevflow', timeout=0): acquired.append(True)
            thread=threading.Thread(target=another_writer)
            thread.start(); thread.join(timeout=2)
            with self.assertRaisesRegex(TimeoutError,'worker is active'): recover(self.root,'rollback')
            return _run(*args)
        with patch('aidevflow.method_execution._run',side_effect=native), redirect_stdout(StringIO()):
            self.assertEqual(main(['--root',str(self.root),'method-run','status','--module','feature']),0)
        self.assertEqual(acquired,[True])

    def test_stale_specs_are_not_overwritten_by_native_result(self):
        self._reach_design()
        from aidevflow.method_execution import _run
        p=self.root/self.contract['design_artifacts'][0]
        def native(*args):
            result=_run(*args)
            p.write_text(p.read_text(encoding='utf-8')+'\nActual concurrent edit\n',encoding='utf-8')
            return result
        with patch('aidevflow.method_execution._run',side_effect=native):
            with self.assertRaisesRegex(GateError,'Stale method operation'):
                execute(self.root,'baseline','feature','req',[self.report])
        self.assertIn('Actual concurrent edit',p.read_text(encoding='utf-8'))
        self.assertIn('草稿',p.read_text(encoding='utf-8'))
        self.gate.status()

    def test_reopened_native_completed_task_does_not_finish_gate_work(self):
        self.build()
        self.gate.complete_work_item('W1',['src/sample.py']); self.gate.sync_projections()
        self.gate.reopen_work_item('W1','Repair defect')
        self.gate.sync_projections()
        status=self.gate.status()
        self.assertEqual(status['work_items'][0]['status'],'in_progress')
        self.assertTrue(status['method_deferred_transitions'])
        with self.assertRaisesRegex(GateError,'Complete all registered work'): self.gate.advance('verify')

    def test_canonical_acceptance_mapping_is_exact_not_a_row_count(self):
        self.design()
        from aidevflow.method_execution import _check_native_mappings
        changed=copy.deepcopy(self.contract)
        changed['method']['acceptance_map']['REQ-1']=['AC-feature-value-unapproved']
        with self.assertRaisesRegex(GateError,'exactly cover'):
            _check_native_mappings(source_for(self.root)/ZsspecAdapter.script,self.root,self.gate,
                                   self.gate._load_active()[0],changed)

    def test_revision_cannot_freeze_draft_native_specs_or_lose_profile(self):
        self.build()
        base='specs/aidevflow-intake/CURRENT/baseline-drafts/'
        p=self.root/base; p.mkdir(parents=True,exist_ok=True)
        (p/'test.py').write_text((self.root/'tests/test_feature.py').read_text(encoding='utf-8')+'\n# corrected baseline\n',encoding='utf-8')
        (p/'review.md').write_text('Reviewed bounded test correction',encoding='utf-8')
        plan=copy.deepcopy(self.gate._plan(self.gate._load_active()[0])); plan.pop('method_profile')
        self._write_json(base+'plan.json',plan)
        draft={'schema':'aidevflow/baseline-revision-v1','reason':'Correct test fixture',
               'changes':[{'path':'tests/test_feature.py','draft':base+'test.py'}],'work_plan_draft':base+'plan.json'}
        self._write_json(base+'revision.json',draft)
        result=self.gate.plan_baseline_revision(base+'revision.json')
        self.assertEqual(result['proposal']['work_plan']['method_profile'],ZsspecAdapter.profile)
        (p/'draft-req.md').write_text((self.root/self.contract['design_artifacts'][0]).read_text(encoding='utf-8').replace('已定稿','草稿'),encoding='utf-8')
        draft['changes'].append({'path':self.contract['design_artifacts'][0],'draft':base+'draft-req.md'})
        self._write_json(base+'revision.json',draft)
        with self.assertRaisesRegex(GateError,'already be finalized'):
            self.gate.plan_baseline_revision(base+'revision.json')
