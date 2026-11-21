"""Slash commands: /delete movie, /delete tv, /mydeletions and /pendingdeletions. Every reply is private to the user."""

import asyncio
import re

import discord
from discord import app_commands
from discord.utils import escape_markdown, escape_mentions

from .arr import RADARR, SONARR, ArrError, Service, normalize, search
from .embeds import my_requests_embed, preview_embed
from .views import SearchView, SubmitView

MAX_LISTED = 10
AUTOCOMPLETE_WAIT = 2.5  # Discord ignores autocomplete answers after 3 seconds
PICKED = re.compile(r"id:(\d+)")  # the value of a title picked from the autocomplete list


async def suggest(interaction: discord.Interaction, service: Service, current: str) -> list[app_commands.Choice[str]]:
    """Library titles matching what the user has typed so far."""
    client = interaction.client.clients.get(service.key)
    if client is None:
        return []
    fetch = asyncio.ensure_future(client.library())
    fetch.add_done_callback(lambda task: task.cancelled() or task.exception())  # don't log a late failure as unretrieved
    try:
        items = await asyncio.wait_for(asyncio.shield(fetch), AUTOCOMPLETE_WAIT)
    except (asyncio.TimeoutError, ArrError):
        return []  # a slow first load keeps running and fills the cache for the next keystroke
    if current.strip():
        matches = search(items, current)
    else:
        matches = sorted(items, key=lambda item: normalize(item.title))
    return [app_commands.Choice(name=item.label[:100], value=f"id:{item.arr_id}") for item in matches[:25]]


async def find(interaction: discord.Interaction, service: Service, title: str) -> None:
    bot = interaction.client
    if not bot.can_request(interaction.user):
        await interaction.response.send_message("You're not allowed to request deletions.", ephemeral=True)
        return
    client = bot.clients.get(service.key)
    if client is None:
        await interaction.response.send_message(
            f"{service.name} isn't set up, so {service.noun} deletions aren't available.", ephemeral=True)
        return
    # Slash commands work even where the bot can't post, but the result is announced in this channel.
    permissions = interaction.app_permissions
    in_thread = isinstance(interaction.channel, discord.Thread)
    if not (permissions.view_channel and (permissions.send_messages_in_threads if in_thread else permissions.send_messages)):
        await interaction.response.send_message(
            "I can't post in this channel, so I couldn't tell you the result. "
            "Use /delete in a channel where Purgarr can send messages.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True, thinking=True)
    title = title.strip()[:100]
    picked = PICKED.fullmatch(title)
    try:
        if picked:
            item = await client.get(int(picked.group(1)))
            results = [item] if item else []
        else:
            results = search(await client.library(), title)
    except ArrError as exc:
        await interaction.followup.send(f"Couldn't search the library: {exc}", ephemeral=True)
        return

    if not results:
        name = f"That {service.noun}" if picked else f"**{escape_mentions(escape_markdown(title))}**"
        await interaction.followup.send(f"{name} doesn't currently exist on the server.", ephemeral=True)
    elif len(results) == 1:
        view = SubmitView(interaction.user.id, results[0])
        view.message = await interaction.followup.send(
            "Is this the one?", embed=preview_embed(results[0]), view=view, ephemeral=True, wait=True)
    else:
        view = SearchView(interaction.user.id, results, service.noun)
        view.message = await interaction.followup.send(
            f"I found {len(results)} matches, pick the right one:", view=view, ephemeral=True, wait=True)


delete = app_commands.Group(name="delete", description="Ask for a movie or show to be deleted", guild_only=True)


@delete.command(name="movie", description="Ask for a movie to be deleted")
@app_commands.describe(title="Start typing, then pick the movie from the list")
async def delete_movie(interaction: discord.Interaction, title: str) -> None:
    await find(interaction, RADARR, title)


@delete_movie.autocomplete("title")
async def movie_titles(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    return await suggest(interaction, RADARR, current)


@delete.command(name="tv", description="Ask for a TV show to be deleted")
@app_commands.describe(title="Start typing, then pick the show from the list")
async def delete_tv(interaction: discord.Interaction, title: str) -> None:
    await find(interaction, SONARR, title)


@delete_tv.autocomplete("title")
async def show_titles(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    return await suggest(interaction, SONARR, current)


@app_commands.command(name="mydeletions", description="See your deletion requests")
@app_commands.guild_only()
async def my_requests(interaction: discord.Interaction) -> None:
    requests = interaction.client.db.by_user(interaction.user.id)
    await interaction.response.send_message(embed=my_requests_embed(requests), ephemeral=True)


@app_commands.command(name="pendingdeletions", description="Admins: re-post pending deletion requests in the admin channel")
@app_commands.guild_only()
async def pending_requests(interaction: discord.Interaction) -> None:
    bot = interaction.client
    if not bot.is_admin(interaction.user):
        await interaction.response.send_message("Only admins can use this.", ephemeral=True)
        return
    total = bot.db.count_pending()
    if total == 0:
        await interaction.response.send_message("No pending deletion requests.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True, thinking=True)
    channel = bot.get_channel(bot.config.admin_channel_id)
    try:
        for request in bot.db.pending(MAX_LISTED):
            await bot.post_card(request, channel)
    except discord.HTTPException as exc:
        await interaction.followup.send(f"Couldn't post to the admin channel: {exc}", ephemeral=True)
        return
    more = f" (the oldest {MAX_LISTED} of {total})" if total > MAX_LISTED else ""
    await interaction.followup.send(f"Posted the pending requests in <#{channel.id}>{more}.", ephemeral=True)


COMMANDS = (delete, my_requests, pending_requests)
