"""Exercise the real bundled engine in isolated Python, without test doubles."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
PROGRAM = r'''
import hashlib, json, math, os, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
# Access to personal skill directories must not be needed even for reports.
os.path.expanduser = lambda path: str(Path.cwd() / "no-personal-skills")
from core import ENGINE_PATHS, require_engine
from core.engine import analyze
import server
import run_chanlun
K = require_engine()
assert all('.workbuddy' not in p for p in ENGINE_PATHS), ENGINE_PATHS
assert Path(K.__file__).resolve() == Path(sys.argv[2]).resolve(), K.__file__
assert hashlib.sha256(Path(K.__file__).read_bytes()).hexdigest() == '61679c24c3af70cab9cc9d7d231fa9b68e4be654f2323f98b98098013fcda774'
for count, flat in ((1, True), (120, True), (240, False)):
    bars = []
    for i in range(count):
        close = 10 if flat else 20 + math.sin(i / 4) * 2 + i / 200
        bars.append(dict(date=20260000+i, open=close, close=close,
                         high=close+.2, low=close-.2, volume=1000))
    assert isinstance(K.detect(bars), list)
    assert '均线' in run_chanlun.report(bars)[0]
    if count >= 120:
        result = analyze(bars, with_report=True)
        assert result and '均线' in result['report'], result
        assert '报告生成失败' not in result['report']
assert Path(run_chanlun.__file__).resolve().parent == Path(sys.argv[1]) / 'core' / '_engine'
print(json.dumps(dict(engine=K.__file__, report=run_chanlun.__file__)))
'''


class BundledEngineTests(unittest.TestCase):
    def check_engine(self, override=None, expected=None):
        env = os.environ.copy()
        for key in ('CHANLUN_ENGINE_DIR', 'CHANLUN_ENGINE_SHA256', 'PYTHONPATH'):
            env.pop(key, None)
        if override is not None:
            env['CHANLUN_ENGINE_DIR'] = str(override)
        with tempfile.TemporaryDirectory() as home:
            completed = subprocess.run(
                [sys.executable, '-I', '-B', '-c', PROGRAM, str(ROOT),
                 str(expected or ROOT / 'core/_engine/chanlun_core.py')],
                cwd=home, env=env, capture_output=True, text=True, encoding='utf-8', timeout=30)
        self.assertEqual(completed.returncode, 0, completed.stderr + completed.stdout)
        self.assertIn('chanlun_core.py', json.loads(completed.stdout)['engine'])

    def test_real_analysis_and_reports_run_without_personal_skills(self):
        self.check_engine()

    def test_removed_skill_override_falls_back_to_bundle(self):
        with tempfile.TemporaryDirectory() as temp:
            self.check_engine(Path(temp) / 'deleted-skill')

    def test_explicit_engine_override_still_works(self):
        with tempfile.TemporaryDirectory() as temp:
            engine = Path(temp) / 'chanlun_core.py'
            engine.write_bytes((ROOT / 'core/_engine/chanlun_core.py').read_bytes())
            self.check_engine(temp, engine)
