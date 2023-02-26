[![User](https://img.shields.io/badge/Modmail%20Plugins-by%20Jerrie-black.svg?style=popout&logo=github&logoColor=white)](https://github.com/Jerrie-Aries/)
[![Made-with-Python](https://img.shields.io/badge/Made%20with-Python%203.8%20|%203.9-blue.svg?style=popout&logo=python&logoColor=yellow)](https://www.python.org/)
[![Database-MongoDB](https://img.shields.io/badge/Database-MongoDB-%234ea94b.svg?style=popout&logo=mongodb&logoColor=white)](https://www.mongodb.com/cloud)
[![Code-Style](https://img.shields.io/badge/Code%20Style-Black-000000.svg)](https://github.com/python/black)
[![GitHub-License](https://badgen.net/github/license/Jerrie-Aries/modmail-plugins?label=License)](https://github.com/Jerrie-Aries/modmail-plugins/blob/master/LICENSE)


## Modmail Plugins

Custom plugins for [Modmail bot](https://github.com/kyb3r/modmail).

Checkout the [wiki](https://github.com/Jerrie-Aries/modmail-plugins/wiki) for latest news and additional info.

__**Important:**__
Plugins with `*` mark after their names in the table below require `Extended Utils` plugin to work.
It must be installed before the ones with `*` mark, otherwise you have to reload those plugins.
To install it, use command:
```
?plugin add Jerrie-Aries/modmail-plugins/utils
```

## How to install

Use the format of `?plugin add Jerrie-Aries/modmail-plugins/<plugin_name><@branch>` where `<plugin_name>` (use without `<` and `>`) is the name of the plugin file. `<@branch>` is optional, defaults to `master`.

Example:
```
?plugin add Jerrie-Aries/modmail-plugins/trivia
```
to add the trivia plugin.


## List of plugins
| Name | Name for command | Description |
| --- | --- | --- |
| Announcement | `announcement` | Post announcements on your server with this plugin. Publishing announcements to subscribed channels is also supported. |
| Embed Manager`*` | `embedmanager` | Manage, post, edit, store embeds with this plugin. This plugin is a modified version of `embedutils` cog made by [PhenoM4n4n](https://github.com/phenom4n4n). Any credits must go to original developer of this cog. Original repository can be found [here](https://github.com/phenom4n4n/phen-cogs/tree/master/embedutils). |
| Extended Utils | `utils` | Entended utils from various sources to work with other plugins. |
| Giveaway | `giveaway` | Host giveaways on your server with this plugin. The source code was from [officialpiyush/modmail-plugins/giveaway](https://github.com/officialpiyush/modmail-plugins/tree/master/giveaway). |
| Invites`*` | `invites` | Checks which invite is used when someone joins the server, and the log embed will be posted in the designated channel. |
| Keep Alive | `keepalive` | Hosting on Replit? Keep Alive plugin is specifically for those who host their bots on Replit. This plugin will keep your bots up using [UptimeRobot](https://uptimerobot.com/) service to ping the web server created by the plugin. Read [Keep Alive plugin wiki](https://github.com/Jerrie-Aries/modmail-plugins/wiki/Keep-Alive-plugin-guide) for more info. |
| Log Viewer | `logviewer` | Start the logviewer server with plugin? Yes. This plugin is a modified version of kyb3r's [logviewer](https://github.com/kyb3r/logviewer). Any credits must go to original developers. If you want to use this plugin, make sure to read the [wiki](https://github.com/Jerrie-Aries/modmail-plugins/wiki/Log-Viewer-plugin) first. |
| Moderation`*` | `moderation` | Moderate your server with moderation plugin. This plugin supports kick, ban, add/remove roles, mute and temp mute, change nicknames, and many more. |
| Role Manager`*` | `rolemanager` | Manage roles on your server. This plugin includes Auto Role, Mass Roling, Reaction Roles, and Targeter (a tool to search members that match the given args). This plugin is a combination and modified version of `roleutils` cog made by [PhenoM4n4n](https://github.com/phenom4n4n), and `targeter` cog made by [NeuroAssassin](https://github.com/NeuroAssassin). |
| RTFM | `rtfm` | RTFM commands for developers to easily fetch `discord.py` and `python` documentation links. Converted from [RoboDanny](https://github.com/Rapptz/RoboDanny). |
| Support Utility`*` | `supportutils` | Addtional tools to enhance Support members' experience. Contact Modmail using button or dropdown. Users submit their feedback after the ticket is closed. |
| Trivia`*` | `trivia` | Play trivia with friends on your server. This is a modified version of `cogs trivia` of [Red-DiscordBot](https://github.com/Cog-Creators/Red-DiscordBot). I just made it compatible with Modmail bot. Any credits must go to original developer. You can find the source [here](https://github.com/Cog-Creators/Red-DiscordBot/tree/V3/develop/redbot/cogs/trivia). |

`*` : Requires *Extended Utils* plugin to work.


## Reporting bugs and errors
If you encounter any bugs or errors, just open a new issue [here](https://github.com/Jerrie-Aries/modmail-plugins/issues/new) and report it.
Or you can report it on Modmail's [support](https://discord.gg/zmdYe3ZVHG) server in [`#plugin-support`](https://discord.com/channels/1079074933008781362/1079077960876838992) channel.
