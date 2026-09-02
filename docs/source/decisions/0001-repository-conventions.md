# 0001 — Repository conventions and deviations from the technical plan

- **Status:** Accepted
- **Scope:** Issues 1–4 (repository bootstrap, CI, configuration, Sleeper client)

## Context

The technical plan specifies the repository layout and toolchain in §B.1–§B.3.
Four decisions taken during implementation depart from the letter of the plan or
resolve a question the plan left open. They are recorded here rather than in
pull request comments so the reasoning survives.

## Decisions

### 1. Build backend is `uv_build`, not `hatchling`

§B.1 and §B.3 inherit `hatchling` from the `ditto` template. The repository uses
`uv_build` instead.

The project already mandates uv as the only supported environment and dependency
workflow, so using uv's own backend removes a second build tool from the chain.
For a pure-Python src-layout package the two are equivalent in output, and
`uv_build` needs no `[tool.hatch.build.targets.wheel]` block because
`src/<package>` is its default layout. `[tool.uv.build-backend]` states the
module name and root explicitly anyway, so the layout is not implicit.

The constraint is pinned as `uv_build>=0.12.3,<0.13`. `uv_build` supports pure
Python only; if compiled extensions are ever needed, this decision must be
revisited.

### 2. Docstrings are Google style

§B.1 and §B.4 call for Google-style docstrings. The `ditto` template shipped
`docs/source/conf.py` with `napoleon_google_docstring = False` and
`napoleon_numpy_docstring = True`, which contradicts that.

The plan wins. `conf.py` is set to Google style and the template's one remaining
NumPy-style docstring was converted, so the repository is internally consistent.

### 3. HTTP is `requests`, and the CLI is `argparse`

§B.3 lists `requests` as a runtime dependency and §B.6 specifies `responses` for
test mocking, which is `requests`-specific. An earlier scaffold used `httpx`;
that has been removed. `nflreadpy` already depends on `requests`, so this adds
no new transitive weight.

§B.3 lists no CLI library, so the CLI is built on `argparse` from the standard
library. The command surface sketched in §B.9 is two levels deep, which
subparsers handle without a third-party dependency.

### 4. The league's draft settings are resolved, not just flagged

§ Caveats records that the draft object could not be fetched, leaving
`settings.draft_rounds = 3` unexplained for a 15-slot redraft roster.

`nuclearff sleeper fetch-league` retrieves it. The draft object reports
`rounds: 15`, `type: snake`, `pick_timer: 300`, and `reversal_round: 3` — a
third-round-reversal snake draft. The league object's `draft_rounds = 3` is
stale and must not be used for pick-gap or VONA math.

`detect_anomalies` encodes this: when both objects are present and disagree, it
emits a `draft_rounds_mismatch` warning naming the draft object as
authoritative. `settings.max_keepers = 1` alongside `settings.type = 0` remains
flagged as `keepers_in_redraft`, since only the league UI can confirm whether a
keeper is actually in play.

## Consequences

- Anyone reading §B.3 and the committed `pyproject.toml` will find the build
  backend differs; this note is the explanation.
- New modules must use Google-style docstrings. A NumPy-style docstring will
  render incorrectly rather than fail loudly, so it is worth watching in review.
- The 15-round figure should be re-confirmed if the commissioner changes league
  settings before the draft.
