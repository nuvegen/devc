"""Provider-Auswahl (SPEC 6.1 `platform`, 8.2)."""

import os
import shutil

from ..errors import EngineError


def get_provider(config):
    platform = config.platform
    if platform == "auto":
        if shutil.which("lxc") is None:
            raise EngineError("lxc wurde nicht im PATH gefunden; LXD installieren oder platform setzen")
        platform = "lxd"
    if platform == "lxd":
        from .lxd import LxdProvider

        lxd = config.lxd
        return LxdProvider(
            project=lxd["project"],
            pool=lxd["storage_pool"],
            network=lxd["network"],
            shared_mode=lxd["shared_mode"],
            allowed_roots=config.allowed_roots,
            uid=os.getuid(),
            gid=os.getgid(),
        )
    raise EngineError(f"Plattform wird nicht unterstützt: {platform}")
