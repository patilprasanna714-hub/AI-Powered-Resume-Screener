"""
services/nlp-parser/lambda_function.py
Owner: Sasi — Comprehend NLP & Data Parsing

Lambda entrypoint package matching the suggested repository structure (Appendix A).
Re-exports the handler from the core nlp_lambda module.
"""

import os
import sys

# Ensure parent directory is in sys.path
parent_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)

from nlp_lambda import lambda_handler, process_resume_record
