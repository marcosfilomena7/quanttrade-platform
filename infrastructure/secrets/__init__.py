"""Secrets management skeleton.

ARCHITECTURE.md §3.6: "Keys in HashiCorp Vault or cloud KMS. Never in
.env, never in git, never in an environment variable in prod. Fetched at
runtime, held in memory, zeroized on shutdown."

This is the one place in the codebase permitted to read `os.environ` for
a credential — `EnvSecretsClient` (local development only, never prod).
Every other module must go through the `SecretsClient` abstraction; this
is enforced by `scripts/check_no_env_secrets.py`, wired into `make lint`.
"""
