import os
import sys
import traceback
import tempfile
from typing import Callable, Optional
from io import UnsupportedOperation
from logging import getLogger
from urllib.parse import quote

from conda import CondaError
from conda.common.compat import on_win
from conda.common.url import urlparse


log = getLogger(f"conda.{__name__}")


def escape_channel_url(channel):
    if channel.startswith("file:"):
        if "%" in channel:  # it's escaped already
            return channel
        if on_win:
            channel = channel.replace("\\", "/")
    parts = urlparse(channel)
    if parts.scheme:
        components = parts.path.split("/")
        if on_win:
            if parts.netloc and len(parts.netloc) == 2 and parts.netloc[1] == ":":
                # with absolute paths (e.g. C:/something), C:, D:, etc might get parsed as netloc
                path = "/".join([parts.netloc] + [quote(p) for p in components])
                parts = parts.replace(netloc="")
            else:
                path = "/".join(components[:2] + [quote(p) for p in components[2:]])
        else:
            path = "/".join([quote(p) for p in components])
        parts = parts.replace(path=path)
        return str(parts)
    return channel
