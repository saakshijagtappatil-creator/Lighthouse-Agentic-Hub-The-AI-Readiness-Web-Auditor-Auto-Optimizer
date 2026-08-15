#!/bin/bash
set -e

echo "=== Running Custom Pytest-based Evaluations ==="
uv run pytest tests/eval/test_eval_quality.py -v -s
