from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING, Any, Generic, Literal, Self, TypeVar

import discord
from discord.utils import MISSING
from loguru import logger
from seria.utils import clean_url, split_list_to_chunks

from hoyo_buddy.db import get_locale

from .. import emojis
from ..bot.error_handler import get_error_embed
from ..embeds import ErrorEmbed
from ..exceptions import InvalidInputError
from ..l10n import LocaleStr, translator

if TYPE_CHECKING:
    import io
    from collections.abc import Iterable, Sequence

    from hoyo_buddy.enums import Locale

    from ..types import Interaction, User


__all__ = (
    "Button",
    "GoBackButton",
    "Modal",
    "PaginatorSelect",
    "Select",
    "SelectOption",
    "TextInput",
    "ToggleButton",
    "ToggleUIButton",
    "URLButtonView",
    "V_co",
    "View",
)

V_co = TypeVar("V_co", bound="View", covariant=True)


class View(discord.ui.View):
    def __init__(self, *, author: User, locale: Locale) -> None:
        super().__init__(timeout=600)
        self.author = author
        self.locale = locale
        self.message: discord.Message | None = None
        self.item_states: dict[str, bool] = {}

    def __repr__(self) -> str:
        return (
            f"{self.__class__.__module__.replace('hoyo_buddy.ui.', '')}.{self.__class__.__name__}"
        )

    def add_items(self, items: Iterable[Button | Select]) -> Self:
        for item in items:
            self.add_item(item)
        return self

    async def on_timeout(self) -> None:
        if self.message and not all(
            item.url for item in self.children if isinstance(item, (discord.ui.Button))
        ):
            self.clear_items()
            with contextlib.suppress(discord.HTTPException):
                await self.message.edit(view=self)
        else:
            logger.warning(f"View {self!r} timed out without a set message")

    async def on_error(self, i: Interaction, error: Exception, item: discord.ui.Item[Any]) -> None:
        locale = await get_locale(i)
        embed, recognized = get_error_embed(error, locale)
        if not recognized:
            i.client.capture_exception(error)

        with contextlib.suppress(Exception):
            await item.unset_loading_state(i)  # pyright: ignore[reportAttributeAccessIssue]
            await self.absolute_edit(i)
        await self.absolute_send(i, embed=embed, ephemeral=True)

    async def interaction_check(self, i: Interaction) -> bool:
        if self.author is None:
            return True

        locale = await get_locale(i)

        if i.user.id != self.author.id:
            embed = ErrorEmbed(
                locale,
                title=LocaleStr(key="interaction_failed_title"),
                description=LocaleStr(key="interaction_failed_description"),
            )
            await i.response.send_message(embed=embed, ephemeral=True)
            return False
        return True

    def disable_items(self) -> None:
        for child in self.children:
            if isinstance(child, discord.ui.Button | discord.ui.Select):
                if child.custom_id is not None:
                    self.item_states[child.custom_id] = child.disabled

                if isinstance(child, discord.ui.Button) and child.url:
                    continue

                child.disabled = True

    def enable_items(self) -> None:
        for child in self.children:
            if isinstance(child, discord.ui.Button | discord.ui.Select):
                if isinstance(child, discord.ui.Button) and child.url:
                    continue

                if child.custom_id is not None:
                    child.disabled = self.item_states.get(child.custom_id, False)
                else:
                    # Cannot determine the original state of the item
                    child.disabled = False

    def add_item(self, item: Button | Select, *, translate: bool = True) -> Self:
        if translate:
            item.translate(self.locale)
        return super().add_item(item)

    def get_item(self, custom_id: str) -> Any:
        for item in self.children:
            if isinstance(item, Button | Select) and item.custom_id == custom_id:
                return item

        msg = f"No item found with custom_id {custom_id!r}"
        raise ValueError(msg)

    def translate_items(self) -> None:
        for item in self.children:
            if isinstance(item, Button | Select):
                item.translate(self.locale)

    @staticmethod
    async def absolute_send(i: Interaction, **kwargs: Any) -> None:
        with contextlib.suppress(discord.HTTPException):
            if not i.response.is_done():
                await i.response.send_message(**kwargs)
            else:
                await i.followup.send(**kwargs)

    @staticmethod
    async def absolute_edit(i: Interaction, **kwargs: Any) -> None:
        with contextlib.suppress(discord.HTTPException):
            if not i.response.is_done():
                await i.response.edit_message(**kwargs)
            else:
                await i.edit_original_response(**kwargs)

    @staticmethod
    def get_embeds(message: discord.Message | None) -> list[discord.Embed] | None:
        if message:
            return message.embeds
        return None


