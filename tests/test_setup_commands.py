import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from paper2lark.__main__ import parser

# Run the real package launcher, retaining its integrity check. Only its child
# command is replaced with a Python bootstrap that injects a synthetic provider.
WRAPPER = r'''
import runpy, subprocess, sys
launcher, provider, *arguments = sys.argv[1:]
original = subprocess.run
bootstrap = "import sys; sys.path.insert(0,sys.argv.pop(1)); provider=sys.argv.pop(1); import paper2lark.__main__ as cli; from paper2lark.lark import LarkRunner; cli.LarkRunner=lambda ignored=None: LarkRunner(sys.executable,prefix_args=[provider]); raise SystemExit(cli.main())"
def child(args, **kwargs):
    return original([sys.executable,'-c',bootstrap,args[2],provider,*args[3:]],**kwargs)
subprocess.run=child
sys.argv=[launcher,*arguments]
runpy.run_path(launcher,run_name='__main__')
'''

class SetupCommandTests(unittest.TestCase):
    def test_setup_parser_contract(self):
        for values in [('plan','--input','request.json'),('apply','--plan','plan.json'),('show','--id','id'),('cancel','--id','id')]:
            self.assertEqual(parser().parse_args(['setup',*values]).action,values[0])
    @classmethod
    def setUpClass(cls):
        subprocess.run([sys.executable,str(ROOT/'scripts/build_plugins.py')],check=True,capture_output=True)
    def invoke(self,base,host,language,*arguments,ok=True):
        result=subprocess.run([sys.executable,'-c',WRAPPER,str(ROOT/'dist'/host/'plugins/paper2lark/scripts/paper2lark.py'),str(ROOT/'tests/setup_provider.py'),'--home',str(base/'home'),'--profile','default','--library-language',language,*arguments],cwd=base,env={**os.environ,'P2L_SETUP_STATE':str(base/'remote.json')},capture_output=True,text=True,encoding='utf-8')
        response=json.loads(result.stdout)
        self.assertEqual(result.returncode,0 if ok else 2,(response,result.stderr))
        return response.get('data',response.get('error'))
    def test_lost_create_adoption_and_local_cancel_both_hosts(self):
        for host in ('claude','codex'):
            with self.subTest(host=host), tempfile.TemporaryDirectory() as folder:
                base=Path(folder)
                (base/'remote.json').write_text(json.dumps({'lose':'+space-create'}),encoding='utf-8')
                request=base/'request.json'
                request.write_text(json.dumps({'schema_version':1,'mode':'create','name':'Recovery','site_url':'https://example.test'}),encoding='utf-8')
                planned=self.invoke(base,host,'en','setup','plan','--input',str(request))
                failure=self.invoke(base,host,'en','setup','apply','--plan',planned['plan_path'],ok=False)
                self.assertEqual(failure['code'],'REMOTE_RESULT_UNCERTAIN')
                failure=self.invoke(base,host,'en','setup','apply','--plan',planned['plan_path'],ok=False)
                self.assertEqual(failure['code'],'SETUP_RESULT_UNCERTAIN')
                shown=self.invoke(base,host,'en','setup','show','--id',planned['setup_id'])
                self.assertEqual(shown['pending_step'],'space')
                adoption=base/'adopt.json'
                adoption.write_text(json.dumps({'step':'space','reference':{'space_id':'123'}}),encoding='utf-8')
                applied=self.invoke(base,host,'en','setup','apply','--plan',planned['plan_path'],'--adopt',str(adoption))
                self.assertEqual(applied['status'],'completed')
                calls=json.loads((base/'remote.json').read_text(encoding='utf-8'))['calls']
                self.assertEqual(sum(c[1]=='+space-create' for c in calls),1)
                request.write_text(json.dumps({'schema_version':1,'mode':'migrate'}),encoding='utf-8')
                pending=self.invoke(base,host,'en','setup','plan','--input',str(request))
                before=json.loads((base/'remote.json').read_text(encoding='utf-8'))['calls']
                canceled=self.invoke(base,host,'en','setup','cancel','--id',pending['setup_id'])
                self.assertEqual(canceled['status'],'canceled')
                self.assertEqual(before,json.loads((base/'remote.json').read_text(encoding='utf-8'))['calls'])
    def test_migration_preserves_renamed_fields_and_template_both_hosts(self):
        for host in ('claude','codex'):
            with self.subTest(host=host), tempfile.TemporaryDirectory() as folder:
                base=Path(folder)
                remote_path=base/'remote.json'
                remote_path.write_text('{}',encoding='utf-8')
                request=base/'request.json'
                request.write_text(json.dumps({'schema_version':1,'mode':'create','name':'Migrate','site_url':'https://example.test'}),encoding='utf-8')
                planned=self.invoke(base,host,'en','setup','plan','--input',str(request))
                self.invoke(base,host,'en','setup','apply','--plan',planned['plan_path'])
                binding_path=base/'home/profiles/default/bindings.json'
                binding=json.loads(binding_path.read_text(encoding='utf-8'))
                removed=binding['fields'].pop('summary')['id']
                remote=json.loads(remote_path.read_text(encoding='utf-8'))
                remote['fields']=[f for f in remote['fields'] if f['id'] != removed]
                title_id=binding['fields']['title']['id']
                next(f for f in remote['fields'] if f['id']==title_id)['name']='My custom title'
                remote['documents'][binding['template']['document_id']]['content'] += '\nHuman custom content\n'
                preserved=json.dumps(remote['documents'],sort_keys=True)
                remote_path.write_text(json.dumps(remote),encoding='utf-8')
                binding_path.write_text(json.dumps(binding),encoding='utf-8')
                request.write_text(json.dumps({'schema_version':1,'mode':'migrate'}),encoding='utf-8')
                planned=self.invoke(base,host,'en','setup','plan','--input',str(request))
                self.assertEqual(planned['additions'],['summary'])
                applied=self.invoke(base,host,'en','setup','apply','--plan',planned['plan_path'])
                self.assertEqual(applied['binding']['fields']['title']['name'],'My custom title')
                self.assertEqual(len(applied['binding']['fields']),12)
                after=json.loads(remote_path.read_text(encoding='utf-8'))
                self.assertEqual(json.dumps(after['documents'],sort_keys=True),preserved)
                self.assertEqual(sum(c[1]=='+field-create' for c in after['calls']),1)
    def test_malformed_input_rejected_before_provider(self):
        with tempfile.TemporaryDirectory() as folder:
            base=Path(folder)
            (base/'remote.json').write_text('{}')
            request=base/'bad.json'
            request.write_text('{"schema_version":1,"schema_version":1}')
            failure=self.invoke(base,'codex','en','setup','plan','--input',str(request),ok=False)
            self.assertEqual(failure['code'],'INPUT_INVALID')
            self.assertEqual(json.loads((base/'remote.json').read_text(encoding='utf-8')),{})
    def test_both_hosts_create_and_resume_both_languages(self):
        for host in ('claude','codex'):
            for language in ('en','zh-CN'):
                with self.subTest(host=host,language=language), tempfile.TemporaryDirectory(prefix='setup 璁烘枃 ') as folder:
                    base=Path(folder)
                    (base/'remote.json').write_text('{}',encoding='utf-8')
                    request=base/'request.json'
                    request.write_text(json.dumps({'schema_version':1,'mode':'create','name':'Research','site_url':'https://example.test'}),encoding='utf-8')
                    planned=self.invoke(base,host,language,'setup','plan','--input',str(request))
                    self.assertFalse(planned['remote_mutations'])
                    before=json.loads((base/'remote.json').read_text(encoding='utf-8'))
                    self.assertTrue(all(call[:2]==['auth','status'] for call in before['calls']))
                    applied=self.invoke(base,host,language,'setup','apply','--plan',planned['plan_path'])
                    self.assertEqual(applied['status'],'completed')
                    self.assertEqual(len(applied['binding']['fields']),12)
                    binding=(base/'home/profiles/default/bindings.json').read_bytes()
                    remote=json.loads((base/'remote.json').read_text(encoding='utf-8'))
                    writes=[call for call in remote['calls'] if call[1] in ('+space-create','+node-create','+table-create','+create')]
                    self.assertEqual(len(writes),6)
                    resumed=self.invoke(base,host,language,'setup','apply','--plan',planned['plan_path'])
                    self.assertFalse(resumed['remote_mutations'])
                    self.assertEqual(binding,(base/'home/profiles/default/bindings.json').read_bytes())
                    calls=json.loads((base/'remote.json').read_text(encoding='utf-8'))['calls']
                    self.assertEqual(len([c for c in calls if c[1] in ('+space-create','+node-create','+table-create','+create')]),6)
                    shown=self.invoke(base,host,language,'setup','show','--id',planned['setup_id'])
                    self.assertEqual(shown['status'],'completed')
                    self.assertEqual(len(calls),len(json.loads((base/'remote.json').read_text(encoding='utf-8'))['calls']))

if __name__=='__main__': unittest.main()
