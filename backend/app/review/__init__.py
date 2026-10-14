"""Phase 3: AI review orchestration, finding validation and publication.

The layering is deliberate and one-directional::

    context.py    analysis  -> the bounded document a model may see
    validator.py  RawFinding -> ValidatedFinding, or nothing
    repository.py persistence of runs, findings and comments
    publisher.py  ValidatedFinding -> GitHub inline review comments
    service.py    the orchestrator that runs the above in order

``publisher`` imports ``validator``'s output type and never ``RawFinding``.
That is enforced by a test, because it is the invariant the whole phase rests
on: an unverified model claim has no path to GitHub.
"""
