import json
import tempfile
import unittest
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import project_review as R


class SourceReviewTests(unittest.TestCase):
    def test_literal_and_environment_fallbacks_rejected_without_disclosure(self):
        sources = ["app.config['SECRET_KEY']='private-fixture-value'",
                   "app.secret_key = os.environ.get('KEY', 'private-fixture-value')",
                   "SECRET_KEY = os.getenv('KEY') or 'private-fixture-value'",
                   "app.config.update(SECRET_KEY='private-fixture-value')"]
        for text in sources:
            result = R.inspect_source(text,'app.py')
            self.assertEqual(len(result),1)
            self.assertNotIn('private-fixture-value',json.dumps(result))
    def test_private_persisted_or_required_env_secrets_not_flagged(self):
        for text in ["app.secret_key = os.environ['SECRET_KEY']",
                     "app.config['SECRET_KEY'] = os.environ.get('KEY')",
                     "app.secret_key = load_persisted_secret()"]:
            self.assertEqual(R.inspect_source(text,'app.py'),[])
    def test_excludes_test_fixtures_dependencies_and_symlinks(self):
        with tempfile.TemporaryDirectory() as root:
            path=Path(root);(path/'app.py').write_text("app.secret_key = load_persisted_secret()")
            (path/'tests').mkdir();(path/'tests'/'fixture.py').write_text("app.secret_key='fixture'")
            (path/'test_app.py').write_text("app.secret_key='fixture'")
            (path/'node_modules').mkdir();(path/'node_modules'/'foreign.py').write_text("app.secret_key='fixture'")
            result=R.review_tree(root)
            self.assertEqual(result['python_files'],1);self.assertEqual(result['findings'],[])
    def test_syntax_error_has_location_without_source(self):
        with tempfile.TemporaryDirectory() as root:
            Path(root,'app.py').write_text("app.secret_key='private-fixture-value' ?")
            result=R.review_tree(root)
            self.assertEqual(result['findings'][0]['code'],'python-syntax')
            self.assertNotIn('private-fixture-value',json.dumps(result))
    def test_large_sources_fail_closed(self):
        with tempfile.TemporaryDirectory() as root:
            Path(root,'app.py').write_bytes(b'x'*(1024*1024+1))
            with self.assertRaises(ValueError):R.review_tree(root)
    def test_scanner_uses_literal_container_args_and_rejects_failure(self):
        container=MagicMock();container.exec_run.return_value=SimpleNamespace(exit_code=0,output=b'{"findings":[],"python_files":0}')
        self.assertEqual(R.review_container(container)['findings'],[])
        self.assertEqual(container.exec_run.call_args.args[0][:3],['timeout','15','python3'])
        container.exec_run.return_value=SimpleNamespace(exit_code=1,output=b'private value')
        with self.assertRaisesRegex(ValueError,'could not finish'):R.review_container(container)


if __name__=='__main__':unittest.main()
