import setuptools
import re

version = ""
with open("discord/ext/modmail_utils/__init__.py") as f:
    version = re.search(r'^__version__\s*=\s*[\'"]([^\'"]*)[\'"]', f.read(), re.MULTILINE).group(1)

if not version:
    raise RuntimeError("version is not set.")

packages = ["discord.ext.modmail_utils"]
install_requires = []

setuptools.setup(
    name="modmail-utils",
    url="https://github.com/Jerrie-Aries/modmail-plugins",
    packages=packages,
    version=version,
    author="Jerrie-Aries",
    description="Extended Utils for Modmail plugins.",
    long_description_content_type="text/markdown",
    install_requires=install_requires,
    project_urls={},
    license="AGPL",
)
