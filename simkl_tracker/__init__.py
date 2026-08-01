"""Simkl tracker plugin for Warp MediaCenter.

Talks to api.simkl.com directly through the host's scoped services
(``context["http"]``, ``context["secrets"]``, ``context["cache"]``, ``context["log"]``)
and nothing else. This package must never import ``warp_mediacenter`` — the plugin
receives a normalised, JSON-only ``context``/``payload`` on every call and has no
business reaching into host internals.
"""
