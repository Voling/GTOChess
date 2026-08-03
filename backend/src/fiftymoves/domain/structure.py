from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class PawnShape(BaseModel):
    model_config = ConfigDict(frozen=True)

    isolated: tuple[str, ...] = ()
    doubled: tuple[str, ...] = ()
    backward: tuple[str, ...] = ()
    passed: tuple[str, ...] = ()
    islands: int = 0
    weak_squares: tuple[str, ...] = Field(
        default=(),
        description="Squares in this side's own half no pawn of theirs can ever attack",
    )
    outposts: tuple[str, ...] = Field(
        default=(),
        description="Squares in enemy territory this side holds with a pawn behind them",
    )

    @property
    def empty(self) -> bool:
        return not (self.isolated or self.doubled or self.backward or self.passed)


class Structure(BaseModel):
    model_config = ConfigDict(frozen=True)

    white: PawnShape
    black: PawnShape
    open_files: tuple[str, ...] = ()
    half_open_white: tuple[str, ...] = Field(
        default=(), description="Files with no white pawn, so White's rooks bear down them"
    )
    half_open_black: tuple[str, ...] = ()
