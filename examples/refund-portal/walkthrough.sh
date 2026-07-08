#!/bin/bash
# Full production-line walkthrough — run from an empty directory.
set -e
specline init
specline new refunds
echo ">> fill specs/refunds.md and plans/refunds.md (see tests/conftest.py GOOD_SPEC for a worked example)"
