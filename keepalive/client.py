from __future__ import annotations

from typing import Any, Dict, Optional, TYPE_CHECKING

from core.models import getLogger

if TYPE_CHECKING:
    from aiohttp import ClientSession
    from bot import ModmailBot
    from .keepalive import KeepAlive


logger = getLogger(__name__)


class UptimeRobotMonitor:
    """
    UptimeRobot monitor model.
    """

    def __init__(self, client: UptimeRobotAPIClient, *, data: Dict[str, Any]):
        self.client: UptimeRobotAPIClient = client
        self.id: int = data.pop("id")
        self.friendly_name: str = data.pop("friendly_name")
        self.url: str = data.pop("url")
        self.raw_type: int = data.pop("type")
        self.interval: int = data.pop("interval")
        self.raw_status: int = data.pop("status")

    async def refresh(self) -> None:
        payload = {
            "monitors": self.id,
        }
        data = await self.client.request(self.client.GET_MONITOR, payload=payload)
        try:
            monitor = data["monitors"][0]
        except (IndexError, KeyError):
            logger.error(f"UptimeRobot monitor ID '{self.id} does not exist.")
        else:
            self.friendly_name = monitor.pop("friendly_name")
            self.url = monitor.pop("url")
            self.raw_type = monitor.pop("type")
            self.interval = monitor.pop("interval")
            self.raw_status = monitor.pop("status")

    @property
    def status(self) -> str:
        status_map = {
            0: "Paused",
            1: "Not checked yet",
            2: "Up",
            8: "Seems down",
            9: "Down",
        }
        return status_map[self.raw_status]

    @property
    def type(self) -> str:
        type_map = {
            1: "HTTP(s)",
            2: "Keyword",
            3: "Ping",
            4: "Port",
            5: "Heartbeat",
        }
        return type_map[self.raw_type]


class UptimeRobotAPIClient:
    """
    Represents UptimeRobot API client manager. This client will be used to interact with the
    UptimeRobot API.
    The API key is required for any of the methods here to work.
    """

    BASE: str = "https://api.uptimerobot.com/v2"
    GET_MONITOR: str = BASE + "/getMonitors"
    NEW_MONITOR: str = BASE + "/newMonitor"
    EDIT_MONITOR: str = BASE + "/editMonitor"

    # default config
    monitor_type: int = 1
    monitor_interval: int = 300
    monitor_timeout: int = 60

    def __init__(self, cog: KeepAlive, *, api_key: str):
        """
        Parameters
        -----------
        cog : KeepAlive
            The KeepAlive cog.
        api_key : str
            The UptimeRobot API key.
        """
        if not api_key:
            raise ValueError(f"api_key is required to instantiate {type(self).__name__} class.")
        self.cog: KeepAlive = cog
        self.bot: ModmailBot = cog.bot
        self.session: ClientSession = cog.bot.session
        self.monitor: Optional[UptimeRobotMonitor] = None
        self.api_key: str = api_key
        self.headers: Dict[str, str] = {
            "Content-Type": "application/x-www-form-urlencoded",
            "Cache-Control": "no-cache",
        }

    async def request(self, url: str, *, payload: Dict[str, Any]) -> Dict[str, Any]:
        payload["api_key"] = self.api_key
        # we only use one request method: "POST" to interact with UptimeRobot API
        async with self.session.post(url, data=payload, headers=self.headers) as resp:
            data = await resp.json()
        # TODO: Check error data and stuff
        return data

    async def check_monitor(self) -> None:
        payload = {
            "search": self.cog.keep_alive.url,
            "limit": 5,
        }
        data = await self.request(self.GET_MONITOR, payload=payload)
        error = data.get("error")
        if error:
            message = error.get("message") or "Unknown"
            logger.error("Unable to check UptimeRobot monitor.")
            logger.error(f"Error: {message}")
            return
        monitors = data.get("monitors")
        if not monitors:
            logger.error("UptimeRobot monitor has never been set.")
            monitor = await self.new_monitor()
        else:
            # just get the first one
            monitor = monitors[0]
            to_edit = {}
            if not 300 <= monitor["interval"] <= 600:
                # set to 5 minutes
                to_edit["interval"] = self.monitor_interval
            if monitor["status"] == 0:
                to_edit["status"] = 1
            # TODO: "type" cannot be edited, the suggested solution is delete the current monitor
            # and create a new one
            if monitor["type"] != self.monitor_type:
                pass

            if to_edit:
                to_edit["id"] = monitor["id"]
                monitor.update(await self.edit_monitor(payload=to_edit))

        self.monitor = UptimeRobotMonitor(self, data=monitor)

    async def new_monitor(self) -> Dict[str, Any]:
        default_data = {
            "friendly_name": f"{self.cog.qualified_name} - {self.bot.user.name}",
            "url": self.cog.keep_alive.url,
            "type": self.monitor_type,
            "interval": self.monitor_interval,
            "timeout": self.monitor_timeout,
        }
        logger.info("Creating a new UptimeRobot monitor.")
        data = await self.request(self.NEW_MONITOR, payload={k: v for k, v in default_data.items()})
        monitor = data.get("monitor")
        if not monitor:
            raise ValueError("Failed to create a new UptimeRobot monitor.")
        # only "id" and "status" was returned from response
        default_data.update(monitor)
        return default_data

    async def edit_monitor(self, *, payload: Dict[str, Any]) -> Dict[str, Any]:
        logger.info("Editing UptimeRobot monitor.")
        # only "id" will be returned from response
        data = await self.request(self.EDIT_MONITOR, payload={k: v for k, v in payload.items()})
        monitor = data.get("monitor")
        if not monitor:
            raise ValueError("Failed to edit the UptimeRobot monitor.")
        payload.update(monitor)
        return payload
