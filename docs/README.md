# Developer documentation

Onboarding docs for new developers picking up this codebase. For the external-facing project description, see the repo-root [README.md](../README.md).

Read in this order:

1. **[GETTING_STARTED.md](GETTING_STARTED.md)** — why [build_reuse_review.py](../build_reuse_review.py) fails on a fresh clone, what to run first, and the fast path to a working review dashboard.
2. **[OVERVIEW.md](OVERVIEW.md)** — conceptual picture: the two discovery channels, the archive adapter layer, and the directory map.
3. **[PIPELINE.md](PIPELINE.md)** — step-by-step reference for [run_pipeline.py](../run_pipeline.py), with the JSON files that pass between stages.
4. **[SCRIPTS.md](SCRIPTS.md)** — one-liner reference for every script in the repo, grouped by role.

See also the pre-existing [paper_fetching_flow.md](../paper_fetching_flow.md) (fetch_paper.py fallback chain) and [PLAN.md](../PLAN.md) (original design note for the combined-dashboard merge).
