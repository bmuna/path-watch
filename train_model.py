#!/usr/bin/env python3
from __future__ import annotations

import json
import sys

from model import MODEL_PATH, train


def main() -> int:
    report = train()
    print(json.dumps(report, indent=2))
    print(f"wrote {MODEL_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
