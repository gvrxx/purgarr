"""The bot: connects Discord to Radarr/Sonarr and runs the request lifecycle."""

import logging
from collections import defaultdict

import aiohttp
import discord
from discord import app_commands

from .arr import RADARR, SERVICES, SONARR, ArrClient, ArrError, MediaItem, NotFound
from .commands import COMMANDS
from .config import Config
from .db import OPEN_STATUSES, Database, Request
from .embeds import STATUS, admin_embed, human_size
from .views import AdminButton, ConfirmDeleteView, admin_view

log = logging.getLogger(__name__)


class Purgarr(discord.Client):
    def __init__(self, config: Config):
        super().__init__(
            intents=discord.Intents.default(),
            allowed_mentions=discord.AllowedMentions(everyone=False, roles=False, users=True),
        )
        self.config = config
        self.db = Database(config.db_path)
        self.clients: dict[str, ArrClient] = {}
        self.tree = app_commands.CommandTree(self)
        self.tree.error(self._on_command_error)
        self._session: aiohttp.ClientSession | None = None
        self._commands_synced = False

    async def setup_hook(self) -> None:
        self._session = aiohttp.ClientSession()
        for service, url, key in (
            (RADARR, self.config.radarr_url, self.config.radarr_api_key),
            (SONARR, self.config.sonarr_url, self.config.sonarr_api_key),
        ):
            if url and key:
                self.clients[service.key] = ArrClient(service, url, key, self._session)

        self.add_dynamic_items(AdminButton)
        for command in COMMANDS:
            self.tree.add_command(command)

        for client in self.clients.values():
            try:
                recycle_bin = await client.recycle_bin()
                titles = len(await client.library())  # also warms the cache so autocomplete answers quickly
            except ArrError as exc:
                log.error("%s", exc)
                continue
            if recycle_bin:
                log.info("Connected to %s: %s titles, recycle bin %s", client.service.name, titles, recycle_bin)
            else:
                log.warning("Connected to %s: %s titles, but no recycle bin, so deletions are permanent",
                            client.service.name, titles)

    async def close(self) -> None:
        await super().close()
        if self._session:
            await self._session.close()
        self.db.close()

    async def on_ready(self) -> None:
        log.info("Logged in as %s", self.user)
        channel = self.get_channel(self.config.admin_channel_id)
        if channel is None:
            log.warning("Can't see ADMIN_CHANNEL_ID %s: check the ID and that the bot can view it",
                        self.config.admin_channel_id)
            return
        if self._commands_synced:
            return  # on_ready fires again after reconnects

        # Register the commands in the admin channel's server only; they show up there instantly.
        guild = channel.guild
        self.tree.copy_global_to(guild=guild)
        try:
            await self.tree.sync(guild=guild)
        except discord.HTTPException as exc:
            log.error("Couldn't register slash commands in %s: %s", guild.name, exc)
            return
        self._commands_synced = True
        log.info("Slash commands ready in %s", guild.name)

    async def _on_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        name = interaction.command.qualified_name if interaction.command else "unknown"
        log.error("/%s failed", name, exc_info=getattr(error, "original", error))
        message = "Something went wrong, check the Purgarr container logs."
        try:
            if interaction.response.is_done():
                await interaction.followup.send(message, ephemeral=True)
            else:
                await interaction.response.send_message(message, ephemeral=True)
        except discord.HTTPException:
            pass

    # Permissions

    def _in_home_server(self, user: discord.abc.User) -> bool:
        channel = self.get_channel(self.config.admin_channel_id)
        guild = getattr(channel, "guild", None)
        return isinstance(user, discord.Member) and guild is not None and user.guild.id == guild.id

    def is_admin(self, user: discord.abc.User) -> bool:
        if not self._in_home_server(user):
            return False
        permissions = user.guild_permissions
        if permissions.administrator or permissions.manage_guild:
            return True
        return any(role.id in self.config.admin_role_ids for role in user.roles)

    def can_request(self, user: discord.abc.User) -> bool:
        if not self._in_home_server(user):
            return False
        if not self.config.requester_role_ids or self.is_admin(user):
            return True
        return any(role.id in self.config.requester_role_ids for role in user.roles)

    # Review cards

    def _render(self, request: Request) -> discord.Embed:
        return admin_embed(request, self.db.requesters(request.id), self.config.dry_run)

    def _view(self, request: Request) -> discord.ui.View | None:
        return admin_view(request.id) if request.status in OPEN_STATUSES else None

    async def post_card(self, request: Request, channel: discord.abc.Messageable) -> None:
        message = await channel.send(embed=self._render(request), view=self._view(request))
        self.db.set_admin_message(request.id, message.channel.id, message.id)

    async def refresh_card(self, request: Request, extra: discord.Message | None = None,
                           skip_message_id: int | None = None) -> None:
        """Re-renders the stored review card, plus `extra` (a copy that was clicked) if it's a different message."""
        targets = []
        if request.admin_channel_id and request.admin_message_id and request.admin_message_id != skip_message_id:
            channel = self.get_partial_messageable(request.admin_channel_id)
            targets.append(channel.get_partial_message(request.admin_message_id))
        if extra and extra.id not in (request.admin_message_id, skip_message_id):
            targets.append(extra)
        for message in targets:
            try:
                await message.edit(embed=self._render(request), view=self._view(request))
            except discord.HTTPException as exc:
                log.warning("Couldn't update review card for request #%s: %s", request.id, exc)

    async def _show_stale(self, interaction: discord.Interaction, request: Request | None) -> None:
        if request is None:
            await interaction.response.send_message("That request no longer exists.", ephemeral=True)
            return
        if request.status == "deleting":
            # Leave the card alone: if the delete fails, its buttons are still needed.
            await interaction.response.send_message("Someone is deleting this right now.", ephemeral=True)
            return
        await interaction.response.edit_message(embed=self._render(request), view=self._view(request))
        await interaction.followup.send(f"This request is already handled: {STATUS[request.status][0]}.",
                                        ephemeral=True)

    async def notify_requesters(self, request: Request, text: str) -> None:
        by_channel: dict[int, list[int]] = defaultdict(list)
        for requester in self.db.requesters(request.id):
            by_channel[requester.channel_id].append(requester.user_id)
        for channel_id, user_ids in by_channel.items():
            mentions = " ".join(f"<@{user_id}>" for user_id in user_ids)
            try:
                await self.get_partial_messageable(channel_id).send(f"{mentions} {text}")
            except discord.HTTPException as exc:
                log.warning("Couldn't notify requesters in channel %s: %s", channel_id, exc)

    # Request lifecycle

    async def submit_request(self, item: MediaItem, user_id: int, channel_id: int) -> str:
        request, outcome = self.db.add_request(item, user_id, channel_id)
        if outcome == "duplicate":
            return f"You already asked to delete **{request.label}**. It's waiting for an admin."
        try:
            if request.admin_message_id is None:
                channel = self.get_channel(self.config.admin_channel_id) or await self.fetch_channel(
                    self.config.admin_channel_id)
                await self.post_card(request, channel)
            else:
                await self.refresh_card(request)
        except discord.HTTPException as exc:
            log.error("Couldn't post request #%s to the admin channel: %s", request.id, exc)
            return (f"Your request for **{request.label}** was saved, but I couldn't post it for review. "
                    "Ask an admin to run `/pendingdeletions`.")

        log.info("Request #%s for %s: user %s (%s)", request.id, request.label, user_id, outcome)
        if outcome == "joined":
            return f"Someone already asked to delete **{request.label}**, so I added you to that request."
        return f"Deletion request for **{request.label}** sent. An admin will review it."

    async def decline_request(self, interaction: discord.Interaction, request_id: int) -> None:
        if not self.db.decline(request_id, interaction.user.id):
            await self._show_stale(interaction, self.db.get(request_id))
            return
        request = self.db.get(request_id)
        log.info("Request #%s for %s declined by %s", request.id, request.label, interaction.user)
        await interaction.response.edit_message(embed=self._render(request), view=None)
        await self.refresh_card(request, skip_message_id=interaction.message.id)
        await self.notify_requesters(request, f"your request to delete **{request.label}** was declined.")

    async def confirm_delete(self, interaction: discord.Interaction, request_id: int) -> None:
        request = self.db.get(request_id)
        if request is None or request.status != "pending":
            await self._show_stale(interaction, request)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        service = SERVICES[request.service]
        warning = ""
        client = self.clients.get(request.service)
        if client:
            try:
                if not await client.recycle_bin():
                    warning = f"\n**Warning:** {service.name} has no recycle bin set, so this can't be undone."
            except ArrError:
                pass
        await interaction.followup.send(
            f"Delete **{request.label}** ({human_size(request.size_bytes)}) and its files from {service.name}?{warning}",
            view=ConfirmDeleteView(request.id, interaction.message),
            ephemeral=True,
        )

    async def delete_request(self, interaction: discord.Interaction, request_id: int,
                             card: discord.Message | None) -> None:
        if not self.db.claim_for_deletion(request_id):
            await interaction.edit_original_response(content="This request was already handled.")
            if request := self.db.get(request_id):
                await self.refresh_card(request, extra=card)
            return

        request = self.db.get(request_id)
        service = SERVICES[request.service]
        client = self.clients.get(request.service)
        if client is None:
            self.db.release(request_id)
            await interaction.edit_original_response(content=f"{service.name} isn't configured, nothing was deleted.")
            return

        try:
            current = await client.get(request.arr_id)
            gone = current is None
            if current and request.external_id and current.external_id != request.external_id:
                self.db.release(request_id)
                await interaction.edit_original_response(
                    content=f"{service.name} item #{request.arr_id} is now **{current.label}**, not "
                            f"**{request.label}**. Nothing was deleted; decline this request.")
                return
            if current and self.config.dry_run:
                log.warning("DRY RUN: would delete %s #%s (%s)", service.name, request.arr_id, request.label)
            elif current:
                await client.delete(request.arr_id, self.config.add_import_exclusion)
        except NotFound:
            gone = True
        except ArrError as exc:
            self.db.release(request_id)
            await interaction.edit_original_response(content=f"Delete failed, the request is still pending.\n{exc}")
            return
        except Exception:
            self.db.release(request_id)
            log.exception("Unexpected error deleting request #%s", request_id)
            await interaction.edit_original_response(content="Delete failed, check the Purgarr container logs.")
            raise

        self.db.finish_deletion(request_id, interaction.user.id, gone=gone)
        request = self.db.get(request_id)
        log.info("Request #%s for %s approved by %s (%s)", request.id, request.label, interaction.user,
                 "already gone" if gone else "dry run" if self.config.dry_run else "deleted")
        await self.refresh_card(request, extra=card)

        if gone:
            await interaction.edit_original_response(content=f"**{request.label}** was already gone from {service.name}.")
            await self.notify_requesters(request, f"**{request.label}** has been removed from the library.")
        else:
            suffix = " (dry run, nothing was really deleted)" if self.config.dry_run else ""
            await interaction.edit_original_response(content=f"Deleted **{request.label}**{suffix}.")
            await self.notify_requesters(request, f"your request was approved and **{request.label}** was deleted{suffix}.")
