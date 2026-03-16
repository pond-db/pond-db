#!/usr/bin/env bash
# rotate_jwt_secret.sh — Atomically rotate POND_JWT_SECRET in a .env file
#
# Environment variables:
#   POND_ENV_FILE  — path to the .env file (required)
#   POND_AUDIT_LOG — path to the audit log file (required)
#   POND_DRY_RUN   — set to "1" to preview changes without writing (default: 0)
#
# Behaviour:
#   1. Reads current POND_JWT_SECRET from POND_ENV_FILE
#   2. Generates a new 64-char hex secret via openssl
#   3. Writes old secret as POND_JWT_SECRET_V1 (token fallback window)
#   4. Writes new secret as POND_JWT_SECRET
#   5. Appends a JSON audit event to POND_AUDIT_LOG

set -euo pipefail

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
ENV_FILE="${POND_ENV_FILE:-.env}"
AUDIT_LOG="${POND_AUDIT_LOG:-rotation_audit.log}"
DRY_RUN="${POND_DRY_RUN:-0}"

# ---------------------------------------------------------------------------
# Validate inputs
# ---------------------------------------------------------------------------
if [[ ! -f "$ENV_FILE" ]]; then
    echo "ERROR: POND_ENV_FILE not found: $ENV_FILE" >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# Read current secret
# ---------------------------------------------------------------------------
OLD_SECRET=""
while IFS= read -r line; do
    if [[ "$line" == POND_JWT_SECRET=* ]]; then
        OLD_SECRET="${line#POND_JWT_SECRET=}"
        # Strip surrounding quotes if present
        OLD_SECRET="${OLD_SECRET%\"}"
        OLD_SECRET="${OLD_SECRET#\"}"
        OLD_SECRET="${OLD_SECRET%\'}"
        OLD_SECRET="${OLD_SECRET#\'}"
        break
    fi
done < "$ENV_FILE"

if [[ -z "$OLD_SECRET" ]]; then
    echo "ERROR: POND_JWT_SECRET not found in $ENV_FILE" >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# Generate new secret (64 hex chars = 256 bits of entropy)
# ---------------------------------------------------------------------------
NEW_SECRET=$(openssl rand -hex 32)

TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

if [[ "$DRY_RUN" == "1" ]]; then
    echo "DRY_RUN: would rotate POND_JWT_SECRET at $TIMESTAMP"
    echo "DRY_RUN: old secret (first 8 chars): ${OLD_SECRET:0:8}..."
    echo "DRY_RUN: new secret (first 8 chars): ${NEW_SECRET:0:8}..."
    exit 0
fi

# ---------------------------------------------------------------------------
# Rewrite .env atomically via a temp file
# ---------------------------------------------------------------------------
TMP_FILE=$(mktemp "${ENV_FILE}.XXXXXX")

# Copy all lines except POND_JWT_SECRET and POND_JWT_SECRET_V1
while IFS= read -r line; do
    case "$line" in
        POND_JWT_SECRET=*|POND_JWT_SECRET_V1=*)
            ;;  # drop old entries
        *)
            printf '%s\n' "$line" >> "$TMP_FILE"
            ;;
    esac
done < "$ENV_FILE"

# Append updated secrets
printf 'POND_JWT_SECRET=%s\n' "$NEW_SECRET" >> "$TMP_FILE"
printf 'POND_JWT_SECRET_V1=%s\n' "$OLD_SECRET" >> "$TMP_FILE"

# Atomic replace
mv "$TMP_FILE" "$ENV_FILE"

# ---------------------------------------------------------------------------
# Append audit event
# ---------------------------------------------------------------------------
cat >> "$AUDIT_LOG" <<EOF
{"event":"jwt_secret_rotation","timestamp":"${TIMESTAMP}","old_secret_prefix":"${OLD_SECRET:0:8}","new_secret_prefix":"${NEW_SECRET:0:8}","env_file":"${ENV_FILE}"}
EOF

echo "JWT secret rotation complete at $TIMESTAMP"
echo "Old secret preserved as POND_JWT_SECRET_V1 for token fallback."
