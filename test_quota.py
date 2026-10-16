"""Quota regression tests use fake CLI processes and isolated configuration."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

import quota


class QuotaTests(unittest.TestCase):
    def test_normalize_missing_zero_invalid_and_expired_windows(self):
        result = {"rateLimitsByLimitId": {"codex": {
            "primary": {"usedPercent": 0, "windowDurationMins": 300, "resetsAt": 2000},
            "secondary": {"usedPercent": 75, "windowDurationMins": 10080, "resetsAt": 3000}}}}
        windows = quota.codex_windows(result)
        self.assertEqual([item["label"] for item in windows], ["5h", "7d"])
        self.assertIn("已用 0%", quota.display("Codex", {"windows": windows}, now=1000))
        self.assertIn("5h 待刷新", quota.display("Codex", {"windows": windows}, now=2000))
        self.assertEqual(quota.codex_windows({}), [])
        for invalid in (None, True, -1, 101, "20", float("nan")):
            self.assertEqual(quota.codex_windows({"rateLimits": {
                "primary": {"usedPercent": invalid}}}), [])
        self.assertEqual(quota.codex_windows({"rateLimits": {"primary": None}}), [])

    def test_codex_protocol_notifications_and_process_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            fake = Path(directory) / "codex"
            pid_file = Path(directory) / "pid"
            fake.write_text("#!/usr/bin/python3\nimport json,sys,os\n"
                            f"open({str(pid_file)!r},'w').write(str(os.getpid()))\n"
                            "for line in sys.stdin:\n"
                            " r=json.loads(line)\n"
                            " if r['method']=='initialize':\n"
                            "  print(json.dumps({'id':r['id'],'result':{}}),flush=True)\n"
                            " elif r['method']=='account/rateLimits/read':\n"
                            "  print(json.dumps({'method':'account/updated','params':{}}),flush=True)\n"
                            "  print(json.dumps({'id':r['id'],'result':{'rateLimits':{'primary':"
                            "{'usedPercent':42,'windowDurationMins':300,'resetsAt':9999999999}}}}),flush=True)\n")
            fake.chmod(0o755)
            with patch("quota.shutil.which", return_value=str(fake)):
                result = quota.codex_quota(timeout=2)
            self.assertEqual(result["windows"][0]["used"], 42)
            self.assertFalse(Path("/proc/" + pid_file.read_text()).exists())

    def test_codex_timeout_is_bounded_and_reaps_child(self):
        with tempfile.TemporaryDirectory() as directory:
            fake = Path(directory) / "codex"
            pid_file = Path(directory) / "pid"
            fake.write_text("#!/usr/bin/python3\nimport os,time\n"
                            f"open({str(pid_file)!r},'w').write(str(os.getpid()))\ntime.sleep(60)\n")
            fake.chmod(0o755)
            with patch("quota.shutil.which", return_value=str(fake)):
                result = quota.codex_quota(timeout=0.2)
            self.assertEqual(result["message"], "查询超时")
            self.assertFalse(Path("/proc/" + pid_file.read_text()).exists())

    def test_remote_probe_batch_mode_and_no_unexpected_output(self):
        result = subprocess.CompletedProcess([], 0, '{"codex":{"message":"未登录"}}', '')
        with patch("quota.subprocess.run", return_value=result) as run:
            self.assertIn("codex", quota.fetch("dev-server"))
            args, kwargs = run.call_args
            self.assertIn("-oBatchMode=yes", args[0])
            self.assertIn("dev-server", args[0])
            self.assertIn("source", json.loads(kwargs["input"]))
        with self.assertRaises(ValueError):
            quota.fetch("-oProxyCommand=bad")
        result.returncode, result.stderr = 255, "private diagnostic"
        with patch("quota.subprocess.run", return_value=result):
            self.assertNotIn("private", str(quota.fetch("dev-server")))

    def test_remote_bootstrap_probe_with_isolated_home(self):
        # Execute the exact remote shell command, replacing only SSH transport.
        # No real SSH connection, account credentials or CLI is used.
        real_run = subprocess.run

        def fake_transport(command, **kwargs):
            return real_run(["/bin/sh", "-c", command[-1]], **kwargs)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.dict(os.environ, {"HOME": str(root), "PATH": "/usr/bin:/bin",
                                         "XDG_DATA_HOME": str(root / "data 空格"),
                                         "XDG_CACHE_HOME": str(root / "cache")}):
                with patch("quota.subprocess.run", side_effect=fake_transport):
                    result = quota.fetch("dev-server")
                    self.assertEqual(result["codex"]["message"], "未安装 CLI")



if __name__ == "__main__":
    unittest.main(verbosity=2)
