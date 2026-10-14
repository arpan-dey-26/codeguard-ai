"""Deterministic analysis of a synchronised pull request.

    diff/       unified-diff parsing and the LineMap (ground truth)
    source/     head-SHA source retrieval, line indexing, classification
    languages/  language detection and per-language declaration extraction
    mapping/    changed lines -> AST declarations

Two rules hold across the whole package:

*   **Nothing from a repository is executed.** Files are parsed, never
    imported, evaluated, built, installed or passed to a shell.
*   **Output is deterministic.** The same head SHA, source and configuration
    produce byte-identical artifacts. No random ids, no wall-clock values, no
    unordered iteration in serialised output.
"""
