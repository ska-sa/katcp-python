# -*- coding: utf-8 -*-
"""Locate the katcp-python repository root.

Checks KATCP_REPO env var, git root of bench/, or falls back to the parent directory.
"""
from __future__ import absolute_import
import os
import subprocess


def katcp_repo():
    env = os.environ.get('KATCP_REPO')
    if env:
        return os.path.abspath(env)
    here = os.path.dirname(os.path.abspath(__file__))
    try:
        out = subprocess.check_output(
            ['git', '-C', here, 'rev-parse', '--show-toplevel'],
            stderr=subprocess.STDOUT)
        return out.decode('utf-8').strip()
    except Exception:
        return os.path.dirname(here)