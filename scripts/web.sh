#!/usr/bin/env bash
#
# web.sh — the one entry point for building and running Hardwood Web (WEB_DESIGN.md §12.1).
#
# Single-origin is the whole point of this project's design (WEB_DESIGN.md §0): the browser
# never makes a cross-origin request, because `uvicorn` serves both `/v1` and the built SPA
# from one process on one port. That means there is no separate "frontend server" to run in
# development the way a typical React app has one — the workflow is build the static bundle,
# then point the same backend at it, which is exactly what `setup` and `dev` below do. `npm
# run dev` (Vite's own dev server, proxying `/v1` back to this same backend — see
# `vite.config.ts`) is still there for iterating on a component in isolation; it is not what
# this script's `dev` subcommand runs.
#
# Commands:
#   setup    Create a Python virtualenv (if absent), pip-install the backend with the [serve,web]
#            extras, copy backend/.env.example to backend/.env (if absent), then npm ci + npm
#            run build the web app. Safe to re-run.
#   dev      Serve http://127.0.0.1:8000 with uvicorn, backed by whatever backend/.env and
#            web/dist already contain. Requires `setup` (or an equivalent manual install) to
#            have run at least once.
#   build    Rebuild web/dist only (npm run build), without touching the Python side. For
#            iterating on the frontend against an already-running `dev` in another terminal —
#            reload the page after this finishes.
#   doctor   Print what is configured, what is not, and *why* each optional piece (Google,
#            Apple, mail, the web bundle itself) is off. Never fails the exit code on a merely
#            unconfigured optional feature; it fails only when it cannot form an opinion at
#            all (no Python, most likely).
#
# Every command is safe to run from any working directory; this script cds to the repo root
# first. Nothing here touches git, and nothing here is a substitute for
# `python3 scripts/check_contracts.py`, which is a separate, unrelated guard.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."
ROOT="$(pwd)"
BACKEND_DIR="$ROOT/backend"
WEB_DIR="$ROOT/web"
VENV_DIR="$BACKEND_DIR/.venv"
PORT="${PORT:-8000}"

usage() {
  sed -n '2,27p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
}

# --------------------------------------------------------------------------------- guard rails
#
# "Fail gracefully with a clear message when Node is missing, not a stack trace" is a literal
# requirement of this file (WEB_DESIGN.md's WP0 build order); the same courtesy is extended to
# a missing Python, since `dev` and `doctor` need one just as much as `setup` and `build` need
# Node.

require_node() {
  if ! command -v node >/dev/null 2>&1; then
    cat >&2 <<EOF
web.sh $1: Node.js was not found on PATH.
  Install Node 22 or newer from https://nodejs.org (or via nvm/your package manager),
  then re-run: $0 $1
EOF
    exit 1
  fi
  if ! command -v npm >/dev/null 2>&1; then
    echo "web.sh $1: npm was not found on PATH (an unusual Node install?)." >&2
    echo "  Install npm and re-run: $0 $1" >&2
    exit 1
  fi
}

require_python() {
  if ! command -v python3 >/dev/null 2>&1; then
    cat >&2 <<EOF
web.sh $1: python3 was not found on PATH.
  Install Python 3.11 or newer, then re-run: $0 $1
EOF
    exit 1
  fi
}

# Prints the interpreter this script should run backend Python with: the project virtualenv's
# if `setup` has created one, else the system `python3` (so `doctor` still has something to
# say about a checkout nobody has set up yet), signalling which one it picked on stderr.
resolve_python() {
  if [[ -x "$VENV_DIR/bin/python3" ]]; then
    echo "$VENV_DIR/bin/python3"
  else
    command -v python3
  fi
}

# --------------------------------------------------------------------------------- setup

