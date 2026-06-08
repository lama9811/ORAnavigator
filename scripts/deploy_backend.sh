#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Safe backend deploy wrapper.
#
# `deploy-cloudrun.sh backend` uses `--set-env-vars`, which REPLACES the whole
# env block -- so it silently WIPES the literal env vars it doesn't list
# (SMTP_*, API_URL, RESEARCH_SECRET, ALLOW_TEST_EMAILS, FROM_EMAIL) and resets
# `--min-instances 0` (undoing the warm-instance / cold-start fix).
#
# This wrapper:
#   1. captures the CURRENT literal env vars + min-instances from the running
#      service (live, into a temp file -- NEVER committed),
#   2. runs the normal backend deploy,
#   3. re-applies any captured literal vars the deploy dropped, plus the
#      previous min-instances,
#   4. verifies health.
#
# No secrets are stored in the repo: values are read live each run. Replaces
# the old, non-durable /tmp/post_deploy_backend.sh.
#
# Usage:  bash scripts/deploy_backend.sh
# ---------------------------------------------------------------------------
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export PATH="$HOME/google-cloud-sdk/bin:$PATH"
PROJECT="infra-vertex-494621-v1"
REGION="us-central1"
SERVICE="oranavigator-backend"
GCLOUD="$(command -v gcloud || echo "$HOME/google-cloud-sdk/bin/gcloud")"
PY="$(command -v python3 || echo python3)"

BACKUP="$(mktemp /tmp/ora_env_backup.XXXXXX.json)"
trap 'rm -f "$BACKUP"' EXIT

echo "[safe-deploy] 1/4 capturing current env + min-instances..."
"$GCLOUD" run services describe "$SERVICE" --region="$REGION" --project="$PROJECT" \
    --format=json > "$BACKUP" || { echo "[safe-deploy] FAILED to describe service"; exit 1; }

echo "[safe-deploy] 2/4 deploying backend (this rebuilds the image)..."
bash "$SCRIPT_DIR/deploy-cloudrun.sh" backend
DEPLOY_RC=$?
if [ "$DEPLOY_RC" -ne 0 ]; then
    echo "[safe-deploy] deploy returned $DEPLOY_RC -- still attempting env restore for safety."
fi

echo "[safe-deploy] 3/4 restoring any wiped literal env vars + min-instances..."
"$PY" - "$BACKUP" "$SERVICE" "$REGION" "$PROJECT" "$GCLOUD" <<'PY'
import json, subprocess, sys, os
backup, service, region, project, gcloud = sys.argv[1:6]

before = json.load(open(backup))
bc = before["spec"]["template"]["spec"]["containers"][0]
# literal env vars only (skip secretKeyRef -- the deploy re-applies those via --set-secrets)
literals = {e["name"]: e["value"] for e in bc.get("env", []) if "value" in e}
minscale = (before["spec"]["template"]["metadata"].get("annotations", {})
            .get("autoscaling.knative.dev/minScale") or "1")

after = json.loads(subprocess.check_output(
    [gcloud, "run", "services", "describe", service,
     "--region", region, "--project", project, "--format=json"]))
now_names = {e["name"] for e in after["spec"]["template"]["spec"]["containers"][0].get("env", [])}

missing = {k: v for k, v in literals.items() if k not in now_names}
pairs = []
for k, v in missing.items():
    if "|" in v:
        print(f"   !! {k} value contains '|' delimiter; restore it manually")
        continue
    pairs.append(f"{k}={v}")

args = [gcloud, "run", "services", "update", service,
        "--region", region, "--project", project, "--min-instances", str(minscale)]
if pairs:
    args += ["--update-env-vars", "^|^" + "|".join(pairs)]

print(f"   re-applying {len(pairs)} env var(s): {sorted(missing)}  + min-instances={minscale}")
r = subprocess.run(args, capture_output=True, text=True)
for line in (r.stdout + r.stderr).splitlines():
    if any(s in line for s in ("revision", "serving", "Done", "ERROR")):
        print("   " + line.strip())
sys.exit(r.returncode)
PY
RESTORE_RC=$?

echo "[safe-deploy] 4/4 verifying..."
URL="$("$GCLOUD" run services describe "$SERVICE" --region="$REGION" --project="$PROJECT" --format='value(status.url)')"
CODE="$(curl -s -o /dev/null -w '%{http_code}' "$URL/health")"
echo "[safe-deploy] health: $CODE   (restore rc=$RESTORE_RC, deploy rc=$DEPLOY_RC)"
if [ "$CODE" != "200" ]; then
    echo "[safe-deploy] WARNING: health is not 200 -- investigate before declaring success."
    exit 1
fi
echo "[safe-deploy] done. Env + min-instances preserved."
