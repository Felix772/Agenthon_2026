import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest

from agent.workspace import finalize_output_permissions


@unittest.skipUnless(os.name == "posix", "POSIX output modes are enforced in Linux")
class OutputPermissionTests(unittest.TestCase):
    def test_rejected_cli_output_leaves_existing_tree_unchanged(self):
        for kind in ("nonempty", "same_as_input", "inside_input", "ancestor_of_input", "symlink"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                task = root / "task"
                task.mkdir()
                (task / "instruction.md").write_text("Preserve this input")
                (task / "card.toml").write_text("[agent]\ntimeout_sec = 10\n")
                out = root / "out"
                if kind == "nonempty":
                    out.mkdir()
                    (out / ".agent").mkdir()
                    (out / ".agent/run.json").write_text("prior run")
                elif kind == "same_as_input":
                    out = task
                elif kind == "inside_input":
                    out = task / "new-output"
                elif kind == "ancestor_of_input":
                    out = root
                else:
                    out.symlink_to(task, target_is_directory=True)
                for path in [root, *root.rglob("*")]:
                    if not path.is_symlink():
                        path.chmod(0o700 if path.is_dir() else 0o600)
                def snapshot():
                    result = {}
                    for path in [root, *root.rglob("*")]:
                        info = path.lstat()
                        data = os.readlink(path) if path.is_symlink() else path.read_bytes() if path.is_file() else None
                        result[str(path.relative_to(root))] = (info.st_mode, info.st_uid, info.st_gid,
                                                               info.st_dev, info.st_ino, data)
                    return result
                before = snapshot()
                run = subprocess.run([sys.executable, "-m", "agent", "solve", "--task-dir", str(task),
                                      "--out", str(out)], capture_output=True, timeout=15)
                self.assertEqual(run.returncode, 1, run.stderr)
                self.assertEqual(snapshot(), before)

    def test_complete_tree_readable_and_executable_bits_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            nested = root / ".agent/attempt-1"
            nested.mkdir(parents=True)
            plain, executable = nested / "run.json", root / "run.sh"
            plain.write_text("{}")
            executable.write_text("#!/bin/sh\nexit 0\n")
            for path in (root, root / ".agent", nested):
                path.chmod(0o700)
            plain.chmod(0o600)
            executable.chmod(0o777)
            finalize_output_permissions(root)
            self.assertEqual(stat.S_IMODE(root.stat().st_mode), 0o755)
            self.assertEqual(stat.S_IMODE(nested.stat().st_mode), 0o755)
            self.assertEqual(stat.S_IMODE(plain.stat().st_mode), 0o644)
            self.assertEqual(stat.S_IMODE(executable.stat().st_mode), 0o755)
            self.assertEqual(plain.read_text(), "{}")

    def test_refuses_links_and_special_files_before_chmod(self):
        for kind in ("symlink", "hardlink", "fifo"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                regular = root / "regular"
                regular.write_text("unchanged")
                regular.chmod(0o600)
                other = root / "unsafe"
                if kind == "symlink":
                    other.symlink_to(regular)
                elif kind == "hardlink":
                    os.link(regular, other)
                else:
                    os.mkfifo(other)
                with self.assertRaises(ValueError):
                    finalize_output_permissions(root)
                self.assertEqual(stat.S_IMODE(regular.stat().st_mode), 0o600)

    def test_missing_output_is_left_absent(self):
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "absent"
            finalize_output_permissions(missing)
            self.assertFalse(missing.exists())