cmd_setup() {
  require_python setup
  require_node setup

  if [[ ! -d "$VENV_DIR" ]]; then
    echo "web.sh setup: creating a virtualenv at backend/.venv ..."
    python3 -m venv "$VENV_DIR"
  else
    echo "web.sh setup: reusing the existing virtualenv at backend/.venv"
  fi
  local vpy="$VENV_DIR/bin/python3"

  "$vpy" -m pip install --upgrade pip --quiet
  echo "web.sh setup: pip install -e \"backend[serve,web]\" ..."
  "$vpy" -m pip install -e "$BACKEND_DIR[serve,web]"

  if [[ ! -f "$BACKEND_DIR/.env" ]]; then
    if [[ -f "$BACKEND_DIR/.env.example" ]]; then
      cp "$BACKEND_DIR/.env.example" "$BACKEND_DIR/.env"
      echo "web.sh setup: wrote backend/.env from backend/.env.example."
      echo "              Edit it to add Google/Apple credentials, or run '$0 doctor' first."
    else
      echo "web.sh setup: backend/.env.example is missing; skipping the backend/.env copy." >&2
    fi
  else
    echo "web.sh setup: backend/.env already exists, leaving it alone."
  fi

  echo "web.sh setup: npm ci (web/) ..."
  ( cd "$WEB_DIR" && npm ci )

  echo "web.sh setup: npm run build (web/) ..."
  ( cd "$WEB_DIR" && npm run build )

  cat <<EOF
web.sh setup: done.
  Next: python3 -m nbastats.accounts.admin invite --note "me"   (signup mode is invite by default)
        $0 dev                                                  (serve http://127.0.0.1:${PORT})
        $0 doctor                                                (what is/isn't configured, and why)
EOF
}

# --------------------------------------------------------------------------------- dev

cmd_dev() {
  require_python dev
  if [[ ! -x "$VENV_DIR/bin/python3" ]]; then
    cat >&2 <<EOF
web.sh dev: no virtualenv at backend/.venv yet.
  Run '$0 setup' first (it also builds web/dist, which the server needs to have anything to
  serve at '/').
EOF
    exit 1
  fi
  if [[ ! -f "$WEB_DIR/dist/index.html" ]]; then
    cat >&2 <<EOF
web.sh dev: web/dist/index.html does not exist yet.
  The server will still start and answer /v1/* — it just has nothing to serve at '/' until
  you run '$0 build' (or '$0 setup', which builds it too). This is not a crash; see
  nbastats.api.routes_web.mount_web, which logs a warning and skips the SPA mount rather than
  erroring when the bundle is absent.
EOF
  fi

  # Matches backend/scripts/serve_dev.sh's own default: the demo league needs no network, no
  # credentials and no nba_api, and is what makes '/tonight', '/today' and the rest worth
  # opening on a machine with no ingested data. A value already exported by the caller wins.
  export HARDWOOD_DEMO_MODE="${HARDWOOD_DEMO_MODE:-1}"

  echo "web.sh dev: serving http://127.0.0.1:${PORT} (Ctrl-C to stop) ..."
  # cd into backend/ first: DATABASE_URL's default (nbastats/config.py) and backend/.env are
  # both relative to it, same as backend/scripts/serve_dev.sh — running uvicorn from the repo
  # root instead would create hardwood.db one directory up from where every other script and
  # .gitignore rule (backend/hardwood.db) expects to find it.
  cd "$BACKEND_DIR"
  exec "$VENV_DIR/bin/python3" -m uvicorn nbastats.api.app:app --host 127.0.0.1 --port "$PORT"
}

# --------------------------------------------------------------------------------- build

cmd_build() {
  require_node build
  echo "web.sh build: npm run build (web/) ..."
  ( cd "$WEB_DIR" && npm run build )
}

# --------------------------------------------------------------------------------- doctor

cmd_doctor() {
  echo "== Node / npm =="
  if command -v node >/dev/null 2>&1; then
    echo "node:  $(node --version)  ($(command -v node))"
  else
    echo "node:  NOT FOUND — install Node 22+ from https://nodejs.org"
  fi
  if command -v npm >/dev/null 2>&1; then
    echo "npm:   $(npm --version)"
  else
    echo "npm:   NOT FOUND"
  fi

  echo
  echo "== Python =="
  if ! command -v python3 >/dev/null 2>&1; then
    echo "python3: NOT FOUND — install Python 3.11+ and re-run '$0 doctor'."
    exit 1
  fi
  if [[ -x "$VENV_DIR/bin/python3" ]]; then
    echo "venv:    backend/.venv ($("$VENV_DIR/bin/python3" --version 2>&1))"
  else
    echo "venv:    none at backend/.venv yet — using the system interpreter for this report."
    echo "         Run '$0 setup' to create one and install the backend into it."
  fi

  echo
  echo "== web/dist =="
  if [[ -f "$WEB_DIR/dist/index.html" ]]; then
    echo "web/dist/index.html: present"
  else
    echo "web/dist/index.html: MISSING — run '$0 build' (/v1 still works without it)."
  fi

  echo
  echo "== Accounts configuration =="
  local py
  py="$(resolve_python)"
  PYTHONPATH="$BACKEND_DIR${PYTHONPATH:+:$PYTHONPATH}" "$py" - "$BACKEND_DIR" <<'PYEOF'
import sys
from pathlib import Path

backend_dir = Path(sys.argv[1])

try:
    from nbastats.accounts import config as auth_config
except ImportError as exc:
    print(f"nbastats.accounts.config is not importable yet ({exc}).")
    print(f"Run 'pip install -e \"{backend_dir}[serve,web]\"' (or '{Path(__file__).name} setup').")
    raise SystemExit(0)

env_path = backend_dir / ".env"
if env_path.is_file():
    auth_config.load_dotenv(env_path)
    print(f"backend/.env: loaded ({env_path})")
else:
    print("backend/.env: absent — every setting below is a built-in default.")

settings = auth_config.AuthSettings.from_env()
print(f"\n{settings!r}")

refusals = auth_config.startup_refusals(settings)
warnings = auth_config.startup_warnings(settings)
print(f"\nstartup refusals: {len(refusals)} (a non-empty list makes create_app() raise)")
for reason in refusals:
    print(f"  REFUSAL: {reason}")
print(f"startup warnings: {len(warnings)} (surfaced at /v1/health.authWarnings, never fatal)")
for reason in warnings:
    print(f"  warning: {reason}")

print()
try:
    from nbastats.accounts.providers import provider_status
except ImportError:
    print("Google / Apple sign-in: nbastats.accounts.providers is not built yet (WP1).")
else:
    for name, status in provider_status(settings).items():
        state = "enabled" if status.get("enabled") else "disabled"
        reason = status.get("reason")
        suffix = f" — {reason}" if reason else ""
        print(f"{name}: {state}{suffix}")
PYEOF
}

# --------------------------------------------------------------------------------- dispatch

case "${1:-}" in
  setup)  cmd_setup ;;
  dev)    cmd_dev ;;
  build)  cmd_build ;;
  doctor) cmd_doctor ;;
  -h|--help|"") usage ;;
  *)
    echo "web.sh: unknown command '$1' (expected: setup | dev | build | doctor)" >&2
    exit 1
    ;;
esac
