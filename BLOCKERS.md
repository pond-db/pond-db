# Blockers for OSS Release

## Git History Contains Personal Email

**Severity:** Medium  
**Status:** Blocked — manual intervention required

The personal email `[REDACTED]` appears in commit `81666e9` (UI refactor part 3/3). This commit added it to:
1. `src/ponddb/templates/landing.html` (mailto link)
2. `tests/test_ui_part3.py` (test assertion)

Both have been scrubbed from current code, but the email remains in git history.

### Resolution: Run BFG Repo Cleaner before making the repo public

```bash
# 1. Install BFG
wget https://repo1.maven.org/maven2/com/madgit/bfg/1.14.0/bfg-1.14.0.jar -O bfg.jar

# 2. Create a file with strings to replace
echo '[REDACTED]==>contact@databasecompany.com' > replacements.txt

# 3. Make a fresh bare clone of the repo (BFG needs a bare clone)
git clone --mirror git@github.com:pond-db/pond-db.git pond-db-mirror.git

# 4. Run BFG to replace the email in history
java -jar bfg.jar --replace-text replacements.txt db-engine-mirror.git

# 5. Clean up and update refs
cd db-engine-mirror.git
git reflog expire --expire=now --all
git gc --prune=now --aggressive

# 6. Force push cleaned history
git push --force

# 7. All collaborators must re-clone or do: git fetch && git reset --hard origin/main
```

**Note:** Force-pushing rewrites git history. Coordinate with all collaborators before doing this.

## No Actual Secret Values Found in Git History

The following searches returned no results (no actual secrets were committed):
- `POND_JWT_SECRET=` in .env or .txt files
- Discord webhook URLs
- Internal IP address `192.168.88.x` (server IP)
- Hardcoded passwords

The only issue is the personal email in commit `81666e9`.