class URLButtonView(discord.ui.View):
    def __init__(
        self,
        locale: Locale,
        *,
        url: str,
        label: str | LocaleStr | None = None,
        emoji: str | None = None,
    ) -> None:
        super().__init__()
        self.add_item(
            discord.ui.Button(
                label=translator.translate(label, locale) if label else None, url=url, emoji=emoji
            )
        )


class Button(discord.ui.Button, Generic[V_co]):
    def __init__(
        self,
        *,
        style: discord.ButtonStyle = discord.ButtonStyle.secondary,
        label: LocaleStr | str | None = None,
        disabled: bool = False,
        custom_id: str | None = None,
        url: str | None = None,
        emoji: str | None = None,
        row: int | None = None,
    ) -> None:
        super().__init__(
            style=style, disabled=disabled, custom_id=custom_id, url=url, emoji=emoji, row=row
        )

        self.locale_str_label = label
        self.original_label: str | None = None
        self.original_emoji: str | None = None
        self.original_disabled: bool | None = None

        self.view: V_co

    def translate(self, locale: Locale) -> None:
        if self.locale_str_label:
            self.label = translator.translate(self.locale_str_label, locale)

    async def set_loading_state(self, i: Interaction, **kwargs: Any) -> None:
        self.original_label = self.label[:] if self.label else None
        self.original_emoji = str(self.emoji) if self.emoji else None
        self.original_disabled = self.disabled

        self.view.disable_items()

        self.disabled = True
        self.emoji = emojis.LOADING
        self.label = translator.translate(LocaleStr(key="loading_text"), self.view.locale)

        await self.view.absolute_edit(i, view=self.view, **kwargs)

    async def unset_loading_state(self, i: Interaction, **kwargs: Any) -> None:
        if self.original_disabled is None:
            msg = "unset_loading_state called before set_loading_state"
            raise RuntimeError(msg)

        self.view.enable_items()

        self.disabled = self.original_disabled
        self.emoji = self.original_emoji
        self.label = self.original_label

        await self.view.absolute_edit(i, view=self.view, **kwargs)


class GoBackButton(Button, Generic[V_co]):
    def __init__(
        self,
        original_children: list[discord.ui.Item[Any]],
        embeds: Sequence[discord.Embed] | None = None,
        byte_obj: io.BytesIO | None = None,
        row: int = 4,
    ) -> None:
        super().__init__(emoji=emojis.BACK, row=row)
        self.original_children = original_children.copy()
        self.embeds = embeds
        self.byte_obj = byte_obj

        self.view: V_co

    async def callback(self, i: Interaction) -> Any:
        self.view.clear_items()
        for item in self.original_children:
            if isinstance(item, Button | Select):
                self.view.add_item(item, translate=False)

        kwargs: dict[str, Any] = {"view": self.view}
        if self.embeds is not None:
            kwargs["embeds"] = self.embeds

        if self.byte_obj is not None:
            self.byte_obj.seek(0)

            original_image = None
            for embed in self.embeds or []:
                original_image = (
                    clean_url(embed.image.url).split("/")[-1]
                    if embed.image.url is not None
                    else None
                )
                if original_image is not None:
                    embed.set_image(url=f"attachment://{original_image}")

            original_image = original_image or "image.png"
            kwargs["attachments"] = [discord.File(self.byte_obj, filename=original_image)]

        await i.response.edit_message(**kwargs)


