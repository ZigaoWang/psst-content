#!/bin/sh
# Installs a pre-commit hook that refuses commits with badly formatted or invalid area files.
# Run once after cloning: sh scripts/install-hooks.sh
set -e
root=$(git rev-parse --show-toplevel)
hook="$root/.git/hooks/pre-commit"
cat > "$hook" <<'HOOK'
#!/bin/sh
# Only check when area files are part of the commit.
if git diff --cached --name-only --diff-filter=ACM | grep -q '^areas/.*\.json$'; then
  python3 scripts/format.py --check || { echo "Run: python3 scripts/format.py"; exit 1; }
  python3 scripts/validate.py --quiet-warnings || { echo "Fix the validation errors before committing."; exit 1; }
fi
HOOK
chmod +x "$hook"
echo "Installed $hook"
