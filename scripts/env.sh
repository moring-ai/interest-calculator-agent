#!/usr/bin/env sh
# Source this before running `agentcore deploy` or any uv-driven build:
#
#     source scripts/env.sh
#
# Why this exists
# ---------------
# This machine runs a Lumeus ZTNA agent that terminates and re-signs TLS. Tools
# that use the OS trust store (npm, brew, curl) work because the agent's root is
# installed there. `uv` is built on rustls and ships its OWN root store, so it
# rejects the re-signed certificate with "invalid peer certificate:
# UnknownIssuer" and every AgentCore CDK synth fails while resolving Python
# dependencies.
#
# The fix is to hand uv a bundle containing both the public roots (certifi) and
# the Lumeus root, via SSL_CERT_FILE. The bundle is generated locally and is
# gitignored, because it is specific to this machine's security agent.
#
# Deliberately POSIX-ish: this gets sourced from zsh (the default macOS shell)
# as often as from bash, so it must not rely on BASH_SOURCE, and it must not
# `set -e`/`set -u`, which would leak into the caller's interactive shell.

_rr_repo_root() {
    # git is the reliable anchor; fall back to the current directory.
    git rev-parse --show-toplevel 2>/dev/null || pwd
}

_rr_setup() {
    REPO_ROOT="$(_rr_repo_root)"
    BUNDLE="$REPO_ROOT/.certs/combined-ca.pem"
    LUMEUS_CERT="$HOME/Library/Application Support/application.ai.lumeus.v1/Certs/lumeusCert.pem"

    if [ ! -f "$LUMEUS_CERT" ]; then
        echo "No Lumeus certificate found; assuming a normal network. Nothing to do."
        return 0
    fi

    CERTIFI="$(python3 -c 'import certifi; print(certifi.where())' 2>/dev/null)"
    if [ -z "$CERTIFI" ] || [ ! -f "$CERTIFI" ]; then
        echo "Could not locate certifi's CA bundle. Run: python3 -m pip install certifi" >&2
        return 1
    fi

    mkdir -p "$REPO_ROOT/.certs" || return 1
    cat "$CERTIFI" "$LUMEUS_CERT" > "$BUNDLE" || return 1

    SSL_CERT_FILE="$BUNDLE";      export SSL_CERT_FILE
    REQUESTS_CA_BUNDLE="$BUNDLE"; export REQUESTS_CA_BUNDLE
    NODE_EXTRA_CA_CERTS="$LUMEUS_CERT"; export NODE_EXTRA_CA_CERTS
    UV_NATIVE_TLS=1;              export UV_NATIVE_TLS

    echo "TLS trust configured: $(grep -c 'BEGIN CERTIFICATE' "$BUNDLE") roots -> $BUNDLE"
}

_rr_setup
