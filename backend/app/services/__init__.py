"""Use-case orchestration.

Services coordinate repositories and integrations. They contain no SQL (that
is the repository layer) and no HTTP handling (that is the API layer), which
keeps them testable without a web framework or a live database.
"""
