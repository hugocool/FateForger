from datetime import date
from typing import List, Optional

from sqlmodel import SQLModel, Field, Column, Session, select
from fateforger.agents.schedular.models.calendar_event import CalendarEvent
from fateforger.agents.schedular.models.calendar_event import PydanticJSON


class ScheduleDraft(SQLModel, table=True):
    """
    Represents a draft schedule for a given date,
    holding a list of CalendarEvent objects.
    """

    __tablename__ = "schedule_drafts"

    id: Optional[int] = Field(default=None, primary_key=True)
    date: date = Field(
        index=True, nullable=False, description="Date of the draft schedule"
    )
    events: List[CalendarEvent] = Field(
        default_factory=list,
        sa_column=Column(PydanticJSON(List[CalendarEvent])),
        description="List of CalendarEvent objects for this draft",
    )


class DraftStore:
    """
    Provides CRUD operations for ScheduleDraft entries.
    """

    def __init__(self, session: Session):
        self.session = session

    def save(self, draft: ScheduleDraft) -> ScheduleDraft:
        """
        Save a new draft or update an existing one.
        """
        self.session.add(draft)
        self.session.commit()
        self.session.refresh(draft)
        return draft

    def get_by_date(self, draft_date: date) -> Optional[ScheduleDraft]:
        """
        Retrieve a ScheduleDraft by its date.
        """
        statement = select(ScheduleDraft).where(ScheduleDraft.date == draft_date)
        result = self.session.exec(statement).first()
        return result

    def list_all(self) -> List[ScheduleDraft]:
        """
        List all stored drafts.
        """
        statement = select(ScheduleDraft)
        return self.session.exec(statement).all()

    def delete(self, draft_id: int) -> None:
        """
        Delete a draft by its ID.
        """
        draft = self.session.get(ScheduleDraft, draft_id)
        if draft:
            self.session.delete(draft)
            self.session.commit()
