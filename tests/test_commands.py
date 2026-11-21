"""Slash command handlers, with Discord and Radarr replaced by fakes."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from discord import app_commands

from purgarr import commands
from purgarr.arr import RADARR, SONARR, ArrError, to_item
from purgarr.views import SearchView, SubmitView

LIBRARY = [
    to_item(RADARR, {"id": 1, "title": "Big Bang", "year": 2015, "tmdbId": 101}),
    to_item(RADARR, {"id": 2, "title": "Big Bang", "year": 2021, "tmdbId": 102}),
    to_item(RADARR, {"id": 3, "title": "The Big Bang Theory", "year": 2007, "tmdbId": 103}),
    to_item(RADARR, {"id": 4, "title": "Heat", "year": 1995, "tmdbId": 104}),
]


class FakeArr:
    def __init__(self, items=LIBRARY, error=None, delay=0.0):
        self.items, self.error, self.delay = items, error, delay

    async def library(self):
        await asyncio.sleep(self.delay)
        if self.error:
            raise self.error
        return self.items

    async def get(self, arr_id):
        if self.error:
            raise self.error
        return next((item for item in self.items if item.arr_id == arr_id), None)


def make_interaction(radarr=None, can_request=True, admin=False, bot_can_post=True):
    bot = SimpleNamespace(
        clients={"radarr": radarr or FakeArr()},
        can_request=lambda user: can_request,
        is_admin=lambda user: admin,
        db=MagicMock(),
        config=SimpleNamespace(admin_channel_id=999),
        get_channel=lambda channel_id: SimpleNamespace(id=channel_id),
        post_card=AsyncMock(),
    )
    return SimpleNamespace(
        client=bot,
        user=SimpleNamespace(id=42),
        channel_id=100,
        channel=SimpleNamespace(id=100),
        app_permissions=discord.Permissions(view_channel=True, send_messages=bot_can_post),
        response=SimpleNamespace(send_message=AsyncMock(), defer=AsyncMock()),
        followup=SimpleNamespace(send=AsyncMock(return_value=SimpleNamespace(id=1))),
    )


@pytest.mark.asyncio
async def test_refuses_channels_where_the_result_cannot_be_announced():
    interaction = make_interaction(bot_can_post=False)
    await commands.find(interaction, RADARR, "heat")
    interaction.response.defer.assert_not_awaited()
    call = interaction.response.send_message.await_args
    assert "can't post in this channel" in call.args[0] and call.kwargs["ephemeral"] is True


def followup(interaction):
    call = interaction.followup.send.await_args
    assert call.kwargs["ephemeral"] is True
    return (call.args[0] if call.args else ""), call.kwargs


@pytest.mark.asyncio
async def test_autocomplete_lists_matching_library_titles():
    choices = await commands.suggest(make_interaction(), RADARR, "big bang")
    assert [(c.name, c.value) for c in choices] == [
        ("Big Bang (2015)", "id:1"), ("Big Bang (2021)", "id:2"), ("The Big Bang Theory (2007)", "id:3"),
    ]


@pytest.mark.asyncio
async def test_autocomplete_with_nothing_typed_lists_titles_alphabetically():
    choices = await commands.suggest(make_interaction(), RADARR, "")
    assert [c.name for c in choices] == ["Big Bang (2015)", "Big Bang (2021)", "Heat (1995)", "The Big Bang Theory (2007)"]


@pytest.mark.asyncio
async def test_autocomplete_gives_up_quietly(monkeypatch):
    monkeypatch.setattr(commands, "AUTOCOMPLETE_WAIT", 0.05)
    assert await commands.suggest(make_interaction(FakeArr(delay=1)), RADARR, "heat") == []
    assert await commands.suggest(make_interaction(FakeArr(error=ArrError("down"))), RADARR, "heat") == []
    assert await commands.suggest(make_interaction(), SONARR, "heat") == []  # Sonarr not configured


@pytest.mark.asyncio
async def test_picking_from_autocomplete_shows_the_preview():
    interaction = make_interaction()
    await commands.find(interaction, RADARR, "id:4")
    interaction.response.defer.assert_awaited_once_with(ephemeral=True, thinking=True)
    text, kwargs = followup(interaction)
    assert text == "Is this the one?"
    assert kwargs["embed"].title == "Heat (1995)"
    assert isinstance(kwargs["view"], SubmitView) and kwargs["view"].item.arr_id == 4
    assert kwargs["view"].message is not None


@pytest.mark.asyncio
async def test_typed_title_with_several_matches_shows_a_dropdown():
    interaction = make_interaction()
    await commands.find(interaction, RADARR, "big bang")
    _, kwargs = followup(interaction)
    view = kwargs["view"]
    assert isinstance(view, SearchView)
    assert view.select.placeholder == "Select a movie here"
    assert [option.label for option in view.select.options] == [
        "Big Bang (2015)", "Big Bang (2021)", "The Big Bang Theory (2007)"]


@pytest.mark.asyncio
async def test_not_on_the_server():
    typed = make_interaction()
    await commands.find(typed, RADARR, "Totally Unknown")
    assert followup(typed)[0] == "**Totally Unknown** doesn't currently exist on the server."

    picked = make_interaction()
    await commands.find(picked, RADARR, "id:999")
    assert followup(picked)[0] == "That movie doesn't currently exist on the server."


@pytest.mark.asyncio
async def test_library_errors_and_refusals():
    broken = make_interaction(FakeArr(error=ArrError("Could not reach Radarr")))
    await commands.find(broken, RADARR, "heat")
    assert "Couldn't search the library: Could not reach Radarr" in followup(broken)[0]

    refused = make_interaction(can_request=False)
    await commands.find(refused, RADARR, "heat")
    refused.response.defer.assert_not_awaited()
    assert refused.response.send_message.await_args.kwargs["ephemeral"] is True

    no_sonarr = make_interaction()
    await commands.find(no_sonarr, SONARR, "heat")
    assert "Sonarr isn't set up" in no_sonarr.response.send_message.await_args.args[0]


@pytest.mark.asyncio
async def test_requests_command_posts_cards_to_the_admin_channel():
    not_admin = make_interaction()
    await commands.pending_requests.callback(not_admin)
    assert "Only admins" in not_admin.response.send_message.await_args.args[0]

    empty = make_interaction(admin=True)
    empty.client.db.count_pending.return_value = 0
    await commands.pending_requests.callback(empty)
    assert "No pending" in empty.response.send_message.await_args.args[0]

    busy = make_interaction(admin=True)
    busy.client.db.count_pending.return_value = 2
    busy.client.db.pending.return_value = ["request-1", "request-2"]
    await commands.pending_requests.callback(busy)
    assert [call.args[0] for call in busy.client.post_card.await_args_list] == ["request-1", "request-2"]
    assert all(call.args[1].id == 999 for call in busy.client.post_card.await_args_list)
    assert "<#999>" in followup(busy)[0]


@pytest.mark.asyncio
async def test_mydeletions_is_private():
    interaction = make_interaction()
    interaction.client.db.by_user.return_value = []
    await commands.my_requests.callback(interaction)
    kwargs = interaction.response.send_message.await_args.kwargs
    assert kwargs["ephemeral"] is True and kwargs["embed"].title == "Your deletion requests"


@pytest.mark.asyncio
async def test_command_definitions_are_valid_for_discord():
    tree = app_commands.CommandTree(discord.Client(intents=discord.Intents.default()))
    for command in commands.COMMANDS:
        tree.add_command(command)
    payloads = {c.name: c.to_dict(tree) for c in tree.get_commands()}

    assert set(payloads) == {"delete", "mydeletions", "pendingdeletions"}
    subcommands = {option["name"]: option for option in payloads["delete"]["options"]}
    assert set(subcommands) == {"movie", "tv"}
    for sub in subcommands.values():
        (title,) = sub["options"]
        assert title["name"] == "title" and title["required"] and title["autocomplete"]
