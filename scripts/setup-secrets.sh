#!/usr/bin/env bash
# Create Docker secrets files for docker-compose.yml
# Run this once before 'docker compose up'

set -e

mkdir -p secrets

if [ ! -f secrets/jwt_secret.txt ]; then
    python3 -c "import secrets; print(secrets.token_hex(32))" > secrets/jwt_secret.txt
    echo "Generated: secrets/jwt_secret.txt"
else
    echo "Already exists: secrets/jwt_secret.txt"
fi

if [ ! -f secrets/api_key.txt ]; then
    python3 -c "import secrets; print(secrets.token_urlsafe(32))" > secrets/api_key.txt
    echo "Generated: secrets/api_key.txt"
else
    echo "Already exists: secrets/api_key.txt"
fi

if [ ! -f secrets/session_secret.txt ]; then
    python3 -c "import secrets; print(secrets.token_hex(32))" > secrets/session_secret.txt
    echo "Generated: secrets/session_secret.txt"
else
    echo "Already exists: secrets/session_secret.txt"
fi

echo ""
echo "Secrets ready. Run: docker compose up"
