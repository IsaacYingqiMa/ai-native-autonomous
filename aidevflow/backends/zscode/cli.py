"""Small installed maintenance CLI; user interaction lives in zscode's two commands."""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path
from .engine import LightGate, doctor
from ...storage import GateError


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'): stream.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, default=Path.cwd())
    parser.add_argument('command', choices=('status', 'doctor'))
    args = parser.parse_args()
    try:
        result = doctor(args.root) if args.command == 'doctor' else LightGate(args.root).summary()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (GateError, OSError) as exc:
        print(str(exc))
        return 1