class ToggleButton(Button, Generic[V_co]):
    def __init__(self, current_toggle: bool, toggle_label: LocaleStr, **kwargs: Any) -> None:
        self.current_toggle = current_toggle
        self.toggle_label = toggle_label
        kwargs["row"] = kwargs.get("row", 1)
        super().__init__(
            style=self._get_style(),
            label=self._get_label(),
            emoji=emojis.TOGGLE_EMOJIS[current_toggle],
            **kwargs,
        )

        self.view: V_co

    def _get_label(self) -> LocaleStr:
        return LocaleStr(
            custom_str="{toggle_label}: {status_str}",
            toggle_label=self.toggle_label,
            status_str=self._get_status_str(),
        )

    def _get_style(self) -> discord.ButtonStyle:
        return discord.ButtonStyle.green if self.current_toggle else discord.ButtonStyle.gray

    def _get_status_str(self) -> LocaleStr:
        return (
            LocaleStr(key="on_button_label")
            if self.current_toggle
            else LocaleStr(key="off_button_label")
        )

    def update_style(self) -> None:
        self.style = self._get_style()
        self.locale_str_label = self._get_label()
        self.emoji = emojis.TOGGLE_EMOJIS[self.current_toggle]

        self.translate(self.view.locale)

    async def callback(self, i: Interaction, *, edit: bool = True, **kwargs: Any) -> Any:
        self.current_toggle = not self.current_toggle
        self.update_style()
        if edit:
            await i.response.edit_message(view=self.view, **kwargs)


class SelectOption(discord.SelectOption):
    def __init__(
        self,
        *,
        label: LocaleStr | str,
        value: str,
        description: LocaleStr | str | None = None,
        emoji: str | None = None,
        default: bool = False,
    ) -> None:
        super().__init__(
            label=label if isinstance(label, str) else label.identifier,
            value=value,
            emoji=emoji,
            default=default,
        )
        self.locale_str_label = label
        self.locale_str_description = description


class Select(discord.ui.Select, Generic[V_co]):
    def __init__(
        self,
        *,
        custom_id: str = MISSING,
        placeholder: LocaleStr | str | None = None,
        min_values: int = 1,
        max_values: int = 1,
        options: list[SelectOption],
        disabled: bool = False,
        row: int | None = None,
    ) -> None:
        if not options:
            options = [SelectOption(label="placeholder", value="0")]
            disabled = True
        super().__init__(
            custom_id=custom_id,
            min_values=min_values,
            max_values=max_values,
            options=options,  # pyright: ignore[reportArgumentType]
            disabled=disabled,
            row=row,
        )
        self.locale_str_placeholder = placeholder

        self.original_placeholder: str | None = None
        self.original_options: list[SelectOption] | None = None
        self.original_disabled: bool | None = None
        self.original_max_values: int | None = None
        self.original_min_values: int | None = None

        self.view: V_co

    @property
    def options(self) -> list[SelectOption]:
        return self._underlying.options  # pyright: ignore [reportReturnType]

    @options.setter
    def options(self, value: list[SelectOption]) -> None:
        if not value:
            value = [SelectOption(label="placeholder", value="0")]
            self.disabled = True
        self._underlying.options = value  # pyright: ignore [reportAttributeAccessIssue]

    def translate(self, locale: Locale) -> None:
        if self.locale_str_placeholder:
            self.placeholder = translator.translate(self.locale_str_placeholder, locale)[:100]
        for option in self.options:
            # NOTE: This is a workaround for a bug(?) in discord.py where options somehow get converted to discord.components.SelectOption internally
            if not isinstance(option, SelectOption):  # pyright: ignore[reportUnnecessaryIsInstance]
                continue

            option.label = translator.translate(option.locale_str_label, locale)[:100]
            option.value = option.value[:100]

            if option.locale_str_description:
                option.description = translator.translate(option.locale_str_description, locale)[
                    :100
                ]

    async def set_loading_state(self, i: Interaction) -> None:
        self.original_options = self.options.copy()
        self.original_disabled = self.disabled
        self.original_placeholder = self.placeholder[:] if self.placeholder else None
        self.original_max_values = self.max_values
        self.original_min_values = self.min_values

        self.view.disable_items()

        self.options = [
            SelectOption(
                label=translator.translate(LocaleStr(key="loading_text"), self.view.locale),
                value="loading",
                default=True,
                emoji=emojis.LOADING,
            )
        ]
        self.disabled = True
        self.max_values = 1
        self.min_values = 1

        await self.view.absolute_edit(i, view=self.view)

    async def unset_loading_state(self, i: Interaction, **kwargs: Any) -> None:
        if (
            not self.original_options
            or self.original_disabled is None
            or self.original_max_values is None
            or self.original_min_values is None
        ):
            msg = "unset_loading_state called before set_loading_state"
            raise RuntimeError(msg)

        self.view.enable_items()

        self.options = self.original_options
        self.disabled = self.original_disabled
        self.placeholder = self.original_placeholder
        self.max_values = self.original_max_values
        self.min_values = self.original_min_values

        self.update_options_defaults()

        await self.view.absolute_edit(i, view=self.view, **kwargs)

    def update_options_defaults(self, *, values: list[str] | None = None) -> None:
        values = values or self.values
        for option in self.options:
            option.default = option.value in values

    def reset_options_defaults(self) -> None:
        for option in self.options:
            option.default = False


