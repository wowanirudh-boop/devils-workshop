"""The app as the UI tests run it (no window, no network)."""

from nicegui import ui

from devils_workshop.ui.app import register_pages

register_pages()
ui.run(storage_secret="tests")
