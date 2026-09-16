import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from paper2lark.provisioning import Provisioner
from paper2lark.lark import LarkRunner
from paper2lark.errors import Paper2LarkError

class Runner:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []
    def call(self, args, cwd=None):
        LarkRunner._validated(args)
        self.calls.append(args)
        return self.responses.pop(0)
    def auth(self, verify=False):
        assert verify
        return {'code': 'OK', 'verified': True, 'account': {'identity': 'user', 'app_id': 'app', 'user_id': 'user', 'brand': 'lark'}}

class ProvisioningTests(unittest.TestCase):
    def test_account_verified(self):
        self.assertEqual(Provisioner(Runner()).account()['user_id'], 'user')
    def test_creates_capture_identity_without_reads(self):
        cases = [('space', {'name':'Library','description':'Research'}, {'space_id':'123'}, {'space_id':'123'}),
                 ('node', {'space_id':'123','title':'Index','obj_type':'bitable'}, {'node_token':'n','obj_token':'b','resolved_space_id':'123'}, {'node_token':'n','obj_token':'b','space_id':'123'}),
                 ('table', {'base_token':'b','name':'Index','fields':[{'name':'Title','type':'text'}]}, {'table':{'id':'tbl1'}}, {'table_id':'tbl1'}),
                 ('field', {'base_token':'b','table_id':'tbl1','definition':{'name':'Title','type':'text'}}, {'field':{'id':'fld1'}}, {'field_id':'fld1'})]
        for kind, payload, response, expected in cases:
            with self.subTest(kind=kind):
                runner = Runner(response)
                self.assertEqual(Provisioner(runner).perform(kind,payload,None),expected)
                self.assertEqual(len(runner.calls),1)
    def test_partial_create_is_uncertain(self):
        with self.assertRaises(Paper2LarkError) as caught:
            Provisioner(Runner({'node_token':'n'})).perform('node',{'space_id':'123','title':'Root','obj_type':'docx'},None)
        self.assertEqual(caught.exception.code,'REMOTE_RESULT_UNCERTAIN')
    def test_space_verification_exhausts_pages(self):
        runner = Runner({'spaces':[{'space_id':'123','name':'Library','description':'Research'}],'has_more':True,'page_token':'next'}, {'spaces':[],'has_more':False,'page_token':''})
        self.assertEqual(Provisioner(runner).verify('space',{'name':'Library','description':'Research'},{'space_id':'123'},None),{'space_id':'123'})
        self.assertEqual(len(runner.calls),2)
    def test_node_wrong_parent_rejected(self):
        runner=Runner({'space_id':'123','node_token':'n','obj_token':'d','obj_type':'docx','parent_node_token':'other','title':'Root'})
        with self.assertRaises(Paper2LarkError):
            Provisioner(runner).verify('node',{'space_id':'123','title':'Root','obj_type':'docx'},{'node_token':'n','obj_token':'d','space_id':'123'},None)
    def test_table_schema_allows_service_extras(self):
        field={'name':'Status','type':'select','multiple':False,'options':[{'name':'Unread'}]}
        runner=Runner({'table':{'id':'tbl1','name':'Index'}},{'fields':[dict(field,id='fld1'),{'id':'fld2','name':'Service','type':'text'}],'total':2})
        self.assertEqual(Provisioner(runner).verify('table',{'base_token':'b','name':'Index','fields':[field]},{'table_id':'tbl1'},None),{'table_id':'tbl1'})
    def test_field_missing_requested_option_rejected(self):
        runner=Runner({'field':{'id':'fld1','name':'Status','type':'select','multiple':False,'options':[]}})
        with self.assertRaises(Paper2LarkError):
            Provisioner(runner).verify('field',{'base_token':'b','table_id':'tbl1','definition':{'name':'Status','type':'select','multiple':False,'options':[{'name':'Unread'}]}},{'field_id':'fld1'},None)
    def test_template_artifact_and_digest(self):
        payload={'space_id':'123','parent_node_token':'parent','title':'Template','content':'# Template\n'}
        runner=Runner({'document':{'document_id':'doc','revision_id':1,'url':'https://example.test/docx/doc'}}, {'document':{'document_id':'doc','revision_id':1,'content':payload['content']}}, {'space_id':'123','node_token':'node','obj_token':'doc','obj_type':'docx','parent_node_token':'parent','title':'Template'})
        with tempfile.TemporaryDirectory() as directory:
            provider=Provisioner(runner)
            created=provider.perform('template',payload,directory)
            self.assertEqual(created['document_id'],'doc')
            self.assertEqual((Path(directory)/'publication.md').read_bytes(),payload['content'].encode())
            self.assertEqual(provider.verify('template',payload,created,directory)['node_token'],'node')
    def test_space_repeated_cursor_is_rejected(self):
        runner = Runner({'spaces': [], 'has_more': True, 'page_token': 'x'}, {'spaces': [], 'has_more': True, 'page_token': 'x'})
        with self.assertRaises(Paper2LarkError) as caught:
            Provisioner(runner).verify('space', {'name': 'Library', 'description': ''}, {'space_id': '123'}, None)
        self.assertEqual(caught.exception.code, 'CLI_PARTIAL_RESULT')
    def test_template_wrong_digest_rejected(self):
        runner = Runner({'document': {'document_id': 'doc', 'revision_id': 1, 'content': 'changed'}})
        with self.assertRaises(Paper2LarkError):
            Provisioner(runner).verify('template', {'space_id':'123', 'parent_node_token':'parent', 'title':'Template', 'content':'original'}, {'document_id':'doc'}, None)
    def test_field_options_match_semantically_with_service_ids(self):
        definition = {'name':'Status', 'type':'select', 'multiple':False, 'options':[{'name':'Unread','hue':'Blue'}, {'name':'Read'}]}
        observed = dict(definition, id='fld1', options=[{'id':'opt2','name':'Read'}, {'id':'opt1','name':'Unread','hue':'Blue'}])
        provider = Provisioner(Runner({'field': observed}))
        self.assertEqual(provider.verify('field', {'base_token':'b','table_id':'tbl1','definition':definition}, {'field_id':'fld1'}, None), {'field_id':'fld1'})
    def test_all_provisioning_writes_require_destinations(self):
        for args in [['wiki','+node-create','--title','X','--obj-type','docx'], ['base','+field-create','--base-token','b','--json','{}'], ['base','+table-create','--name','T','--fields','[]']]:
            with self.subTest(args=args), self.assertRaises(Paper2LarkError):
                LarkRunner._validated(args)
    def test_node_shortcut_readback_rejected(self):
        runner = Runner({'space_id':'123','node_token':'n','obj_token':'d','obj_type':'docx','parent_node_token':'','title':'Root','node_type':'shortcut'})
        with self.assertRaises(Paper2LarkError):
            Provisioner(runner).verify('node', {'space_id':'123','title':'Root','obj_type':'docx'}, {'space_id':'123','node_token':'n','obj_token':'d'}, None)
    def test_allowlist_requires_structured_schema(self):
        with self.assertRaises(Paper2LarkError):
            LarkRunner._validated(['base','+table-create','--base-token','b','--name','Index','--fields','not json'])

if __name__=='__main__': unittest.main()
