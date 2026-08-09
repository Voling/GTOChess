from __future__ import annotations

from typing import Any

import chess

from gtochess.domain.explanations import Evidence, EvidenceKind
from gtochess.domain.models import MAX_LOSS_CP, EngineReport
from gtochess.domain.storyboard import Arrow, ArrowRole, Beat, Glyph, Scene, Storyboard
from gtochess.engine.protocol import EngineProvider
from gtochess.llm.facts import pawns
from gtochess.llm.tools import ProbeLimit

WALK_LINE = "walk_line"
BEST_REPLIES = "best_replies"
SHOW_LINE = "show_line"

GLYPH_BANDS: tuple[tuple[int, Glyph], ...] = (
    (300, Glyph.BLUNDER),
    (150, Glyph.MISTAKE),
    (50, Glyph.DUBIOUS),
)
EDITORIAL = (Glyph.STRONG, Glyph.BRILLIANT, Glyph.INTERESTING)

TOOLS: list[dict[str, Any]] = [
    {
        "name": WALK_LINE,
        "description": (
            "Play a line out on the board and get the engine's verdict on every move in "
            "it, including how much each move gave up against the best available. Walk a "
            "line before saying anything about it. Returns a line_id you can show later."
        ),
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "moves_san": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Moves in SAN from the position under discussion.",
                }
            },
            "required": ["moves_san"],
            "additionalProperties": False,
        },
    },
    {
        "name": BEST_REPLIES,
        "description": (
            "List the engine's preferred moves in the position reached after an optional "
            "line, with their evaluations. Use this to find what the opponent gets to do."
        ),
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "moves_san": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Moves in SAN leading to the position. Empty for this one.",
                },
                "count": {"type": "integer", "description": "How many moves to return, 1 to 5."},
            },
            "required": ["moves_san", "count"],
            "additionalProperties": False,
        },
    },
    {
        "name": SHOW_LINE,
        "description": (
            "Put a line you have already walked on the board for the reader, as a scene "
            "they can step through. Give one note per move, in the same order. Draw an "
            "arrow when a square or a route is the point of the move. Question marks are "
            "assigned from the engine's numbers and cannot be set here; !, !? and !! can."
        ),
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "line_id": {"type": "string", "description": "From a walk_line call."},
                "title": {"type": "string", "description": "What this scene shows, a few words."},
                "notes": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "One line of commentary per move. Empty string to stay silent.",
                },
                "arrows": {
                    "type": "array",
                    "description": "Arrows to draw, each tied to a move by its index.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "move_index": {"type": "integer"},
                            "origin": {"type": "string"},
                            "target": {"type": "string"},
                            "role": {"type": "string", "enum": ["idea", "threat"]},
                        },
                        "required": ["move_index", "origin", "target", "role"],
                        "additionalProperties": False,
                    },
                },
                "praise": {
                    "type": "array",
                    "description": "Moves worth a !, !? or !!, by index.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "move_index": {"type": "integer"},
                            "glyph": {"type": "string", "enum": ["!", "!?", "!!"]},
                        },
                        "required": ["move_index", "glyph"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["line_id", "title", "notes", "arrows", "praise"],
            "additionalProperties": False,
        },
    },
]


class BoardLimit(ProbeLimit):
    pass


def glyph_for(loss_cp: int) -> Glyph:
    for threshold, glyph in GLYPH_BANDS:
        if loss_cp >= threshold:
            return glyph
    return Glyph.PLAIN


class WalkedMove:
    def __init__(
        self,
        *,
        uci: str,
        san: str,
        epd: str,
        score_cp: int,
        loss_cp: int,
        best_san: str,
    ) -> None:
        self.uci = uci
        self.san = san
        self.epd = epd
        self.score_cp = score_cp
        self.loss_cp = loss_cp
        self.best_san = best_san
        self.glyph = glyph_for(loss_cp)


class WalkedLine:
    def __init__(self, identifier: str, root_epd: str, moves: list[WalkedMove], opening_cp: int):
        self.id = identifier
        self.root_epd = root_epd
        self.moves = moves
        self.opening_cp = opening_cp


