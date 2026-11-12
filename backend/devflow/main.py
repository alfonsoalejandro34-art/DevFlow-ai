"""ASGI entry point: ``uvicorn devflow.main:app``."""

from devflow.app import create_app

app = create_app()
