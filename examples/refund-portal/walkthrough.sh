#!/usr/bin/env bash
# Full production-line walkthrough on the worked example: every README quickstart step, each must exit 0.
# Usage: bash examples/refund-portal/walkthrough.sh [workdir]   (default: a fresh temporary directory)
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
work="${1:-$(mktemp -d)}"
mkdir -p "$work" && cd "$work"
step() { echo ">> specline $*"; specline "$@"; }
step init
step new refunds
cp "$here/specs/refunds.md" specs/refunds.md
cp "$here/plans/refunds.md" plans/refunds.md
step validate refunds
step strict refunds
step verify-validators refunds
step optimize-prd refunds
step gate spec refunds
step tasks refunds
step gate plan refunds
step loop next refunds
step loop done refunds T1
step handoff refunds
step challenge refunds
step agent claude
step status
if command -v hsf >/dev/null 2>&1; then echo ">> hsf validate"; hsf validate handoff/refunds_decisions.yaml; fi
echo "walkthrough OK in $work"
