#!/usr/bin/env python3
"""
RSS Article Monitor - Entry point
"""

import sys
import io
import argparse

# Fix Windows console encoding (GBK -> UTF-8)
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

from src.core.monitor import RSSMonitor
from src.runtime_paths import RuntimePathError, optional_run_dir, require_skill_data_dir, require_content_root


def main() -> int:
    parser = argparse.ArgumentParser(description="Save RSS articles into the bound mymind root.")
    parser.add_argument("--config", default="", help="Package config path; defaults to config.yaml inside this Skill.")
    parser.add_argument("--content-root", default="", help="Explicit content root; defaults to OPENMIND_ROOT (legacy alias CCTOOLS_MYMIND_ROOT).")
    parser.add_argument("--skill-data-dir", default="", help="App-private Skill data directory.")
    parser.add_argument("--run-dir", default="", help="App-private Run working directory.")
    parser.add_argument("--date", default="", help="Output date in YYYYMMDD; defaults to the local current date.")
    args = parser.parse_args()
    try:
        root = require_content_root(args.content_root)
        data_dir = require_skill_data_dir(args.skill_data_dir)
        optional_run_dir(args.run_dir)
        monitor = RSSMonitor(args.config, str(root), str(data_dir), args.date)
        monitor.monitor()
        return 0
    except KeyboardInterrupt:
        print("\nInterrupted by user")
        return 130
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
