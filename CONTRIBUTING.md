# Contributing

This is an Exbound implementation repository. Start with
[`AGENTS.md`](AGENTS.md) (the clean-room wall, the coverage triplet, and the
verification discipline) and the organization
[contribution guide](https://github.com/exbound/.github/blob/main/CONTRIBUTING.md).

## What's useful here

Small, independently verifiable units — not large speculative write-ups:

- An implemented feature from `docs/engineering-spec.md`, with the fixture and
  the reference result that show it is compatible.
- A **failing fixture** for a native file or operation this repo does not yet
  handle — a valid contribution on its own.
- A reference computation showing a numerical discrepancy against this
  implementation, with the exact reference-software version.
- A correction to the engineering spec, with a reproducible check.

## Ground rules

- Keep the **clean-room wall**: no vendor code, symbol names, binary-internals
  detail, artwork, or trademarks reach this repo (see `AGENTS.md`). Describe
  behavior and format, never how it was recovered.
- **Lawfully held inputs only.** Do not commit vendor binaries or
  vendor-copyrighted samples; see [`tests/fixtures/README.md`](tests/fixtures/README.md).
- Every claim carries the command, file, or reference version that produced it.
- `just check && just test` before you push.

## Security

Report vulnerabilities through the organization's private vulnerability
reporting, not a public issue. See
[SECURITY](https://github.com/exbound/.github/blob/main/SECURITY.md).
