# Supabase 005 breaking false-negative diagnostic

## Frozen Ground Truth

Base `GET /authorize` documents optional query parameter `flow_type`; Head removes it. The frozen review
classifies this as breaking because an existing caller may continue sending a previously documented input.
The affected and executable scope is `GET /authorize`.

## oasdiff evidence

oasdiff did **not** miss the structural change. It emitted `request-parameter-removed` with text
“deleted the `query` request parameter `flow_type`”, path `/authorize`, method `GET`, and level **2**.
Guardian's frozen severity normalizer maps level 2 to `warning`, while deterministic Contract FAIL uses
ERROR (level 3) changes only. The normalized record was retained; normalization did not drop it.

- oasdiff version 1.28.0: exit=0, changes=[{"id": "request-parameter-removed", "text": "deleted the `query` request parameter `flow_type`", "comment": "This is a warning because some clients may return an error when receiving an unexpected parameter. It is recommended to deprecate the parameter first.", "level": 2, "operation": "GET", "path": "/authorize", "section": "paths", "fingerprint": "8edf15cee5dc"}]
- oasdiff version 1.27.0: exit=0, changes=[{"id": "request-parameter-removed", "text": "deleted the `query` request parameter `flow_type`", "comment": "This is a warning because some clients may return an error when receiving an unexpected parameter. It is recommended to deprecate the parameter first.", "level": 2, "operation": "GET", "path": "/authorize", "section": "paths", "fingerprint": "8edf15cee5dc"}]

## Root cause

This is a **policy-definition disagreement with Ground Truth**, expressed through oasdiff's rule severity:
the tool reports optional request-parameter removal as WARNING rather than ERROR. Both tested versions
behave the same, and no newer stable release than 1.28.0 was available for an upgrade replay.

That does not invalidate either convention. It means E3-B's compatibility policy treats removal of a
documented optional input as gate-breaking, while frozen oasdiff `--fail-on ERR` does not. E3-B remains
PASS for the system and FAIL for Ground Truth; no headline metric was rewritten.

## Evidence identity

- Base: `19545601676675a923bb9850499717e89cf91a0b`
- Head: `f4c3cdb020e6808333ec460470f9e081f0d9bc93`
- Frozen Ground Truth verdict: `fail`
