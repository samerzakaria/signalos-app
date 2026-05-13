# SignalOS Core v1.1 — CLI sub-commands.
# Concept adapted from a5c-ai/babysitter (MIT). No source code copied.
#
# Each module here is a thin argparse wrapper that delegates to the
# business-logic modules in the parent package (session.py, pause.py,
# harness.py, context.py, registry.py). Keeping the argparse glue
# separate from the logic lets us unit-test logic directly.

__all__: list[str] = []  # W-2
