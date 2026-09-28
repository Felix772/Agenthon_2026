import argparse
import sys
from pathlib import Path

from .solver import solve


def main(argv=None):
    parser = argparse.ArgumentParser(description="QFBench coding agent")
    commands = parser.add_subparsers(dest="command", required=True)
    command = commands.add_parser("solve")
    command.add_argument("--task-dir", type=Path, required=True)
    command.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        solve(args.task_dir, args.out)
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"solve failed: {exc}", file=sys.stderr)
        return 1
    return 0
