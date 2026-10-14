"""The durable queue plane.

    backoff.py       retry policy — pure, injectable RNG
    queue.py         the JobQueue facade over the repository layer
    runner.py        the worker claim loop and dispatcher
    handlers/        one module per job type

PostgreSQL is the queue (architecture.md §5.2). No Redis, no Celery, no
broker — and, decisively, no second system, because that is what allows a job
to be enqueued in the same transaction as the webhook that caused it.
"""
