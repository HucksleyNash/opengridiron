from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class DraftDomainError(Exception):
    status_code: int
    code: str
    message: str
    context: dict[str, object] = field(default_factory=dict)

    def __str__(self) -> str:
        return self.message


def not_found(entity: str, entity_id: int) -> DraftDomainError:
    return DraftDomainError(
        status_code=404,
        code=f"{entity}_not_found",
        message=f"The {entity.replace('_', ' ')} was not found.",
        context={f"{entity}_id": entity_id},
    )
