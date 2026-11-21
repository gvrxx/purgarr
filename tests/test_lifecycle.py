"""Request lifecycle in the bot, with Discord and Radarr replaced by fakes."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from purgarr.arr import RADARR, ArrError, NotFound, to_item
from purgarr.bot import Purgarr
from purgarr.config import Config

MATRIX = to_item(RADARR, {"id": 1, "title": "The Matrix", "year": 1999, "tmdbId": 603})
HOME_GUILD = 1


class FakeRadarr:
    service = RADARR

    def __init__(self, current=MATRIX, delete_error=None):
        self.current = current
        self.delete_error = delete_error
        self.deleted = []

    async def get(self, arr_id):
        return self.current

    async def delete(self, arr_id, add_exclusion):
        if self.delete_error:
            raise self.delete_error
        self.deleted.append((arr_id, add_exclusion))

    async def recycle_bin(self):
        return "/recycle"


@pytest.fixture
def make_bot(tmp_path):
    bots = []

    def factory(radarr=None, **overrides):
        values = dict(
            discord_token="token", admin_channel_id=999, admin_role_ids=frozenset({50}),
            requester_role_ids=frozenset(), radarr_url="http://radarr", radarr_api_key="key",
            sonarr_url="", sonarr_api_key="", add_import_exclusion=False, dry_run=False,
            db_path=str(tmp_path / f"bot{len(bots)}.db"),
        )
        values.update(overrides)
        bot = Purgarr(Config(**values))
        bot.clients["radarr"] = radarr or FakeRadarr()
        bot.refresh_card = AsyncMock()
        bot.notify_requesters = AsyncMock()
        bot.get_channel = lambda channel_id: SimpleNamespace(id=channel_id, guild=SimpleNamespace(id=HOME_GUILD))
        bots.append(bot)
        return bot

    yield factory
    for bot in bots:
        bot.db.close()


def admin_click(user_id=42):
    return SimpleNamespace(
        user=SimpleNamespace(id=user_id),
        message=SimpleNamespace(id=777),
        response=SimpleNamespace(edit_message=AsyncMock(), send_message=AsyncMock(), defer=AsyncMock()),
        followup=SimpleNamespace(send=AsyncMock()),
        edit_original_response=AsyncMock(),
    )


def member(role_ids=(), guild_id=HOME_GUILD, administrator=False, manage_guild=False):
    user = MagicMock(spec=discord.Member)
    user.guild = SimpleNamespace(id=guild_id)
    user.roles = [SimpleNamespace(id=role_id) for role_id in role_ids]
    user.guild_permissions = SimpleNamespace(administrator=administrator, manage_guild=manage_guild)
    return user


def last_message(interaction):
    return interaction.edit_original_response.await_args.kwargs["content"]


@pytest.mark.asyncio
async def test_approve_deletes_marks_and_notifies(make_bot):
    radarr = FakeRadarr()
    bot = make_bot(radarr)
    request, _ = bot.db.add_request(MATRIX, 10, 100)
    click = admin_click()

    await bot.delete_request(click, request.id, card=None)

    assert radarr.deleted == [(1, False)]
    done = bot.db.get(request.id)
    assert done.status == "deleted" and done.resolved_by == 42
    assert "Deleted **The Matrix (1999)**" in last_message(click)
    bot.notify_requesters.assert_awaited_once()


@pytest.mark.asyncio
async def test_import_exclusion_setting_is_passed(make_bot):
    radarr = FakeRadarr()
    bot = make_bot(radarr, add_import_exclusion=True)
    request, _ = bot.db.add_request(MATRIX, 10, 100)
    await bot.delete_request(admin_click(), request.id, card=None)
    assert radarr.deleted == [(1, True)]


@pytest.mark.asyncio
async def test_dry_run_deletes_nothing(make_bot):
    radarr = FakeRadarr()
    bot = make_bot(radarr, dry_run=True)
    request, _ = bot.db.add_request(MATRIX, 10, 100)
    click = admin_click()
    await bot.delete_request(click, request.id, card=None)
    assert radarr.deleted == []
    assert bot.db.get(request.id).status == "deleted"
    assert "dry run" in last_message(click)


@pytest.mark.asyncio
async def test_already_gone(make_bot):
    for radarr in (FakeRadarr(current=None), FakeRadarr(delete_error=NotFound("gone"))):
        bot = make_bot(radarr)
        request, _ = bot.db.add_request(MATRIX, 10, 100)
        click = admin_click()
        await bot.delete_request(click, request.id, card=None)
        assert bot.db.get(request.id).status == "gone"
        assert "already gone" in last_message(click)


@pytest.mark.asyncio
async def test_failed_delete_stays_pending(make_bot):
    bot = make_bot(FakeRadarr(delete_error=ArrError("Could not reach Radarr")))
    request, _ = bot.db.add_request(MATRIX, 10, 100)
    click = admin_click()
    await bot.delete_request(click, request.id, card=None)
    assert bot.db.get(request.id).status == "pending"
    assert "still pending" in last_message(click)
    bot.notify_requesters.assert_not_awaited()


@pytest.mark.asyncio
async def test_changed_item_is_not_deleted(make_bot):
    other = to_item(RADARR, {"id": 1, "title": "Something Else", "year": 2020, "tmdbId": 111})
    radarr = FakeRadarr(current=other)
    bot = make_bot(radarr)
    request, _ = bot.db.add_request(MATRIX, 10, 100)
    await bot.delete_request(admin_click(), request.id, card=None)
    assert radarr.deleted == []
    assert bot.db.get(request.id).status == "pending"


@pytest.mark.asyncio
async def test_second_confirm_does_not_delete_twice(make_bot):
    radarr = FakeRadarr()
    bot = make_bot(radarr)
    request, _ = bot.db.add_request(MATRIX, 10, 100)
    await bot.delete_request(admin_click(), request.id, card=None)
    late = admin_click(user_id=43)
    await bot.delete_request(late, request.id, card=None)
    assert radarr.deleted == [(1, False)]
    assert "already handled" in last_message(late)
    assert bot.db.get(request.id).resolved_by == 42


@pytest.mark.asyncio
async def test_decline_and_stale_decline(make_bot):
    bot = make_bot()
    request, _ = bot.db.add_request(MATRIX, 10, 100)

    click = admin_click()
    await bot.decline_request(click, request.id)
    assert bot.db.get(request.id).status == "declined"
    assert click.response.edit_message.await_args.kwargs["view"] is None
    bot.notify_requesters.assert_awaited_once()

    late = admin_click()
    await bot.decline_request(late, request.id)
    late.response.edit_message.assert_awaited_once()
    assert "already handled" in late.followup.send.await_args.args[0]

    missing = admin_click()
    await bot.decline_request(missing, 12345)
    assert "no longer exists" in missing.response.send_message.await_args.args[0]


@pytest.mark.asyncio
async def test_confirm_is_refused_for_handled_requests(make_bot):
    bot = make_bot()
    request, _ = bot.db.add_request(MATRIX, 10, 100)
    bot.db.decline(request.id, admin_id=1)
    click = admin_click()
    await bot.confirm_delete(click, request.id)
    click.response.defer.assert_not_awaited()
    click.followup.send.assert_awaited_once()


@pytest.mark.asyncio
async def test_submit_posts_once_then_joins(make_bot):
    bot = make_bot()

    async def post_card(request, channel):
        bot.db.set_admin_message(request.id, channel.id, 555)

    bot.post_card = AsyncMock(side_effect=post_card)

    assert "sent" in await bot.submit_request(MATRIX, 10, 100)
    assert "already asked" in await bot.submit_request(MATRIX, 10, 100)
    assert "added you" in await bot.submit_request(MATRIX, 11, 100)
    bot.post_card.assert_awaited_once()
    bot.refresh_card.assert_awaited_once()


def test_permissions(make_bot):
    bot = make_bot()
    assert bot.is_admin(member(role_ids=[50]))
    assert bot.is_admin(member(manage_guild=True))
    assert not bot.is_admin(member(role_ids=[7]))
    assert not bot.is_admin(member(role_ids=[50], guild_id=2))  # admin role ID in some other server
    assert not bot.is_admin(SimpleNamespace(id=1))  # not a server member (DM)

    assert bot.can_request(member())
    assert not bot.can_request(member(guild_id=2))

    restricted = make_bot(requester_role_ids=frozenset({60}))
    assert restricted.can_request(member(role_ids=[60]))
    assert restricted.can_request(member(role_ids=[50]))
    assert not restricted.can_request(member(role_ids=[7]))


@pytest.mark.asyncio
async def test_clicks_during_a_delete_leave_the_card_alone(make_bot):
    bot = make_bot()
    request, _ = bot.db.add_request(MATRIX, 10, 100)
    bot.db.claim_for_deletion(request.id)
    for handler in (bot.confirm_delete, bot.decline_request):
        click = admin_click()
        await handler(click, request.id)
        click.response.edit_message.assert_not_awaited()
        assert "deleting this right now" in click.response.send_message.await_args.args[0]
    assert bot._view(bot.db.get(request.id)) is not None
