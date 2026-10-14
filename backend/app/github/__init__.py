"""GitHub integration — the acquisition plane.

    signature.py   HMAC verification of webhook deliveries (stdlib only)
    events.py      routing: which deliveries become work
    payloads.py    narrow models for webhook payloads
    app_auth.py    private key -> App JWT -> installation token (+ cache)
    client.py      REST operations: pagination, retries, rate limits
    models.py      typed models for REST responses

Authentication and API operations are separate modules on purpose: they have
different secrets and different lifetimes, and keeping them apart means the
client's test suite needs no cryptographic material.
"""
