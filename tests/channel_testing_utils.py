import pathlib
import os
import socket
import sys
import subprocess
from typing import Tuple

import pytest
from xprocess import ProcessStarter

from conda.testing.integration import _get_temp_prefix, run_command


def _mock_http_server(xprocess, name, port, auth="none", user=None, password=None, token=None):
    """
    Adapted from
    https://github.com/mamba-org/powerloader/blob/effe2b7e1/test/helpers.py#L11
    """
    curdir = pathlib.Path(__file__).parent
    print("Starting mock_http_server")

    class Starter(ProcessStarter):

        pattern = f"Server started at localhost:{port}"
        terminate_on_interrupt = True
        timeout = 10
        args = [
            sys.executable,
            "-u",  # unbuffered
            str(curdir / "_reposerver.py"),
            "-d",
            str(curdir / "data" / "mamba_repo"),
            "--port",
            str(port),
        ]
        if auth == "token":
            assert token
            args += ["--token", token]
        elif auth:
            args += ["--auth", auth]
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        if user and password:
            env["TESTPWD"] = f"{user}:{password}"

        def startup_check(self):
            s = socket.socket()
            address = "localhost"
            error = False
            try:
                s.connect((address, port))
            except Exception as e:
                print("something's wrong with %s:%d. Exception is %s" % (address, port, e))
                error = True
            finally:
                s.close()

            return not error

    logfile = xprocess.ensure(name, Starter)

    if user and password:
        yield f"http://{user}:{password}@localhost:{port}"
    elif token:
        yield f"http://localhost:{port}/t/{token}"
    else:
        yield f"http://localhost:{port}"

    xprocess.getinfo(name).terminate()


@pytest.fixture
def http_server_auth_none(xprocess):
    yield from _mock_http_server(xprocess, name="http_server_auth_none", port=8000, auth="none")


@pytest.fixture
def http_server_auth_none_debug_repodata(xprocess):
    yield from _mock_http_server(
        xprocess,
        name="http_server_auth_none_debug_repodata",
        port=8000,
        auth="none-debug-repodata",
    )


@pytest.fixture
def http_server_auth_none_debug_packages(xprocess):
    yield from _mock_http_server(
        xprocess,
        name="http_server_auth_none_debug_packages",
        port=8000,
        auth="none-debug-packages",
    )


@pytest.fixture
def http_server_auth_basic(xprocess):
    yield from _mock_http_server(
        xprocess,
        name="http_server_auth_basic",
        port=8000,
        auth="basic",
        user="user",
        password="test",
    )


@pytest.fixture
def http_server_auth_basic_email(xprocess):
    yield from _mock_http_server(
        xprocess,
        name="http_server_auth_basic_email",
        port=8000,
        auth="basic",
        user="user@email.com",
        password="test",
    )


@pytest.fixture
def http_server_auth_token(xprocess):
    yield from _mock_http_server(
        xprocess,
        name="http_server_auth_token",
        port=8000,
        auth="token",
        token="xy-12345678-1234-1234-1234-123456789012",
    )


def create_with_channel(
    channel, solver="libmamba", check=True, **kwargs
) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "conda",
            "create",
            "-p",
            _get_temp_prefix(),
            f"--experimental-solver={solver}",
            "--json",
            "--override-channels",
            "--download-only",
            "-c",
            channel,
            "test-package",
        ],
        check=check,
        **kwargs,
    )


def create_with_channel_in_process(channel, solver="libmamba", **kwargs) -> Tuple[str, str, int]:
    stdout, stderr, returncode = run_command(
        "create",
        _get_temp_prefix(),
        f"--experimental-solver={solver}",
        "--json",
        "--override-channels",
        "--download-only",
        "-c",
        channel,
        "test-package",
        **kwargs,
    )
    return stdout, stderr, returncode
