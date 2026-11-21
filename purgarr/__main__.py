import logging
import sys

import discord

from . import __version__
from .bot import Purgarr
from .config import Config, ConfigError


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    log = logging.getLogger("purgarr")
    log.info("Purgarr %s starting", __version__)

    try:
        config = Config.from_env()
    except ConfigError as exc:
        log.error("Configuration problem: %s", exc)
        sys.exit(1)

    if config.dry_run:
        log.warning("DRY_RUN is on: approved requests are marked deleted but nothing is removed")

    try:
        Purgarr(config).run(config.discord_token, log_handler=None)
    except discord.LoginFailure:
        log.error("Discord rejected DISCORD_TOKEN. Reset the token in the Developer Portal and update it.")
        sys.exit(1)


if __name__ == "__main__":
    main()