class BoardSession:
    def __init__(
        self,
        engine: EngineProvider,
        board: chess.Board,
        *,
        depth: int = 14,
        max_moves: int = 8,
        max_calls: int = 8,
    ) -> None:
        self._engine = engine
        self._board = board
        self._depth = depth
        self._max_moves = max_moves
        self._max_calls = max_calls
        self._calls = 0
        self._lines: dict[str, WalkedLine] = {}
        self._scenes: list[Scene] = []
        self._root: EngineReport | None = None
        self.evidence: list[Evidence] = []

    @property
    def calls(self) -> int:
        return self._calls

    @property
    def scenes(self) -> int:
        return len(self._scenes)

    @property
    def schema(self) -> list[dict[str, Any]]:
        return TOOLS

    def _spend(self) -> None:
        if self._calls >= self._max_calls:
            raise BoardLimit(f"the engine has already answered {self._max_calls} times")
        self._calls += 1

    def _record(self, statement: str, kind: EvidenceKind = EvidenceKind.ENGINE_LINE) -> str:
        identifier = f"probe{len(self.evidence) + 1}"
        self.evidence.append(Evidence(id=identifier, kind=kind, statement=statement))
        return identifier

    def _mover_cp(self, score_cp: int, board: chess.Board) -> int:
        return score_cp if board.turn == chess.WHITE else -score_cp

    def _parse(self, probe: chess.Board, san: str, played: list[str]) -> chess.Move:
        try:
            return probe.parse_san(san)
        except (chess.IllegalMoveError, chess.InvalidMoveError, chess.AmbiguousMoveError):
            raise BoardLimit(
                f"{san} is not legal after {' '.join(played) or 'the start'}"
            ) from None

    def _walk(self, moves_san: list[str]) -> tuple[chess.Board, str]:
        if len(moves_san) > self._max_moves:
            raise BoardLimit(f"at most {self._max_moves} moves per call")
        probe = self._board.copy(stack=False)
        played: list[str] = []
        for san in moves_san:
            move = self._parse(probe, san, played)
            played.append(san)
            probe.push(move)
        return probe, " ".join(played)

    def walk_line(self, moves_san: list[str]) -> dict[str, Any]:
        self._spend()
        if not moves_san:
            raise BoardLimit("walk_line needs at least one move")
        if len(moves_san) > self._max_moves:
            raise BoardLimit(f"at most {self._max_moves} moves per call")

        probe = self._board.copy(stack=False)
        if self._root is None:
            self._root = self._engine.analyse(probe, depth=self._depth, multipv=1)
        before = self._root
        opening_cp = before.score_cp
        moves: list[WalkedMove] = []
        played: list[str] = []

        for san in moves_san:
            if probe.is_game_over(claim_draw=False):
                break
            mover_best = self._mover_cp(before.score_cp, probe)
            best_san = before.best.move_san
            move = self._parse(probe, san, played)
            san_text = probe.san(move)
            played.append(san_text)
            probe.push(move)
            after = self._engine.analyse(probe, depth=self._depth, multipv=1)
            mover_after = -self._mover_cp(after.score_cp, probe)
            moves.append(
                WalkedMove(
                    uci=move.uci(),
                    san=san_text,
                    epd=probe.epd(),
                    score_cp=after.score_cp,
                    loss_cp=min(MAX_LOSS_CP, max(0, mover_best - mover_after)),
                    best_san=best_san,
                )
            )
            before = after

        if not moves:
            raise BoardLimit("the game is already over in this position")

        identifier = f"line{len(self._lines) + 1}"
        self._lines[identifier] = WalkedLine(identifier, self._board.epd(), moves, opening_cp)

        faults = [f"{m.san}{m.glyph}" for m in moves if m.glyph is not Glyph.PLAIN]
        tail = f" The engine marks {', '.join(faults)}." if faults else ""
        statement = (
            f"Walking {' '.join(played)} takes the evaluation from {pawns(opening_cp)} to "
            f"{pawns(moves[-1].score_cp)} for White at depth {self._depth}.{tail}"
        )
        return {
            "line_id": identifier,
            "evidence_id": self._record(statement),
            "summary": statement,
            "moves": [
                {
                    "san": m.san,
                    "score_cp_white": m.score_cp,
                    "gave_up_cp": m.loss_cp,
                    "engine_preferred": m.best_san,
                    "glyph": str(m.glyph),
                }
                for m in moves
            ],
        }

    def best_replies(self, moves_san: list[str], count: int) -> dict[str, Any]:
        self._spend()
        bounded = max(1, min(int(count), 5))
        probe, line = self._walk(moves_san)
        if probe.is_game_over(claim_draw=False):
            statement = f"After {line} there are no moves: {probe.result()}."
            return {"evidence_id": self._record(statement), "summary": statement}

        report = self._engine.analyse(probe, depth=self._depth, multipv=bounded)
        mover = "White" if probe.turn == chess.WHITE else "Black"
        parts = [
            f"{item.move_san} ({pawns(self._mover_cp(item.score_cp, probe))})"
            for item in report.lines
        ]
        where = f"after {line}" if line else "here"
        statement = (
            f"The engine's choices for {mover} {where} at depth {self._depth}: {', '.join(parts)}."
        )
        return {
            "evidence_id": self._record(statement),
            "summary": statement,
            "moves": [
                {"san": item.move_san, "score_cp_for_mover": self._mover_cp(item.score_cp, probe)}
                for item in report.lines
            ],
        }

    def show_line(self, payload: dict[str, Any]) -> dict[str, Any]:
        identifier = str(payload.get("line_id") or "")
        walked = self._lines.get(identifier)
        if walked is None:
            raise BoardLimit(f"no line called {identifier!r}, walk it first")

        notes = [str(n) for n in payload.get("notes") or []]
        arrows_by_move: dict[int, list[Arrow]] = {}
        for entry in payload.get("arrows") or []:
            origin = str(entry.get("origin", "")).lower()
            target = str(entry.get("target", "")).lower()
            if origin not in chess.SQUARE_NAMES or target not in chess.SQUARE_NAMES:
                continue
            role = ArrowRole.THREAT if entry.get("role") == "threat" else ArrowRole.IDEA
            index = int(entry.get("move_index", -1))
            arrows_by_move.setdefault(index, []).append(
                Arrow(origin=origin, target=target, role=role)
            )

        praise: dict[int, Glyph] = {}
        for entry in payload.get("praise") or []:
            try:
                glyph = Glyph(str(entry.get("glyph", "")))
            except ValueError:
                continue
            if glyph in EDITORIAL:
                praise[int(entry.get("move_index", -1))] = glyph

        beats = [Beat(index=0, epd=walked.root_epd, score_cp=walked.opening_cp)]
        for position, move in enumerate(walked.moves):
            arrows = [Arrow(origin=move.uci[:2], target=move.uci[2:4], role=ArrowRole.PLAYED)]
            drawn = {(move.uci[:2], move.uci[2:4])}
            for extra in arrows_by_move.get(position, ()):
                if (extra.origin, extra.target) in drawn:
                    continue
                drawn.add((extra.origin, extra.target))
                arrows.append(extra)
            glyph = move.glyph
            if glyph is Glyph.PLAIN and position in praise:
                glyph = praise[position]
            beats.append(
                Beat(
                    index=position + 1,
                    epd=move.epd,
                    move_uci=move.uci,
                    move_san=move.san,
                    glyph=glyph,
                    arrows=tuple(arrows),
                    highlights=(move.uci[2:4],),
                    note=notes[position] if position < len(notes) else "",
                    score_cp=move.score_cp,
                    evidence_id=identifier,
                )
            )

        scene = Scene(title=str(payload.get("title") or "A line"), beats=tuple(beats))
        self._scenes.append(scene)
        return {
            "shown": scene.title,
            "beats": len(scene.beats),
            "summary": (f"{scene.title} is on the board, {scene.moves} moves to step through."),
        }

    @property
    def unshown(self) -> list[str]:
        shown = {beat.evidence_id for scene in self._scenes for beat in scene.beats}
        return [key for key in self._lines if key not in shown]

    def show_longest_walk(self) -> bool:
        pending = self.unshown
        if not pending:
            return False
        best = max(pending, key=lambda key: len(self._lines[key].moves))
        walked = self._lines[best]
        self.show_line(
            {
                "line_id": best,
                "title": " ".join(m.san for m in walked.moves),
                "notes": [],
            }
        )
        return True

    def storyboard(self) -> Storyboard:
        return Storyboard(
            root_epd=self._board.epd(),
            orientation="white" if self._board.turn == chess.WHITE else "black",
            scenes=tuple(self._scenes),
        )

    def dispatch(self, name: str, payload: dict[str, Any]) -> dict[str, Any]:
        if name == WALK_LINE:
            return self.walk_line([str(m) for m in payload.get("moves_san") or []])
        if name == BEST_REPLIES:
            return self.best_replies(
                [str(m) for m in payload.get("moves_san") or []], int(payload.get("count", 3))
            )
        if name == SHOW_LINE:
            return self.show_line(payload)
        raise BoardLimit(f"no such tool: {name}")
