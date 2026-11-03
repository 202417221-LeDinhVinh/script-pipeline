"""Web-novel scraper (SPEC §15). No dependency on the LLM code.

Only stdlib + httpx + PyYAML + vsp.io are imported anywhere in this package.
`--ingest` is handled by the CLI layer (which calls the existing pipeline),
never from inside this package.
"""
