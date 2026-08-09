"""Engine abstraction.

Two consumers, deliberately separated:

* the enrichment pipeline, which calls :meth:`analyse` on its own schedule; and
* the LLM, which may only reach the engine through the bounded probe tools in
  ``gtochess.llm.tools``. The model never calculates -- it asks, and the
  engine's answer becomes citable evidence like any other.
"""

from __future__ import annotations

from typing import Protocol

import chess

from gtochess.domain.models import EngineReport


class EngineProvider(Protocol):
    """Anything that can evaluate a position.

    Implementations must handle ``board.chess960`` correctly: castling encoding
    differs, and a provider that ignores it will silently mis-evaluate 960.
    """

    @property
    def engine_id(self) -> str: ...

    def analyse(self, board: chess.Board, *, depth: int, multipv: int = 3) -> EngineReport:
        """Multi-PV search. ``lines`` comes back ranked, best first."""
        ...

    def close(self) -> None: ...


class EngineError(RuntimeError):
    pass
