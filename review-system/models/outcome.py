"""
Outcome schema for the Outcomes Notion database.
N rows per week, each linked to a WeeklyReview via relation.
"""
import ultimate_notion as uno
from models.weekly_review import WeeklyReview


class PriorityOptions(uno.OptionNS):
    MUST    = uno.Option(name='Must',    color=uno.Color.GREEN)
    SUPPORT = uno.Option(name='Support', color=uno.Color.BLUE)


class StatusOptions(uno.OptionNS):
    HIT     = uno.Option(name='Hit',     color=uno.Color.GREEN)
    PARTIAL = uno.Option(name='Partial', color=uno.Color.YELLOW)
    MISS    = uno.Option(name='Miss',    color=uno.Color.RED)


class Outcome(uno.Schema, db_title='Outcomes'):
    """One row per outcome. Linked back to the week it was committed in."""

    title    = uno.PropType.Title('title')
    dod      = uno.PropType.Text('dod')
    priority = uno.PropType.Select('priority', options=PriorityOptions)
    status   = uno.PropType.Select('status',   options=StatusOptions)
    ticket   = uno.PropType.URL('ticket')
    review   = uno.PropType.Relation('review', schema=WeeklyReview)
