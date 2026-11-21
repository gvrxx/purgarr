"""Embeds shown to requesters and admins."""

import discord

from .arr import SERVICES, MediaItem
from .db import Request, Requester

STATUS = {
    "pending": ("Waiting for review", discord.Color.orange()),
    "deleting": ("Deleting...", discord.Color.dark_orange()),
    "deleted": ("Deleted", discord.Color.red()),
    "declined": ("Declined", discord.Color.dark_grey()),
    "gone": ("Already removed from the library", discord.Color.light_grey()),
}


def human_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024:
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.2f} TB"


def _kind(service_key: str) -> str:
    service = SERVICES[service_key]
    return f"{service.noun.capitalize()} ({service.name})"


def preview_embed(item: MediaItem) -> discord.Embed:
    """What the requester sees before submitting."""
    overview = item.overview if len(item.overview) <= 300 else item.overview[:297] + "..."
    embed = discord.Embed(title=item.label, url=item.link, description=overview, color=discord.Color.blurple())
    embed.add_field(name="Type", value=_kind(item.service))
    embed.add_field(name="Size on disk", value=human_size(item.size_bytes))
    embed.add_field(name="Files", value=item.detail)
    if item.poster_url:
        embed.set_thumbnail(url=item.poster_url)
    return embed


def admin_embed(request: Request, requesters: list[Requester], dry_run: bool = False) -> discord.Embed:
    """The review card in the admin channel."""
    status, color = STATUS.get(request.status, (request.status, discord.Color.default()))
    embed = discord.Embed(title=request.label, url=request.link, color=color)
    embed.set_author(name=f"{SERVICES[request.service].noun.capitalize()} deletion request")
    embed.add_field(name="Type", value=_kind(request.service))
    embed.add_field(name="Size on disk", value=human_size(request.size_bytes))
    embed.add_field(name="Files", value=request.detail or "-")

    mentions = " ".join(f"<@{r.user_id}>" for r in requesters) or "-"
    if len(mentions) > 1000:
        mentions = mentions[:990] + " ..."
    embed.add_field(name=f"Requested by ({len(requesters)})", value=mentions, inline=False)
    embed.add_field(name="Requested", value=f"<t:{request.created_at}:R>")

    if request.resolved_at and request.resolved_by:
        status = f"{status} by <@{request.resolved_by}> <t:{request.resolved_at}:R>"
    embed.add_field(name="Status", value=status, inline=False)

    if request.poster_url:
        embed.set_thumbnail(url=request.poster_url)
    embed.set_footer(text=f"Request #{request.id}" + (" | DRY RUN: nothing is really deleted" if dry_run else ""))
    return embed


def my_requests_embed(requests: list[Request]) -> discord.Embed:
    embed = discord.Embed(title="Your deletion requests", color=discord.Color.blurple())
    if not requests:
        embed.description = "You haven't requested any deletions yet."
        return embed
    lines = [
        f"**{r.label}** ({SERVICES[r.service].noun}): {STATUS.get(r.status, (r.status,))[0]} <t:{r.created_at}:R>"
        for r in requests
    ]
    embed.description = "\n".join(lines)
    return embed
