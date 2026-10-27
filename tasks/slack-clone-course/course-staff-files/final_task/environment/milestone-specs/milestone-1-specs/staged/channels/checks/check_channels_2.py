#!/usr/bin/env python3
"""Check for channels-2.md (rule groups B2). Prints one PASS/FAIL line per rule, then a RESULT line.

Usage: python3 /spec/channels/checks/check_channels_2.py [B2]     Exit 0 only if every rule passes.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from channels_rules import GROUPS  # noqa: E402
from checklib import run  # noqa: E402

STAGE = ('B2',)
run([g for g in GROUPS if g[0] in STAGE], sys.argv[1:], "python3 /spec/channels/checks/check_channels_2.py [B2]")
