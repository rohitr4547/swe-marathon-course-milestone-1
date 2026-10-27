#!/usr/bin/env python3
"""Check for /spec/channels/channels.md (rule groups B0 to B6). Prints one PASS/FAIL line per rule, then a RESULT line.

Usage: python3 /spec/channels/checks/check_channels.py [B0 B1 B2 B3 B4 B5 B6]     Exit 0 only if every rule passes.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from channels_rules import GROUPS  # noqa: E402
from checklib import run  # noqa: E402

run(GROUPS, sys.argv[1:], "python3 /spec/channels/checks/check_channels.py [B0 B1 B2 B3 B4 B5 B6]")
