import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from paper2lark.errors import Paper2LarkError
from paper2lark.lark import LarkRunner, parse_auth, classify_error


class LarkTests(unittest.TestCase):
    def assert_private_error(self, error, *secrets):
        chain, current = [], error
        while current is not None and id(current) not in {id(item) for item in chain}:
            chain.append(current)
            current = current.__cause__ or current.__context__
        material = ' '.join(f'{type(item).__name__}: {item!s} {item!r}' for item in chain)
        for secret in secrets:
            self.assertNotIn(secret, material)
        self.assertIsNone(error.__cause__)
        self.assertIsNone(error.__context__)

    def test_token_missing_is_ambiguous_not_instruction_to_login(self):
        result = parse_auth({'appId': 'app-test', 'identities': {'user': {'status': 'token_missing', 'available': False}}})
        self.assertEqual(result['code'], 'CREDENTIALS_UNAVAILABLE')
        self.assertNotIn('login', result['message'].lower())

    def test_explicit_logged_out_and_keychain_error_are_distinct(self):
        self.assertEqual(parse_auth({'identities': {'user': {'status': 'logged_out'}}})['code'], 'LOGIN_REQUIRED')
        self.assertEqual(classify_error({'type': 'environment', 'message': 'keychain access denied'}).code, 'CREDENTIAL_STORE_INACCESSIBLE')
        self.assertEqual(classify_error({'subtype': 'missing_scope'}).code, 'MISSING_SCOPE')
        self.assertEqual(classify_error({'type': 'network'}).code, 'NETWORK_ERROR')

    def test_ready_status_requires_complete_account_fingerprint(self):
        data = {'appId': 'app-test', 'brand': 'lark', 'identities': {'user': {'status': 'ready', 'available': True, 'openId': 'user-test', 'tokenStatus': 'valid'}}}
        result = parse_auth(data)
        self.assertEqual(result['code'], 'OK')
        self.assertEqual(result['account']['identity'], 'user')
        del data['identities']['user']['openId']
        self.assertEqual(parse_auth(data)['code'], 'ACCOUNT_UNVERIFIED')

    def test_positive_server_verification_can_confirm_a_refreshed_token(self):
        data = {'appId': 'app-test', 'brand': 'lark', 'verified': True,
                'identities': {'user': {'status': 'ready', 'available': True,
                                       'openId': 'user-test', 'tokenStatus': 'refreshed', 'verified': True}}}
        self.assertEqual(parse_auth(data)['code'], 'OK')
        data['verified'] = False
        data['identities']['user']['verified'] = False
        self.assertNotEqual(parse_auth(data)['code'], 'OK')

    def test_verified_auth_call_requires_server_verification_flags(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            log = root / 'calls.ndjson'
            script = root / 'unverified-auth.py'
            script.write_text(
                'import json,pathlib,sys\n'
                'args=sys.argv[2:]\n'
                'with pathlib.Path(sys.argv[1]).open("a",encoding="utf-8") as stream: '
                'stream.write(json.dumps(args)+"\\n")\n'
                'print(json.dumps({"appId":"app-test","brand":"lark","identities":{"user":'
                '{"status":"ready","available":True,"openId":"user-test","tokenStatus":"valid"}}}))\n',
                encoding='utf-8')
            runner = LarkRunner(executable=sys.executable, prefix_args=[str(script), str(log)])
            self.assertEqual(runner.auth()['code'], 'OK')
            verified = runner.auth(verify=True)
            self.assertEqual(verified['code'], 'AUTH_VERIFY_FAILED')
            self.assertNotIn('user-test', verified['message'])
            calls = [json.loads(line) for line in log.read_text(encoding='utf-8').splitlines()]
            self.assertNotIn('--verify', calls[0])
            self.assertIn('--verify', calls[1])

    def test_explicit_user_verification_failure_is_not_overridden(self):
        data = {'appId': 'app-test', 'brand': 'lark', 'verified': True,
                'identities': {'user': {'status': 'ready', 'available': True,
                                       'openId': 'user-test', 'tokenStatus': 'valid', 'verified': False}}}
        self.assertEqual(parse_auth(data)['code'], 'AUTH_VERIFY_FAILED')

    def test_runner_does_not_execute_mutations_or_shell_metacharacters(self):
        runner = LarkRunner(executable=sys.executable)
        with self.assertRaises(Paper2LarkError) as raised:
            runner.call(['auth', 'login'])
        self.assertEqual(raised.exception.code, 'OPERATION_NOT_ALLOWED')

    def test_real_subprocess_utf8_stderr_failure_and_redaction(self):
        # A fake provider is injected at the executable boundary, not around parsing.
        with tempfile.TemporaryDirectory(prefix='论文 cli ') as folder:
            script = Path(folder) / 'provider.py'
            script.write_text('import json,sys\nprint(json.dumps({"ok":False,"error":{"subtype":"missing_scope","message":"SECRET fixture"}}),file=sys.stderr)\nsys.exit(1)\n', encoding='utf-8')
            runner = LarkRunner(executable=sys.executable, prefix_args=[str(script)])
            with self.assertRaises(Paper2LarkError) as raised:
                runner.call(['wiki', '+node-get', '--node-token', '论文;$(echo nope)'])
            self.assertEqual(raised.exception.code, 'MISSING_SCOPE')
            self.assertNotIn('SECRET', str(raised.exception))

    def test_timeout_is_bounded_and_reported_without_retry(self):
        with tempfile.TemporaryDirectory() as folder:
            script = Path(folder) / 'slow.py'
            script.write_text('import time\ntime.sleep(5)\n', encoding='utf-8')
            runner = LarkRunner(executable=sys.executable, prefix_args=[str(script)], timeout=0.05)
            with self.assertRaises(Paper2LarkError) as raised:
                runner.call(['wiki', '+node-get', '--node-token', 'secret-read-token'])
            self.assertEqual(raised.exception.code, 'CLI_TIMEOUT')
            self.assert_private_error(raised.exception, 'secret-read-token')

    def test_write_timeout_is_uncertain_and_not_retried(self):
        with tempfile.TemporaryDirectory() as folder:
            script = Path(folder) / 'slow-write.py'
            counter = Path(folder) / 'count.txt'
            script.write_text(
                'import pathlib,sys,time\n'
                'p=pathlib.Path(sys.argv[1]); p.write_text(str(int(p.read_text() or "0")+1))\n'
                'time.sleep(5)\n', encoding='utf-8')
            counter.write_text('0', encoding='utf-8')
            runner = LarkRunner(executable=sys.executable, prefix_args=[str(script), str(counter)], timeout=0.5)
            with self.assertRaises(Paper2LarkError) as raised:
                runner.call(['base', '+record-batch-create', '--base-token', 'base', '--table-id', 'tbl',
                             '--json', '{"create_records":[{"fldTitle":"论文;$(nope)"}]}'])
            self.assertEqual(raised.exception.code, 'REMOTE_RESULT_UNCERTAIN')
            self.assertEqual(counter.read_text(encoding='utf-8'), '1')
            self.assert_private_error(raised.exception, '论文;$(nope)', 'base', 'tbl')

    def test_read_and_write_argument_contracts_are_separate(self):
        runner = LarkRunner(executable=sys.executable)
        invalid = [
            ['base', '+record-list', '--table-id', 't'],
            ['base', '+record-list', '--base-token', '   ', '--table-id', 't'],
            ['base', '+record-list', '--base-token', 'b', '--table-id', '\t'],
            ['base', '+record-list', '--base-token', 'b', '--table-id', 't', '--output', 'escape.ndjson'],
            ['base', '+record-list', '--base-token', 'b', '--base-token', 'b2', '--table-id', 't'],
            ['base', '+field-get', '--base-token', 'b', '--table-id', 't', '--field-id', 'a', '--field-id', 'b'],
            ['base', '+record-batch-create', '--base-token', 'b', '--table-id', 't', '--json', 'x\x00y'],
            ['base', '+record-delete', '--base-token', 'b', '--table-id', 't', '--record-id', 'rec_a'],
        ]
        for args in invalid:
            with self.subTest(args=args), self.assertRaises(Paper2LarkError) as raised:
                runner.call(args)
            self.assertEqual(raised.exception.code, 'OPERATION_NOT_ALLOWED')

    def test_export_reads_raw_ndjson_manifest_and_keeps_arguments_atomic(self):
        with tempfile.TemporaryDirectory(prefix='论文 export ') as folder:
            root = Path(folder)
            script = root / 'provider.py'
            script.write_text(
                'import json,pathlib,sys\n'
                'args=sys.argv[1:]; out=args[args.index("--output")+1]; p=(pathlib.Path.cwd()/out).resolve()\n'
                'row={"record_id":"rec_1","标题":"论文;$(echo nope)","关键词":["AI Agents"]}\n'
                'p.write_text(json.dumps(row,ensure_ascii=False)+"\\n",encoding="utf-8")\n'
                'm=p.with_suffix(".manifest.json"); m.write_text("{}",encoding="utf-8")\n'
                'print(json.dumps({"format":"ndjson","base_token":"base-test","table_id":"tblTest",'
                '"record_file":str(p),"manifest_file":str(m),"records_count":1,"has_more":False,"rev":7,'
                '"columns":{"record_id":{"physical_type":"string"},"标题":{"field_id":"fldTitle",'
                '"physical_type":"string|null"},"关键词":{"field_id":"fldTags","physical_type":"array<string>"}}}))\n',
                encoding='utf-8')
            runner = LarkRunner(executable=sys.executable, prefix_args=[str(script)])
            manifest, records = runner.export(
                ['base', '+record-list', '--base-token', 'base-test', '--table-id', 'tblTest',
                 '--field-id', 'fldTitle', '--field-id', 'fldTags'], root)
            self.assertEqual(manifest['records_count'], 1)
            self.assertEqual(records[0]['标题'], '论文;$(echo nope)')
            # The native record-get command accepts a repeated record projection.
            manifest, records = runner.export(
                ['base', '+record-get', '--base-token', 'base-test', '--table-id', 'tblTest',
                 '--record-id', 'rec_1', '--record-id', 'rec_2', '--field-id', 'fldTitle'], root)
            self.assertEqual(records[0]['record_id'], 'rec_1')

    def test_export_rejects_truncation_and_artifact_path_escape(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            script = root / 'provider.py'
            script.write_text(
                'import json,pathlib,sys\n'
                'mode=sys.argv[1]; args=sys.argv[2:]; out=args[args.index("--output")+1]; p=(pathlib.Path.cwd()/out).resolve()\n'
                'if mode=="manifest": print("{"); raise SystemExit\n'
                'p.write_text("{\\\"record_id\\\":\\\"rec\\\",\\\"标题\\\":\\\"RAW_RECORD_SECRET\\\"\\n" if mode=="bad" else "{\\\"record_id\\\":\\\"rec\\\"}\\n",encoding="utf-8")\n'
                'outside=(pathlib.Path.cwd().parent/"outside.ndjson").resolve()\n'
                'm=p.with_suffix(".manifest.json"); m.write_text("{}",encoding="utf-8")\n'
                'manifest={"format":"ndjson","base_token":"base-test","table_id":"tblTest",'
                '"record_file":str(outside if mode=="escape" else p),"manifest_file":str(m),'
                '"records_count":1,"has_more":False,"rev":7,"columns":{"record_id":{"physical_type":"string"}},'
                '"warnings":["warn"] if mode=="warning" else [],'
                '"ignored_fields":["x"] if mode=="ignored" else [],'
                '"result":"partial" if mode=="partial" else "success"}\n'
                'if mode=="missingrev": manifest.pop("rev")\n'
                'if mode=="badrev": manifest["rev"]=-1\n'
                'print(json.dumps(manifest))\n',
                encoding='utf-8')
            for mode in ('bad', 'escape', 'manifest', 'warning', 'ignored', 'partial', 'missingrev', 'badrev'):
                runner = LarkRunner(executable=sys.executable, prefix_args=[str(script), mode])
                with self.subTest(mode=mode), self.assertRaises(Paper2LarkError) as raised:
                    runner.export(['base', '+record-list', '--base-token', 'base-test', '--table-id', 'tblTest'], root)
                self.assertEqual(raised.exception.code, 'CLI_OUTPUT_INVALID')
                self.assert_private_error(raised.exception, 'RAW_RECORD_SECRET')

    def test_malformed_write_response_is_an_uncertain_remote_result(self):
        with tempfile.TemporaryDirectory() as folder:
            for index, output in enumerate(('RAW_PROVIDER_SECRET', '[]', '{"ok":true}')):
                script = Path(folder) / f'broken-{index}.py'
                script.write_text(f'print({output!r})\n', encoding='utf-8')
                runner = LarkRunner(executable=sys.executable, prefix_args=[str(script)])
                with self.subTest(output=output), self.assertRaises(Paper2LarkError) as raised:
                    runner.call(['base', '+record-batch-update', '--base-token', 'base', '--table-id', 'tbl',
                                 '--json', '{"update_records":{"rec_a":{"fldTitle":"A"}}}'])
                self.assertEqual(raised.exception.code, 'REMOTE_RESULT_UNCERTAIN')
                self.assert_private_error(raised.exception, 'RAW_PROVIDER_SECRET', 'fldTitle')

    def test_every_nonzero_write_exit_is_uncertain_but_read_errors_remain_classified(self):
        with tempfile.TemporaryDirectory() as folder:
            cases = [
                ('success', {'ok':True,'identity':'user','data':{}}, 'REMOTE_RESULT_UNCERTAIN'),
                ('network-write', {'ok':False,'error':{'type':'network','message':'RAW_EXIT_SECRET'}}, 'REMOTE_RESULT_UNCERTAIN'),
                ('network-read', {'ok':False,'error':{'type':'network','message':'RAW_EXIT_SECRET'}}, 'NETWORK_ERROR'),
            ]
            for name, response, expected in cases:
                script = Path(folder) / f'{name}.py'
                script.write_text('import json,sys\nprint(json.dumps(' + repr(response) + '),file=sys.stderr)\nsys.exit(1)\n', encoding='utf-8')
                runner = LarkRunner(executable=sys.executable, prefix_args=[str(script)])
                args = (['wiki', '+node-get', '--node-token', 'secret-token'] if name == 'network-read' else
                        ['base', '+record-batch-update', '--base-token', 'base', '--table-id', 'tbl',
                         '--json', '{"update_records":{"rec_a":{"fldTitle":"SECRET_TITLE"}}}'])
                with self.subTest(name=name), self.assertRaises(Paper2LarkError) as raised:
                    runner.call(args)
                self.assertEqual(raised.exception.code, expected)
                self.assert_private_error(raised.exception, 'RAW_EXIT_SECRET', 'SECRET_TITLE', 'secret-token')

    def test_process_start_failure_has_no_sensitive_exception_chain(self):
        with tempfile.TemporaryDirectory() as folder:
            fake = Path(folder) / 'not-a-program.exe'
            fake.write_text('not executable', encoding='utf-8')
            runner = LarkRunner(executable=fake)
            fake.unlink()
            with self.assertRaises(Paper2LarkError) as raised:
                runner.call(['base', '+record-batch-create', '--base-token', 'secret-base', '--table-id', 'secret-table',
                             '--json', '{"create_records":[{"fldTitle":"SECRET_TITLE"}]}'])
            self.assertEqual(raised.exception.code, 'REMOTE_RESULT_UNCERTAIN')
            self.assert_private_error(raised.exception, 'secret-base', 'secret-table', 'SECRET_TITLE')

    def test_missing_export_directory_has_no_sensitive_exception_chain(self):
        with tempfile.TemporaryDirectory() as folder:
            missing = Path(folder) / 'PRIVATE_PATH_MARKER' / 'missing'
            runner = LarkRunner(executable=sys.executable)
            with self.assertRaises(Paper2LarkError) as raised:
                runner.export(['base', '+record-list', '--base-token', 'base-test', '--table-id', 'tblTest'], missing)
            self.assertEqual(raised.exception.code, 'EXPORT_PATH_INVALID')
            self.assert_private_error(raised.exception, 'PRIVATE_PATH_MARKER')

    def test_nonfinite_json_constants_are_rejected_at_process_boundary(self):
        with tempfile.TemporaryDirectory() as folder:
            for constant in ('NaN', 'Infinity', '-Infinity'):
                for write in (False, True):
                    script = Path(folder) / f'constant-{constant.replace("-", "n")}-{write}.py'
                    script.write_text(
                        'import sys\nsys.stdout.write(\'{"ok":true,"identity":"user","data":{"value":'
                        + constant + '}}\')\n', encoding='utf-8')
                    runner = LarkRunner(executable=sys.executable, prefix_args=[str(script)])
                    args = (['base', '+record-batch-update', '--base-token', 'base', '--table-id', 'tbl',
                             '--json', '{"update_records":{"rec_a":{"fldYear":1}}}'] if write else
                            ['wiki', '+node-get', '--node-token', 'token'])
                    with self.subTest(constant=constant, write=write), self.assertRaises(Paper2LarkError) as raised:
                        runner.call(args)
                    self.assertEqual(raised.exception.code,
                                     'REMOTE_RESULT_UNCERTAIN' if write else 'CLI_OUTPUT_INVALID')
                    self.assert_private_error(raised.exception, constant)

    def test_exponent_overflow_is_rejected_in_read_and_write_envelopes(self):
        with tempfile.TemporaryDirectory() as folder:
            for write in (False, True):
                script = Path(folder) / f'overflow-{write}.py'
                script.write_text(
                    'import sys\n'
                    'sys.stdout.write(\'{"ok":true,"identity":"user","data":'
                    '{"provider_extra":{"RAW_OVERFLOW_SECRET":1e999}}}\')\n', encoding='utf-8')
                runner = LarkRunner(executable=sys.executable, prefix_args=[str(script)])
                args = (['base', '+record-batch-update', '--base-token', 'secret-base',
                         '--table-id', 'secret-table',
                         '--json', '{"update_records":{"rec_a":{"fldTitle":"SECRET_TITLE"}}}']
                        if write else ['wiki', '+node-get', '--node-token', 'secret-token'])
                with self.subTest(write=write), self.assertRaises(Paper2LarkError) as raised:
                    runner.call(args)
                self.assertEqual(raised.exception.code,
                                 'REMOTE_RESULT_UNCERTAIN' if write else 'CLI_OUTPUT_INVALID')
                self.assert_private_error(raised.exception, 'RAW_OVERFLOW_SECRET', '1e999',
                                          'SECRET_TITLE', 'secret-token', 'secret-base', 'secret-table')

    def test_deep_json_is_normalized_for_read_and_write_envelopes(self):
        with tempfile.TemporaryDirectory() as folder:
            for write in (False, True):
                script = Path(folder) / f'deep-{write}.py'
                script.write_text(
                    'import sys\n'
                    'deep="["*20000+"0"+"]"*20000\n'
                    'sys.stdout.write(\'{"ok":true,"identity":"user","data":'
                    '{"RAW_DEEP_SECRET":\'+deep+\'}}\')\n', encoding='utf-8')
                runner = LarkRunner(executable=sys.executable, prefix_args=[str(script)])
                args = (['base', '+record-batch-update', '--base-token', 'secret-base',
                         '--table-id', 'secret-table',
                         '--json', '{"update_records":{"rec_a":{"fldTitle":"SECRET_TITLE"}}}']
                        if write else ['wiki', '+node-get', '--node-token', 'secret-token'])
                with self.subTest(write=write), self.assertRaises(Paper2LarkError) as raised:
                    runner.call(args)
                self.assertEqual(raised.exception.code,
                                 'REMOTE_RESULT_UNCERTAIN' if write else 'CLI_OUTPUT_INVALID')
                self.assert_private_error(raised.exception, 'RAW_DEEP_SECRET',
                                          'SECRET_TITLE', 'secret-token', 'secret-base', 'secret-table')

    def test_json_depth_limit_rejects_nested_read_and_write_envelopes(self):
        with tempfile.TemporaryDirectory() as folder:
            for write in (False, True):
                script = Path(folder) / f'depth-limit-{write}.py'
                script.write_text(
                    'import sys\n'
                    'deep="["*500+"\\\"RAW_DEPTH_SECRET\\\""+"]"*500\n'
                    'sys.stdout.write(\'{"ok":true,"identity":"user","data":\'+deep+\'}\')\n',
                    encoding='utf-8')
                runner = LarkRunner(executable=sys.executable, prefix_args=[str(script)])
                args = (['base', '+record-batch-update', '--base-token', 'depth-base',
                         '--table-id', 'depth-table',
                         '--json', '{"update_records":{"rec_a":{"fldTitle":"DEPTH_TITLE"}}}']
                        if write else ['wiki', '+node-get', '--node-token', 'depth-token'])
                with self.subTest(write=write), self.assertRaises(Paper2LarkError) as raised:
                    runner.call(args)
                self.assertEqual(raised.exception.code,
                                 'REMOTE_RESULT_UNCERTAIN' if write else 'CLI_OUTPUT_INVALID')
                self.assert_private_error(raised.exception, 'RAW_DEPTH_SECRET', 'DEPTH_TITLE',
                                          'depth-token', 'depth-base', 'depth-table')

    def test_nonfinite_manifest_and_ndjson_values_are_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            script = root / 'nonfinite.py'
            script.write_text(
                'import json,pathlib,sys\n'
                'mode=sys.argv[1]; args=sys.argv[2:]; p=(pathlib.Path.cwd()/args[args.index("--output")+1]).resolve()\n'
                'p.write_text(\'{"record_id":"rec_1","value":NaN}\\n\' if mode=="row" else '
                '\'{"record_id":"rec_1"}\\n\',encoding="utf-8")\n'
                'm=p.with_suffix(".manifest.json"); m.write_text("{}",encoding="utf-8")\n'
                'manifest={"format":"ndjson","base_token":"base-test","table_id":"tblTest","rev":1,'
                '"record_file":str(p),"manifest_file":str(m),"records_count":1,"has_more":False,'
                '"columns":{"record_id":{"physical_type":"string"}}}\n'
                'if mode=="manifest": manifest["provider_extra"]=float("inf")\n'
                'print(json.dumps(manifest))\n', encoding='utf-8')
            for mode, marker in (('manifest', 'Infinity'), ('row', 'NaN')):
                runner = LarkRunner(executable=sys.executable, prefix_args=[str(script), mode])
                with self.subTest(mode=mode), self.assertRaises(Paper2LarkError) as raised:
                    runner.export(['base', '+record-list', '--base-token', 'base-test', '--table-id', 'tblTest'], root)
                self.assertEqual(raised.exception.code, 'CLI_OUTPUT_INVALID')
                self.assert_private_error(raised.exception, marker)

    def test_exponent_overflow_is_rejected_in_manifest_and_nested_ndjson_values(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            script = root / 'overflow-export.py'
            script.write_text(
                'import json,pathlib,sys\n'
                'mode=sys.argv[1]; args=sys.argv[2:]\n'
                'p=(pathlib.Path.cwd()/args[args.index("--output")+1]).resolve()\n'
                'row=(\'{"record_id":"rec_1","cell":{"nested":1e999}}\\n\' if mode=="cell" else '
                '\'{"record_id":"rec_1","provider_extra":{"nested":1e999}}\\n\' if mode=="extra" else '
                '\'{"record_id":"rec_1"}\\n\')\n'
                'p.write_text(row,encoding="utf-8")\n'
                'm=p.with_suffix(".manifest.json"); m.write_text("{}",encoding="utf-8")\n'
                'manifest={"format":"ndjson","base_token":"base-test","table_id":"tblTest","rev":1,'
                '"record_file":str(p),"manifest_file":str(m),"records_count":1,"has_more":False,'
                '"columns":{"record_id":{"physical_type":"string"}}}\n'
                'out=json.dumps(manifest)\n'
                'if mode=="manifest": out=out[:-1]+\',"provider_extra":{"nested":1e999}}\'\n'
                'sys.stdout.write(out)\n', encoding='utf-8')
            for mode in ('manifest', 'cell', 'extra'):
                runner = LarkRunner(executable=sys.executable, prefix_args=[str(script), mode])
                with self.subTest(mode=mode), self.assertRaises(Paper2LarkError) as raised:
                    runner.export(['base', '+record-list', '--base-token', 'base-test',
                                   '--table-id', 'tblTest'], root)
                self.assertEqual(raised.exception.code, 'CLI_OUTPUT_INVALID')
                self.assert_private_error(raised.exception, '1e999')

    def test_deep_json_is_normalized_for_manifest_and_ndjson(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            script = root / 'deep-export.py'
            script.write_text(
                'import json,pathlib,sys\n'
                'mode=sys.argv[1]; args=sys.argv[2:]; deep="["*20000+"0"+"]"*20000\n'
                'p=(pathlib.Path.cwd()/args[args.index("--output")+1]).resolve()\n'
                'row=(\'{"record_id":"rec_1","provider_extra":{"RAW_DEEP_SECRET":\'+deep+\'}}\\n\' '
                'if mode=="row" else \'{"record_id":"rec_1"}\\n\')\n'
                'p.write_text(row,encoding="utf-8")\n'
                'm=p.with_suffix(".manifest.json"); m.write_text("{}",encoding="utf-8")\n'
                'manifest={"format":"ndjson","base_token":"base-test","table_id":"tblTest","rev":1,'
                '"record_file":str(p),"manifest_file":str(m),"records_count":1,"has_more":False,'
                '"columns":{"record_id":{"physical_type":"string"}}}\n'
                'out=json.dumps(manifest)\n'
                'if mode=="manifest": out=out[:-1]+\',"provider_extra":{"RAW_DEEP_SECRET":\'+deep+\'}}\'\n'
                'sys.stdout.write(out)\n', encoding='utf-8')
            for mode in ('manifest', 'row'):
                runner = LarkRunner(executable=sys.executable, prefix_args=[str(script), mode])
                with self.subTest(mode=mode), self.assertRaises(Paper2LarkError) as raised:
                    runner.export(['base', '+record-list', '--base-token', 'base-test',
                                   '--table-id', 'tblTest'], root)
                self.assertEqual(raised.exception.code, 'CLI_OUTPUT_INVALID')
                self.assert_private_error(raised.exception, 'RAW_DEEP_SECRET')

    def test_json_depth_limit_rejects_nested_manifest_and_ndjson(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            script = root / 'depth-export.py'
            script.write_text(
                'import json,pathlib,sys\n'
                'mode=sys.argv[1]; args=sys.argv[2:]; deep="["*500+"\\\"RAW_DEPTH_SECRET\\\""+"]"*500\n'
                'p=(pathlib.Path.cwd()/args[args.index("--output")+1]).resolve()\n'
                'row=(\'{"record_id":"rec_1","provider_extra":\'+deep+\'}\\n\' '
                'if mode=="row" else \'{"record_id":"rec_1"}\\n\')\n'
                'p.write_text(row,encoding="utf-8")\n'
                'm=p.with_suffix(".manifest.json"); m.write_text("{}",encoding="utf-8")\n'
                'manifest={"format":"ndjson","base_token":"base-test","table_id":"tblTest","rev":1,'
                '"record_file":str(p),"manifest_file":str(m),"records_count":1,"has_more":False,'
                '"columns":{"record_id":{"physical_type":"string"}}}\n'
                'out=json.dumps(manifest)\n'
                'if mode=="manifest": out=out[:-1]+\',"provider_extra":\'+deep+\'}\'\n'
                'sys.stdout.write(out)\n', encoding='utf-8')
            for mode in ('manifest', 'row'):
                runner = LarkRunner(executable=sys.executable, prefix_args=[str(script), mode])
                with self.subTest(mode=mode), self.assertRaises(Paper2LarkError) as raised:
                    runner.export(['base', '+record-list', '--base-token', 'base-test',
                                   '--table-id', 'tblTest'], root)
                self.assertEqual(raised.exception.code, 'CLI_OUTPUT_INVALID')
                self.assert_private_error(raised.exception, 'RAW_DEPTH_SECRET')

    def test_export_rejects_unsafe_record_ids(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for index, record_id in enumerate(('   ', ' recvuWhOsIukaD', 'recvuWhOsIukaD ',
                                                'recvuWh\nOsIukaD', 'x' * 257)):
                script = root / f'bad-id-{index}.py'
                row = json.dumps({'record_id': record_id}, ensure_ascii=False) + '\n'
                script.write_text(
                    'import json,pathlib,sys\n'
                    'args=sys.argv[1:]; p=(pathlib.Path.cwd()/args[args.index("--output")+1]).resolve()\n'
                    f'p.write_text({row!r},encoding="utf-8")\n'
                    'm=p.with_suffix(".manifest.json"); m.write_text("{}",encoding="utf-8")\n'
                    'print(json.dumps({"format":"ndjson","base_token":"base-test","table_id":"tblTest",'
                    '"rev":1,"record_file":str(p),"manifest_file":str(m),"records_count":1,'
                    '"has_more":False,"columns":{"record_id":{"physical_type":"string"}}}))\n',
                    encoding='utf-8')
                runner = LarkRunner(executable=sys.executable, prefix_args=[str(script)])
                with self.subTest(record_id=repr(record_id)), self.assertRaises(Paper2LarkError) as raised:
                    runner.export(['base', '+record-list', '--base-token', 'base-test',
                                   '--table-id', 'tblTest'], root)
                self.assertEqual(raised.exception.code, 'CLI_OUTPUT_INVALID')


if __name__ == '__main__':
    unittest.main()
