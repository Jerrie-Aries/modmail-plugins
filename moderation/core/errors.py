from discord.ext import commands


class BanEntryNotFound(commands.BadArgument):
    def __init__(self):
        super().__init__("This user is not banned.")
