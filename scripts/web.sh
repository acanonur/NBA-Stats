#!/usr/bin/env bash
#
# web.sh — the one entry point for building and running Hardwood Web (docs/WEB.md §2).
#
# Single-origin is the whole point of this project's design (docs/WEB.md §1): the browser
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
# requirement of this file (docs/WEB.md §12, "The first hour"); the same courtesy is extended to
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
  Next: (cd "$BACKEND_DIR" && .venv/bin/python3 -m nbastats.accounts.admin invite --note "me")
              mint yourself an invite code — signup mode is 'invite' by default, so this is
              the one thing standing between you and an account. Run it from backend/ with
              the venv's interpreter, as printed: the database lives at backend/hardwood.db.
        $0 dev      serve http://127.0.0.1:${PORT}
        $0 doctor   what is/isn't configured, and why
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

  # Same pattern, for the setting that silently breaks every POST when it disagrees with the
  # port we are about to bind. HARDWOOD_PUBLIC_BASE_URL is the sole source for the CSRF Origin
  # check, and it defaults to http://127.0.0.1:8000 — so `PORT=8123 web.sh dev` used to serve a
  # site that loaded, rendered and read perfectly while sign in, sign up and forgot-password
  # all failed with "This request could not be verified. Reload the page and try again.",
  # which reloading never fixed. An exported value from the caller still wins.
  export HARDWOOD_PUBLIC_BASE_URL="${HARDWOOD_PUBLIC_BASE_URL:-http://127.0.0.1:${PORT}}"

  echo "web.sh dev: serving ${HARDWOOD_PUBLIC_BASE_URL} (Ctrl-C to stop) ..."
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
  PYTHONPATH="$BACKEND_DIR${PYTHONPATH:+:$PYTHONPATH}" "$py" - "$BACKEND_DIR" "$0" "$PORT" <<'PYEOF'
import sys
from pathlib import Path

backend_dir = Path(sys.argv[1])
# `python3 -` sets __file__ to the literal string "<stdin>", so printing Path(__file__).name
# told an un-set-up reader to run "'<stdin>' setup". The script's own name is passed in.
script_name = sys.argv[2]
port = sys.argv[3]

try:
    from nbastats.accounts import config as auth_config
except ImportError as exc:
    print(f"nbastats.accounts.config is not importable yet ({exc}).")
    print(f"Run '{script_name} setup' (it creates backend/.venv and installs the backend),")
    print(f"or install it yourself: pip install -e \"{backend_dir}[serve,web]\"")
    raise SystemExit(0)

# The same call `api/app.py::create_app` makes, so doctor and the running server can never
# disagree about what backend/.env contains.
loaded = auth_config.load_env_file(backend_dir / ".env")
if loaded is not None:
    print(f"backend/.env: loaded ({loaded}) — the server reads this same file.")
else:
    print("backend/.env: absent — every setting below is a built-in default.")

try:
    settings = auth_config.AuthSettings.from_env()
except ValueError as exc:
    # A typo in one of the numeric or boolean settings. doctor is the tool you run *because*
    # something is wrong; aborting mid-report with a Python traceback is the one thing it must
    # never do (see this script's header).
    print()
    print(f"configuration error: {exc}")
    print(f"Fix that line in {backend_dir / '.env'} (or unset it in your shell) and re-run")
    print(f"'{script_name} doctor'. Nothing below could be checked until it parses.")
    raise SystemExit(0)
print(f"\n{settings!r}")

configured_port = None
try:
    from urllib.parse import urlsplit

    configured_port = urlsplit(settings.public_base_url).port or (
        443 if settings.public_base_url.startswith("https://") else 80
    )
except ValueError:
    configured_port = None
if configured_port is not None and str(configured_port) != str(port):
    print(
        f"\nNOTE: HARDWOOD_PUBLIC_BASE_URL names port {configured_port}, but PORT={port}. "
        f"'{script_name} dev' exports a matching base URL for you; anything else that binds "
        f"port {port} will fail every sign-in with 'csrf_failed'."
    )

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
