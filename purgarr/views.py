"""Buttons and menus."""

import discord

from .arr import MediaItem
from .embeds import human_size, preview_embed

MENU_TIMEOUT = 180


class _AuthorOnlyView(discord.ui.View):
    """A short-lived menu that only the person who ran the command can use."""

    def __init__(self, author_id: int):
        super().__init__(timeout=MENU_TIMEOUT)
        self.author_id = author_id
        self.message: discord.Message | None = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.author_id:
            return True
        await interaction.response.send_message("This menu belongs to someone else.", ephemeral=True)
        return False

    async def on_timeout(self) -> None:
        if self.message:
            try:
                await self.message.edit(content="This menu expired, run the command again.", view=None)
            except discord.HTTPException:
                pass


class SearchView(_AuthorOnlyView):
    def __init__(self, author_id: int, results: list[MediaItem], noun: str = "title"):
        super().__init__(author_id)
        self.results = results[:25]
        self.select = discord.ui.Select(
            placeholder=f"Select a {noun} here",
            options=[
                discord.SelectOption(
                    label=item.label[:100],
                    description=f"{item.detail} | {human_size(item.size_bytes)}"[:100],
                    value=str(index),
                )
                for index, item in enumerate(self.results)
            ],
        )
        self.select.callback = self.on_select
        self.add_item(self.select)

    async def on_select(self, interaction: discord.Interaction) -> None:
        item = self.results[int(self.select.values[0])]
        view = SubmitView(self.author_id, item)
        view.message = self.message
        self.stop()
        await interaction.response.edit_message(content="Is this the one?", embed=preview_embed(item), view=view)


class SubmitView(_AuthorOnlyView):
    def __init__(self, author_id: int, item: MediaItem):
        super().__init__(author_id)
        self.item = item

    @discord.ui.button(label="Request deletion", style=discord.ButtonStyle.danger)
    async def submit(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.stop()
        await interaction.response.defer()
        text = await interaction.client.submit_request(self.item, interaction.user.id, interaction.channel_id)
        await interaction.edit_original_response(content=text, view=None)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.stop()
        await interaction.response.edit_message(content="Cancelled, nothing was requested.", embed=None, view=None)


class AdminButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"purgarr:(?P<action>delete|decline):(?P<id>\d+)",
):
    """Delete/Decline on a review card. The request ID lives in custom_id, so buttons keep working after a restart."""

    def __init__(self, action: str, request_id: int):
        if action == "delete":
            button = discord.ui.Button(label="Delete", style=discord.ButtonStyle.danger)
        else:
            button = discord.ui.Button(label="Decline", style=discord.ButtonStyle.secondary)
        button.custom_id = f"purgarr:{action}:{request_id}"
        super().__init__(button)
        self.action = action
        self.request_id = request_id

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Button, match):
        return cls(match["action"], int(match["id"]))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.client.is_admin(interaction.user):
            return True
        await interaction.response.send_message("Only admins can delete or decline requests.", ephemeral=True)
        return False

    async def callback(self, interaction: discord.Interaction) -> None:
        if self.action == "delete":
            await interaction.client.confirm_delete(interaction, self.request_id)
        else:
            await interaction.client.decline_request(interaction, self.request_id)


def admin_view(request_id: int) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    view.add_item(AdminButton("delete", request_id))
    view.add_item(AdminButton("decline", request_id))
    return view


class ConfirmDeleteView(discord.ui.View):
    """The 'are you sure?' step, shown only to the admin who pressed Delete."""

    def __init__(self, request_id: int, card: discord.Message | None):
        super().__init__(timeout=600)
        self.request_id = request_id
        self.card = card

    @discord.ui.button(label="Yes, delete it", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.stop()
        await interaction.response.edit_message(content="Deleting...", view=None)
        await interaction.client.delete_request(interaction, self.request_id, self.card)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.stop()
        await interaction.response.edit_message(content="Cancelled, nothing was deleted.", view=None)
