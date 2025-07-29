from datetime import date as Date
from typing import TYPE_CHECKING, List, Optional

from sqlmodel import Field, Relationship, Session, SQLModel, select

if TYPE_CHECKING:
    from .calendar_event import CalendarEvent


# TODO: consider relationship to planningsessions
class ScheduleDraft(SQLModel, table=True):
    """
    Represents a draft schedule for a given date.
    Related events are linked via CalendarEvent.schedule_draft_id foreign key.
    """

    __tablename__ = "schedule_draft"

    id: Optional[int] = Field(default=None, primary_key=True)
    date: Date = Field(
        index=True, nullable=False, description="Date of the draft schedule"
    )

    # Proper one-to-many relationship
    events: List["CalendarEvent"] = Relationship(
        back_populates="schedule_draft",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )


class DraftStore:
    """
    Provides CRUD operations for ScheduleDraft entries using SQLModel relationships.
    """

    def __init__(self, session: Session):
        self.session = session

    def save(self, draft: ScheduleDraft) -> ScheduleDraft:
        """
        Save a new draft or update an existing one using relationships.
        """
        self.session.add(draft)
        self.session.commit()
        self.session.refresh(draft)
        return draft

    def get_by_date(self, draft_date: Date) -> Optional[ScheduleDraft]:
        """Get a draft by date."""
        stmt = select(ScheduleDraft).where(ScheduleDraft.date == draft_date)
        return self.session.exec(stmt).first()

    def list_all(self) -> List[ScheduleDraft]:
        """List all drafts."""
        stmt = select(ScheduleDraft)
        return list(self.session.exec(stmt).all())

    # def add_events()

    def delete(self, draft_id: int) -> None:
        """Delete a draft and cascade to related events."""
        draft = self.session.get(ScheduleDraft, draft_id)
        if draft:
            self.session.delete(draft)  # Cascades to events
            self.session.commit()
