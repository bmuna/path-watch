#!/usr/bin/env python3
from __future__ import annotations

import json
import sys

try:
    from model import MODEL_PATH, train
except ModuleNotFoundError:
    sys.stderr.write(
        "use the project venv, not system python3:\n"
        "  source .venv/bin/activate\n"
        "  pip install -r requirements.txt\n"
        "  python train_model.py\n"
        "or:  .venv/bin/python train_model.py\n"
    )
    raise SystemExit(1)


def main() -> int:
    report = train()
    print(json.dumps(report, indent=2))
    print(f"wrote {MODEL_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
