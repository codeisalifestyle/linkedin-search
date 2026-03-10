"""Progress callback interfaces and defaults."""

from __future__ import annotations

from typing import Protocol


class ProgressCallback(Protocol):
    """Callback interface for progress updates."""

    def on_start(self, operation: str) -> None:
        ...

    def on_progress(self, message: str, percent: int | None = None) -> None:
        ...

    def on_complete(self, message: str) -> None:
        ...

    def on_error(self, message: str) -> None:
        ...


class ConsoleCallback:
    """Console logger callback."""

    def on_start(self, operation: str) -> None:
        print(f"[start] {operation}")

    def on_progress(self, message: str, percent: int | None = None) -> None:
        if percent is None:
            print(f"[progress] {message}")
            return
        print(f"[progress:{percent:>3}%] {message}")

    def on_complete(self, message: str) -> None:
        print(f"[done] {message}")

    def on_error(self, message: str) -> None:
        print(f"[error] {message}")


class NullCallback:
    """No-op callback."""

    def on_start(self, operation: str) -> None:
        return

    def on_progress(self, message: str, percent: int | None = None) -> None:
        return

    def on_complete(self, message: str) -> None:
        return

    def on_error(self, message: str) -> None:
        return

