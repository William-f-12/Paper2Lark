import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from paper2lark import config
from paper2lark.errors import Paper2LarkError


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name) / 'private'

    def test_default_read_creates_nothing(self):
        result = config.load_config(home=self.home, environ={})
        self.assertEqual(result['settings']['language']['content'], 'en')
        self.assertFalse(result['settings']['workflow']['mark_read_after_publish'])
        self.assertFalse(self.home.exists())

    def test_initialize_preserves_custom_config_and_precedence(self):
        config.initialize(self.home)
        path = self.home / 'config.toml'
        source = 'schema_version=1\ndefault_profile="personal"\n[profiles.personal.language]\ncontent="zh-CN"\n'
        path.write_text(source, encoding='utf-8')
        config.initialize(self.home)
        self.assertEqual(path.read_text(encoding='utf-8'), source)
        envfile = Path(self.temp.name) / 'explicit.env'
        envfile.write_text('PAPER2LARK_CONTENT_LANGUAGE=fr\n', encoding='utf-8')
        result = config.load_config(self.home, environ={'PAPER2LARK_CONTENT_LANGUAGE':'de'}, env_file=envfile)
        self.assertEqual(result['settings']['language']['content'], 'de')
        result = config.load_config(self.home, overrides={'content_language':'ja'}, environ={'PAPER2LARK_CONTENT_LANGUAGE':'de'}, env_file=envfile)
        self.assertEqual(result['settings']['language']['content'], 'ja')

    def test_unsafe_or_unknown_settings_rejected(self):
        for kwargs in ({'home':'relative'}, {'profile':'../bad'}, {'overrides':{'secret':'x'}}, {'overrides':{'wiki_url':'https://example.test/wiki/a'}}):
            with self.subTest(kwargs=kwargs), self.assertRaises(Paper2LarkError):
                config.load_config(environ={}, **kwargs)
        self.home.mkdir()
        for fragment in ('schema_version=2', 'schema_version=1\nsecret="hidden"', 'schema_version=1\n[profiles.personal.keywords]\nmax_words=true', 'schema_version=1\n[profiles.personal.keywords]\nmax_per_paper=9', 'schema_version=1\n[profiles.personal.language]\nkeywords="zh-CN"'):
            (self.home/'config.toml').write_text(fragment, encoding='utf-8')
            with self.subTest(fragment=fragment), self.assertRaises(Paper2LarkError):
                config.load_config(self.home, environ={})

    def test_env_file_rejects_credentials(self):
        path = Path(self.temp.name)/'explicit.env'
        path.write_text('LARK_ACCESS_TOKEN=secret', encoding='utf-8')
        with self.assertRaises(Paper2LarkError) as error:
            config.load_config(self.home, env_file=path, environ={})
        self.assertNotIn('secret', str(error.exception))

    def test_unsupported_policy_values_rejected(self):
        self.home.mkdir()
        for section, assignment in (('keywords', 'reuse_existing_first=false'),
                                    ('workflow', 'existing_note="overwrite"'),
                                    ('workflow', 'archive_strategy="auto"'),
                                    ('reading', 'depth="deep"')):
            with self.subTest(assignment=assignment):
                (self.home/'config.toml').write_text('schema_version=1\n[profiles.personal.'+section+']\n'+assignment, encoding='utf-8')
                with self.assertRaises(Paper2LarkError) as error:
                    config.load_config(self.home, environ={})
                self.assertEqual(error.exception.code, 'CONFIG_INVALID')

    def test_nul_home_rejected_with_stable_error(self):
        value = str(self.home)+'\x00'
        for operation in (lambda:config.load_config(value, environ={}), lambda:config.initialize(value)):
            with self.assertRaises(Paper2LarkError) as error:
                operation()
            self.assertEqual(error.exception.code, 'CONFIG_INVALID')
        self.assertFalse(self.home.exists())

    def test_cli_override_requires_absolute_executable_location(self):
        for value in ('lark-cli', './lark-cli', 'lark-cli --identity user', '"'+str(self.home/'lark-cli.exe')+'"'):
            with self.subTest(value=value), self.assertRaises(Paper2LarkError):
                config.load_config(self.home, overrides={'lark_cli':value}, environ={})
        executable = str(self.home/'tools with spaces'/'lark-cli.exe')
        self.assertEqual(config.load_config(self.home, overrides={'lark_cli':executable}, environ={})['settings']['lark']['cli'], executable)
