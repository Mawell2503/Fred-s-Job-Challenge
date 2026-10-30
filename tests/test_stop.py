"""Stopping a run: SIGTERM to `python -m hiring ...` takes its claude processes down with it.
python -m unittest discover -s tests   (no AI: `claude` is a stand-in script that only sleeps)"""
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    # a finished child of ours that nobody reaped yet is a zombie: dead for our purpose
    try:
        return Path(f"/proc/{pid}/stat").read_text().split()[2] != "Z"
    except OSError:
        return True


@unittest.skipIf(os.name != "posix", "SIGTERM handling is POSIX only")
class StopRun(unittest.TestCase):
    def test_sigterm_kills_the_claude_processes_and_exits(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            pidfile = tmp / "claude.pid"
            fake = tmp / "claude"
            fake.write_text(f"#!{sys.executable}\nimport os, time\n"
                            f"open({str(pidfile)!r}, 'w').write(str(os.getpid()))\ntime.sleep(60)\n")
            fake.chmod(0o755)
            (tmp / "in.json").write_text(json.dumps({"title": "Data engineer", "ad_text": "Python and SQL."}))
            env = {**os.environ, "LLM_BACKEND": "claude_cli", "CLAUDE_BIN": str(fake), "PYTHONPATH": str(REPO)}
            p = subprocess.Popen([sys.executable, "-m", "hiring", "clara", "--input", str(tmp / "in.json"),
                                  "--out", str(tmp / "out.json")], env=env, cwd=tmp,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            try:
                for _ in range(100):                 # wait for the stand-in claude to start
                    if pidfile.exists() and pidfile.read_text():
                        break
                    time.sleep(0.05)
                claude_pid = int(pidfile.read_text())
                self.assertTrue(alive(claude_pid))
                p.send_signal(signal.SIGTERM)
                p.wait(timeout=10)
                self.assertEqual(p.returncode, 128 + signal.SIGTERM)
                for _ in range(40):
                    if not alive(claude_pid):
                        break
                    time.sleep(0.05)
                self.assertFalse(alive(claude_pid), "the claude process outlived the stopped run")
                self.assertFalse((tmp / "out.json").exists())
            finally:
                if p.poll() is None:
                    p.kill()
                    p.wait()


if __name__ == "__main__":
    unittest.main()
