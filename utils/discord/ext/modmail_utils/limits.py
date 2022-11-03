__all__ = ("Limit",)


class Limit:
    """
    Store the length limit of various objects.

    Since these are subjected to change, putting them in one place to be managed
    would make things easier.
    """

    # https://discord.com/developers/docs/resources/channel#embed-object-embed-limits
    embed = 6000
    embed_description = 4096
    embed_title = 256
    embed_author = 256
    embed_footer = 2048
    embed_field_name = 256
    embed_field_value = 1024
    embed_fields = 25

    # https://discord.com/developers/docs/interactions/message-components#button-object-button-structure
    button_label = 80

    # https://discord.com/developers/docs/interactions/message-components#select-menu-object-select-menu-structure
    select_options = 25
    select_placeholder = 150
    # https://discord.com/developers/docs/interactions/message-components#select-menu-object-select-option-structure
    select_label = 100
    select_description = 100

    # https://discord.com/developers/docs/interactions/message-components#text-inputs-text-input-structure
    text_input_label = 45
    text_input_placeholder = 100
    text_input_max = 4000
