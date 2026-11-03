"""Seed historical policy cases without exposing a client-selectable legacy policy.

These private repository entrypoints are for migration/reconciliation fixtures.
HTTP requests always use the public current-policy entrypoints.
"""
from app.repositories import product_returns, replacement_workflow


def create_legacy_return(*args, **kwargs):
    return product_returns._create_product_return(*args, **kwargs, policy_version=1)


def legacy_preview(order_id, actor_id, lines, replacements):
    return replacement_workflow._preview(order_id, actor_id, lines, replacements, policy_version=1)
