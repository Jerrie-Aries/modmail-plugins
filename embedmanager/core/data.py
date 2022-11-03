from typing import Any, Dict

from discord import TextStyle
from discord.ext.modmail_utils import Limit


_short_length = 256


DESCRIPTIONS = {
    "title": ["**Title:**", "- `Title`: The title of embed.", "- `Embed URL`: The URL of embed.\n"],
    "author": [
        "**Author:**",
        f"- `Name`: Name of author. Must be {Limit.embed_author} or fewer in length.",
        "- `Icon URL`: URL of author icon.",
        "- `Author URL`: URL of author.\n",
    ],
    "body": [
        "**Body:**",
        "- `Description`: Description of embed.",
        "- `Thumbnail URL`: URL of thumbnail image (shown at top right).",
        "- `Image URL`: URL of embed image (shown at bottom).\n",
    ],
    "footer": [
        "**Footer:**",
        f"- `Text`: The text shown on footer (can be up to {Limit.embed_footer} characters).",
        "- `Icon URL`: URL of footer icon.\n",
    ],
    "color": [
        "**Color:**",
        "- `Value`: Color code of the embed.",
        "The following formats are accepted:",
        "\t- `0x<hex>`\n\t- `#<hex>`\n\t- `0x#<hex>`\n\t- `rgb(<number>, <number>, <number>)`",
        "Like CSS, `<number>` can be either 0-255 or 0-100% and `<hex>` can be either a 6 digit hex number or a 3 digit hex shortcut (e.g. #fff).\n",
    ],
    "fields": [
        "**Fields:**",
        f"- `Name`: Name of the field. Can be up to {Limit.embed_field_name} characters.",
        f"- `Value`: Value of the field, can be up to {Limit.embed_field_value} characters.",
        "- `Inline`: Whether or not this field should display inline.\n",
        "Click `Add Field` to add a new field, or `Clear Fields` to clear all fields, if any.",
        f"Embed fields can be added up to {Limit.embed_fields}.\n",
    ],
    "note": [
        "__**Notes:**__",
        f"- The combine sum of characters in embeds in a single message must not exceed {Limit.embed} characters.\n",
    ],
}

SHORT_DESCRIPTIONS = {
    "title": "The title of embed including URL.",
    "author": "The author of the embed.",
    "body": "Description, thumbnail and image URLs.",
    "footer": "The footer text and/or icon of the embed.",
    "color": "Embed's color.",
    "fields": "Add or remove fields.",
}

INPUT_DATA: Dict[str, Any] = {
    "title": {
        "title": {
            "label": "Title",
            "max_length": _short_length,
        },
        "url": {
            "label": "Embed URL",
            "max_length": _short_length,
            "required": False,
        },
    },
    "author": {
        "name": {
            "label": "Name",
            "max_length": _short_length,
        },
        "icon_url": {
            "label": "Icon URL",
            "max_length": _short_length,
            "required": False,
        },
        "url": {
            "label": "Author URL",
            "max_length": _short_length,
            "required": False,
        },
    },
    "body": {
        "description": {
            "label": "Description",
            "style": TextStyle.long,
            "max_length": Limit.text_input_max,
        },
        "thumbnail": {
            "label": "Thumbnail URL",
            "max_length": _short_length,
            "required": False,
        },
        "image": {
            "label": "Image URL",
            "max_length": _short_length,
            "required": False,
        },
    },
    "color": {
        "value": {
            "label": "Value",
            "placeholder": "#ffffff",
            "max_length": 32,
        },
    },
    "footer": {
        "text": {
            "label": "Text",
            "max_length": Limit.embed_footer,
        },
        "icon_url": {
            "label": "Icon URL",
            "max_length": _short_length,
            "required": False,
        },
    },
    "fields": {
        "name": {
            "label": "Name",
            "max_length": Limit.embed_field_name,
        },
        "value": {
            "label": "Value",
            "max_length": Limit.embed_field_value,
            "style": TextStyle.long,
        },
        "inline": {
            "label": "Inline",
            "max_length": 5,
            "required": False,
        },
    },
}

JSON_EXAMPLE = """
{
    "title": "JSON Example",
    "description": "This embed is an example to show various features that can be used in a rich embed.",
    "url": "https://example.com",
    "color": 2616205,
    "fields": [
        {
            "name": "Field 1",
            "value": "This field is not within a line."
       },
        {
            "name": "Field 2",
            "value": "This is also not inline."
        },
        {
            "name": "Field 3",
            "value": "This field will be inline.",
            "inline": true
        },
        {
            "name": "Field 4",
            "value": "This field is also within a line.",
            "inline": true
        }
    ],
    "author": {
            "name": "Author Name",
            "url": "https://example.com",
            "icon_url": "https://link.to/some/image.png"
    },
    "footer": {
        "text": "Footer text",
        "icon_url": "https://link.to/some/image.png"
    },
    "image": {
        "url": "https://link.to/some/image.png"
    },
    "thumbnail": {
        "url": "https://link.to/some/image.png"
    }
}
"""
