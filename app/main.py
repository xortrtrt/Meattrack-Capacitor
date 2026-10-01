"""ASGI entrypoint for MEATTRACK."""

from app.application import create_app


app = create_app()

__all__ = ["app"]

