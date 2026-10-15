# Contributing to Evidence Link Audit

Start with a concrete stale-evidence case and a small synthetic source tree. Keep fixtures free of private source, credentials, and personal paths.

- Add a regression test for observable behavior before changing range/hash semantics, path handling, or CLI statuses.
- Run `python -m unittest discover -v`.
- Install the package in a virtual environment and run `python examples/reproduce_stale.py` to exercise the console entry point.
- Keep the README contract and example artifacts in sync with public behavior.

Prefer the Python standard library. Discuss new dependencies, automatic citation parsing, or new source encodings before implementation. The analyzer should continue to avoid modifying source files, running project code, or accessing the network.
