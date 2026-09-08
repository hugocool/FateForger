"""Test doubles shared by more than one test module.

A double used from two places lives here rather than in whichever test file
happened to define it first. Importing a double out of a *test* module makes
the importer's collection depend on the exporter having been collected, which
under pytest's importlib mode is not guaranteed -- and it hides that two suites
are asserting against the same fake.
"""
