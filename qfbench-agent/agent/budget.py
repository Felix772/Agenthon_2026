"""One local CLI clock, capped by the task card and explicit configuration.

These are participant limits. No organizer stage deadline or roster size is
available to this program; container creation and image pulls occur before it.
"""
from dataclasses import dataclass
import math
import os
from pathlib import Path
import stat
import tomllib

DEFAULT_CAP = 360.0
LOCAL_DOCKER_LAUNCH_ALLOWANCE = 15.0
CARD_SIZE_LIMIT = 256000


def positive_seconds(value, label):
    if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
        raise ValueError(f"{label} must be positive and finite")
    return float(value)


def configured_cap():
    try:
        value = float(os.environ.get("AGENT_SOFT_TIMEOUT_SEC", str(DEFAULT_CAP)))
    except ValueError:
        raise ValueError("AGENT_SOFT_TIMEOUT_SEC must be positive and finite") from None
    return positive_seconds(value, "AGENT_SOFT_TIMEOUT_SEC")


def timeout_from_card(card):
    agent = card.get("agent", {})
    if not isinstance(agent, dict):
        raise ValueError("card.toml must provide a positive [agent].timeout_sec")
    return positive_seconds(agent.get("timeout_sec"), "card.toml [agent].timeout_sec")


def read_card_timeout(root: Path):
    # The supervisor reads only this bounded, regular metadata file. Discovery,
    # input inspection and numerical imports remain in its guarded child.
    path = root / "card.toml"
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_size > CARD_SIZE_LIMIT:
        raise ValueError("card.toml must be a regular file within the size limit")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    descriptor = os.open(path, flags)
    try:
        opened = os.fstat(descriptor)
        if (not stat.S_ISREG(opened.st_mode) or opened.st_size > CARD_SIZE_LIMIT
                or (info.st_dev, info.st_ino) != (opened.st_dev, opened.st_ino)):
            raise ValueError("card.toml changed or exceeds the regular-file size limit")
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            raw = handle.read(CARD_SIZE_LIMIT + 1)
    finally:
        os.close(descriptor)
    if len(raw) > CARD_SIZE_LIMIT:
        raise ValueError("card.toml exceeds the size limit")
    return timeout_from_card(tomllib.loads(raw.decode("utf-8")))


@dataclass(frozen=True)
class Budget:
    card_timeout: float
    cap: float

    @property
    def ceiling(self):
        return min(self.card_timeout, self.cap)

    @property
    def exit_reserve(self):
        # Leave room for process reaping, optional baseline recovery and final
        # output permissions after the parent's wait expires.
        return min(8.0, self.ceiling * .05)

    @property
    def watchdog_gap(self):
        return min(1.0, self.ceiling * .01)

    @property
    def publication_reserve(self):
        return min(15.0, self.ceiling * .05)

    def parent_deadline(self, started):
        return started + self.ceiling - self.exit_reserve

    def child_deadline(self, started):
        return self.parent_deadline(started) - self.watchdog_gap

    def work_deadline(self, started):
        return self.child_deadline(started) - self.publication_reserve


def budget_for(card_timeout, cap=None):
    return Budget(positive_seconds(card_timeout, "card.toml [agent].timeout_sec"),
                  configured_cap() if cap is None else positive_seconds(cap, "Soft timeout"))
