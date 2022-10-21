import setuptools
import re

version = ""
with open("discord/ext/modmail_utils/__init__.py") as f:
    version = re.search(r'^__version__\s*=\s*[\'"]([^\'"]*)[\'"]', f.read(), re.MULTILINE).group(1)

if not version:
    raise RuntimeError("version is not set.")

with open("README.md", "r", encoding="utf-8") as fh:
    long_description = fh.read()

setuptools.setup(
    name="modmail-utils",
    url="https://github.com/Jerrie-Aries/modmail-plugins",
    packages=["discord.ext.modmail_utils"],
    version=version,
    author="Jerrie-Aries",
    description="Extended Utils for Modmail plugins.",
    long_description=long_description,
    long_description_content_type="text/markdown",
    install_requires=["discord.py>=2.0.0"],
    project_urls={},
    license="AGPL",
)
