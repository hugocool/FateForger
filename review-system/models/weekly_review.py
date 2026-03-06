"""
WeeklyReview schema for the Weekly Reviews Notion database.
Bound at runtime via db_title — auto-discovers the DB by name.
"""
import ultimate_notion as uno


class WeeklyReview(uno.Schema, db_title='Weekly Reviews'):
    """One row per week. Written to incrementally as each review phase gate is met."""

    week                = uno.PropType.Date('week')
    intention           = uno.PropType.Text('intention')
    wip_count           = uno.PropType.Number('wip_count')
    themes              = uno.PropType.Text('themes')
    failure_looks_like  = uno.PropType.Text('failure_looks_like')
    thursday_signal     = uno.PropType.Text('thursday_signal')
    clarity_gaps        = uno.PropType.Text('clarity_gaps')
    timebox_directives  = uno.PropType.Text('timebox_directives')
    scrum_directives    = uno.PropType.Text('scrum_directives')
