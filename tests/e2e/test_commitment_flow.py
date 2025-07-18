import pytest

from fateforger.haunters.commitment import CommitmentHaunter


class TestCommitmentFlow:
    @pytest.mark.slow
    @pytest.mark.asyncio
    async def test_commitment_mark_done(self, db_session, mock_slack_client, scheduler):
        haunter = CommitmentHaunter(1, mock_slack_client, scheduler, db_session)
        await haunter.remind()
        await haunter.handle_reply("mark_done")
        assert not scheduler.get_jobs()
