"""
services/reliability/lambda_function.py
Owner: Shashank — DynamoDB, DevOps, Reliability & QA

Lambda entrypoint package matching the suggested repository structure (Appendix A).
Re-exports the handler and reliability processors from the core reliability_lambda module.
"""

import os
import sys

# Ensure parent directory is in sys.path
parent_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)

from reliability_lambda import (
    lambda_handler,
    get_aws_resources,
)
