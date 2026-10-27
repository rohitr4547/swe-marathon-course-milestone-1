#!/usr/bin/env python3
"""Check for roles-1.md (rule groups A0, A1). Prints one PASS/FAIL line per rule, then a RESULT line.

Usage: python3 /spec/roles/checks/check_roles_1.py [A0 A1]     Exit 0 only if every rule passes.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from roles_rules import GROUPS  # noqa: E402
from checklib import run  # noqa: E402

STAGE = ('A0', 'A1')
run([g for g in GROUPS if g[0] in STAGE], sys.argv[1:], "python3 /spec/roles/checks/check_roles_1.py [A0 A1]")
