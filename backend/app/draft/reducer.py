from __future__ import annotations

from dataclasses import dataclass

from .models import DraftEvent


@dataclass(frozen=True)
class ReducedPick:
    event: DraftEvent
    overall_pick: int
    round: int
    team_slot: int
    player_id: int


def snake_team_slot(overall_pick: int, team_count: int) -> int:
    if overall_pick < 1 or team_count < 1:
        raise ValueError("overall_pick and team_count must be positive")
    round_number = (overall_pick - 1) // team_count + 1
    within_round = (overall_pick - 1) % team_count
    return within_round + 1 if round_number % 2 else team_count - within_round


def snake_round(overall_pick: int, team_count: int) -> int:
    return (overall_pick - 1) // team_count + 1


def reduce_picks(events: list[DraftEvent]) -> list[ReducedPick]:
    event_overalls: dict[int, int] = {}
    active: dict[int, DraftEvent] = {}
    for event in sorted(events, key=lambda item: item.sequence):
        if event.overall_pick is not None:
            event_overalls[event.id] = event.overall_pick
        if event.type == "pick_recorded" and event.overall_pick is not None:
            active[event.overall_pick] = event
        elif event.type == "pick_reversed" and event.supersedes_event_id is not None:
            overall = event_overalls.get(event.supersedes_event_id, event.overall_pick)
            if overall is not None:
                active.pop(overall, None)
        elif event.type == "pick_replaced" and event.overall_pick is not None:
            active[event.overall_pick] = event

    reduced: list[ReducedPick] = []
    for overall, event in sorted(active.items()):
        if event.player_id is None or event.round is None or event.team_slot is None:
            continue
        reduced.append(
            ReducedPick(
                event=event,
                overall_pick=overall,
                round=event.round,
                team_slot=event.team_slot,
                player_id=event.player_id,
            )
        )
    return reduced


def next_owner_pick(
    current_overall: int, team_count: int, owner_slot: int, total: int
) -> int | None:
    for overall in range(current_overall + 1, total + 1):
        if snake_team_slot(overall, team_count) == owner_slot:
            return overall
    return None