NEXT_PAGE = SelectOption(
    label=LocaleStr(key="next_page_option_label"), value="next_page", emoji=emojis.FORWARD
)
PREV_PAGE = SelectOption(
    label=LocaleStr(key="prev_page_option_label"), value="prev_page", emoji=emojis.BACK
)


class PaginatorSelect(Select, Generic[V_co]):
    def __init__(self, options: list[SelectOption], **kwargs: Any) -> None:
        if not options:
            options = [SelectOption(label="placeholder", value="0")]
            kwargs["disabled"] = True

        self.options_before_split = options
        self.page_index = 0
        self._max_values = kwargs.get("max_values", 1)
        super().__init__(options=self.process_options(), **kwargs)

        self.view: V_co

    def __repr__(self) -> str:
        return (
            f"<{self.__class__.__name__} custom_id={self.custom_id!r} page_index={self.page_index}>"
        )

    @staticmethod
    def remove_duplicate_options(
        options: list[SelectOption], existing_options: list[SelectOption]
    ) -> list[SelectOption]:
        existing_values = {option.value for option in existing_options}
        return [option for option in options if option.value not in existing_values]

    def process_options(self) -> list[SelectOption]:
        split_options = split_list_to_chunks(self.options_before_split, 23 - self._max_values)
        if not split_options:
            return []

        try:
            values = self.values
        except AttributeError:
            values = []

        selected_options = [
            option
            for option in self.options_before_split
            if option.value in values and option.value not in {NEXT_PAGE.value, PREV_PAGE.value}
        ]

        try:
            split_options[self.page_index]
        except IndexError:
            self.page_index = 0

        if self.page_index == 0:
            if len(split_options) == 1:
                return split_options[0]
            selected_options = self.remove_duplicate_options(selected_options, split_options[0])
            return [NEXT_PAGE] + selected_options + split_options[0]

        if self.page_index == len(split_options) - 1:
            selected_options = self.remove_duplicate_options(selected_options, split_options[-1])
            return [PREV_PAGE] + selected_options + split_options[-1]

        selected_options = self.remove_duplicate_options(
            selected_options, split_options[self.page_index]
        )
        return [PREV_PAGE] + [NEXT_PAGE] + selected_options + split_options[self.page_index]

    def set_page_based_on_value(self, value: str) -> None:
        split_options = split_list_to_chunks(self.options_before_split, 23)

        for i, options in enumerate(split_options):
            if value in [option.value for option in options]:
                self.page_index = i
                break

    def update_page(self) -> bool:
        changed = False
        if "next_page" in self.values:
            changed = True
            self.page_index += 1
            self.options = self.process_options()
        elif "prev_page" in self.values:
            changed = True
            self.page_index -= 1
            self.options = self.process_options()

        if changed:
            for option in self.options:
                option.default = False
            self.update_options_defaults()

            for option in self.options:
                if option.value in {PREV_PAGE.value, NEXT_PAGE.value}:
                    option.default = False

            self.max_values = min(self._max_values, len(self.options))

        self.translate(self.view.locale)
        return changed


class TextInput(discord.ui.TextInput):
    def __init__(
        self,
        *,
        label: LocaleStr | str,
        style: discord.TextStyle = discord.TextStyle.short,
        custom_id: str = MISSING,
        placeholder: LocaleStr | str | None = None,
        default: LocaleStr | str | None = None,
        required: bool = True,
        min_length: int | None = None,
        max_length: int | None = None,
        row: int | None = None,
        is_digit: bool = False,
        max_value: int | None = None,
        min_value: int | None = None,
        is_bool: bool = False,
    ) -> None:
        super().__init__(
            label=label if isinstance(label, str) else "#NoTrans",
            style=style,
            custom_id=custom_id,
            required=required,
            min_length=min_length,
            max_length=max_length,
            row=row,
        )
        self.locale_str_label = label
        self.locale_str_placeholder = placeholder
        self.locale_str_default = default

        self.is_digit = is_digit
        self.max_value = max_value
        self.min_value = min_value
        self.is_bool = is_bool


