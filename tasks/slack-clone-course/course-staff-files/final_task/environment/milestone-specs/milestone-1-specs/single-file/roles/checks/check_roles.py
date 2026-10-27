#!/usr/bin/env python3
"""Check for /spec/roles/roles.md (rule groups A0 to A4). Prints one PASS/FAIL line per rule, then a RESULT line.

Usage: python3 /spec/roles/checks/check_roles.py [A0 A1 A2 A3 A4]     Exit 0 only if every rule passes.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from roles_rules import GROUPS  # noqa: E402
from checklib import run  # noqa: E402

run(GROUPS, sys.argv[1:], "python3 /spec/roles/checks/check_roles.py [A0 A1 A2 A3 A4]")
