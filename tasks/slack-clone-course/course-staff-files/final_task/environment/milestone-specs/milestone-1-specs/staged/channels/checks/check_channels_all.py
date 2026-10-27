#!/usr/bin/env python3
"""Check for every channels stage (B0 to B6); pass group ids to run only some. Prints one PASS/FAIL line per rule, then a RESULT line.

Usage: python3 /spec/channels/checks/check_channels_all.py [B0 B1 B2 B3 B4 B5 B6]     Exit 0 only if every rule passes.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from channels_rules import GROUPS  # noqa: E402
from checklib import run  # noqa: E402

STAGE = ('B0', 'B1', 'B2', 'B3', 'B4', 'B5', 'B6')
run([g for g in GROUPS if g[0] in STAGE], sys.argv[1:], "python3 /spec/channels/checks/check_channels_all.py [B0 B1 B2 B3 B4 B5 B6]")
