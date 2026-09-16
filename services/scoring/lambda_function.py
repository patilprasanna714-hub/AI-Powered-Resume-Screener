"""
services/scoring/lambda_function.py
Owner: Chiranthan — Matching & Queue Processing

Lambda entrypoint package matching the suggested repository structure (Appendix A).
Re-exports the handler and scoring processors from the core lambda_function module.
"""

import os
import sys

# Ensure parent directory is in sys.path
parent_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)

from lambda_function import (
    lambda_handler,
    process_message,
    compute_candidate_score,
    get_job,
    get_candidate,
)