class Modal(discord.ui.Modal):
    def __init__(self, *, title: LocaleStr | str, custom_id: str = MISSING) -> None:
        super().__init__(
            title=title if isinstance(title, str) else "#NoTrans",
            timeout=600,
            custom_id=self.__class__.__name__ if custom_id is MISSING else custom_id,
        )
        self.locale_str_title = title

    async def on_error(self, i: Interaction, error: Exception) -> None:
        locale = await get_locale(i)
        embed, recognized = get_error_embed(error, locale)
        if not recognized:
            i.client.capture_exception(error)

        if not i.response.is_done():
            await i.response.send_message(embed=embed, ephemeral=True)
        else:
            await i.followup.send(embed=embed, ephemeral=True)

    async def on_submit(self, i: Interaction) -> None:
        self.validate_inputs()
        with contextlib.suppress(discord.NotFound):
            await i.response.defer()
        self.stop()

    def translate(self, locale: Locale) -> None:
        self.title = translator.translate(self.locale_str_title, locale, max_length=45)

        for item in self.children:
            if isinstance(item, TextInput):
                item.label = translator.translate(item.locale_str_label, locale)

                if item.is_digit:
                    item.placeholder = f"({item.min_value} ~ {item.max_value})"
                elif item.is_bool:
                    item.placeholder = "0/1"

                if item.locale_str_placeholder:
                    item.placeholder = translator.translate(item.locale_str_placeholder, locale)
                if item.locale_str_default:
                    item.default = translator.translate(item.locale_str_default, locale)

    def validate_inputs(self) -> None:
        """Validates all TextInput children of the modal. Raises InvalidInputError if any input is invalid."""
        for item in self.children:
            if isinstance(item, TextInput) and item.is_digit:
                try:
                    value = int(item.value)
                except ValueError as e:
                    raise InvalidInputError(
                        LocaleStr(key="invalid_input.input_needs_to_be_int", input=item.label)
                    ) from e
                if item.max_value is not None and value > item.max_value:
                    raise InvalidInputError(
                        LocaleStr(
                            key="invalid_input.input_out_of_range.max_value",
                            input=item.label,
                            max_value=item.max_value,
                        )
                    )
                if item.min_value is not None and value < item.min_value:
                    raise InvalidInputError(
                        LocaleStr(
                            key="invalid_input.input_out_of_range.min_value",
                            min_value=item.min_value,
                            input=item.label,
                        )
                    )
            elif isinstance(item, TextInput) and item.is_bool:
                if item.value not in {"0", "1"}:
                    raise InvalidInputError(
                        LocaleStr(key="invalid_input.input_needs_to_be_bool", input=item.label)
                    )


class ToggleUIButton(Button, Generic[V_co]):
    def __init__(self, *, row: int = 4) -> None:
        super().__init__(
            style=discord.ButtonStyle.gray,
            label=LocaleStr(key="hide_ui_button_label"),
            emoji=emojis.HIDE_UI,
            row=row,
        )
        self._items: Sequence[Button | Select] = []
        self._mode: Literal["show", "hide"] = "hide"
        self.view: V_co

    def _set_style(self) -> None:
        self.emoji = emojis.HIDE_UI if self._mode == "hide" else emojis.SHOW_UI
        self.style = (
            discord.ButtonStyle.gray if self._mode == "hide" else discord.ButtonStyle.blurple
        )
        self.locale_str_label = LocaleStr(
            key="hide_ui_button_label" if self._mode == "hide" else "show_ui_button_label"
        )

    async def callback(self, i: Interaction) -> None:
        message = i.message
        if message is None:
            return

        if self._mode == "hide":
            children = self.view.children.copy()
            children.remove(self)
            self._items = children  # pyright: ignore[reportAttributeAccessIssue]
            self.view.clear_items()

            self._mode = "show"
            self._set_style()
            self.view.add_item(self)
        else:
            self.view.clear_items()
            self.view.add_items(self._items)

            self._mode = "hide"
            self._set_style()
            self.view.add_item(self)

        await i.response.edit_message(view=self.view)
