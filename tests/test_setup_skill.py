import json
from pathlib import Path
import re
import sys
import unittest
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from paper2lark.setup import validate_request
from paper2lark.provisioning import Provisioner


class SetupSkillTests(unittest.TestCase):
    def test_documented_requests_pass_runtime_validation(self):
        content = (ROOT / 'skill_sources/setup.md').read_text(encoding='utf-8')
        examples = [json.loads(block) for block in re.findall(r'```json\n(.*?)\n```', content, re.S)]
        requests = [example for example in examples if 'mode' in example]
        self.assertEqual({request['mode'] for request in requests}, {'create', 'migrate'})
        for request in requests:
            self.assertEqual(validate_request(request), request)
        adoption = [example for example in examples if 'step' in example]
        self.assertEqual(len(adoption), 1)
        self.assertEqual(set(adoption[0]), {'step', 'reference'})
        runner = Mock()
        runner.call.return_value = {
            'node_token': 'VERIFIED_NODE_TOKEN', 'obj_token': 'VERIFIED_OBJECT_TOKEN',
            'space_id': 'VERIFIED_SPACE_ID', 'title': 'Paper Library',
            'obj_type': 'docx', 'parent_node_token': '', 'node_type': 'origin',
        }
        verified = Provisioner(runner).verify(
            'node', {'space_id': 'VERIFIED_SPACE_ID', 'title': 'Paper Library', 'obj_type': 'docx'},
            adoption[0]['reference'], ROOT,
        )
        self.assertEqual(verified, adoption[0]['reference'])


if __name__ == '__main__':
    unittest.main()
