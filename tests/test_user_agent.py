"""
This file tests for the different user-agents conda and conda-libmamba-solver
might present to a server. We mock the server via `_reposerver.py`.

There are two request backend we can end up using:
* libcurl, via libmamba
* requests, via conda

As of June 2022, we only use libmamba/libcurl to fetch the repodata.json files.
Package downloads still take place with the usual conda/requests stack. In other
words:

* If we are using conda classic, we will use requests to download both
  repodatas and tarballs.
* If we switch to libmamba solver, we will use curl to download repodatas,
  but tarballs will still be downloaded with requests.

With libmamba 0.23 and conda 4.13, we can customize the user agent. The user
agent will look slightly different depending on the network stack, so we have
up to three possible user agent strings:

* Conda classic will always have the same user agent:
    conda/4.12.0.post61+7acaba230 requests/2.27.1 CPython/3.8.13
    Linux/5.10.25-linuxkit debian/10 glibc/2.28
* Conda with libmamba enabled will have two user agents:
    * For repodata fetching, libmamba will present itself as:
        conda/4.12.0.post61+7acaba230 requests/2.27.1 CPython/3.8.13
        Linux/5.10.25-linuxkit debian/10 glibc/2.28
        solver/libmamba conda-libmamba-solver/22.3.1 libmambapy/0.24.0
        libcurl/7.83.1 OpenSSL/1.1.1o zlib/1.2.12 libssh2/1.10.0 nghttp2/1.47.0
    * For package download, requests will present itself as:
        conda/4.12.0.post61+7acaba230 requests/2.27.1 CPython/3.8.13
        Linux/5.10.25-linuxkit debian/10 glibc/2.28
        solver/libmamba conda-libmamba-solver/22.3.1 libmambapy/0.24.0

Note how conda classic's user agent is present in all three possibilities. When
the libmamba solver is enabled, we extend it with `solver/libmamba` plus the
versions of conda-libmamba-solver and libmambapy. This is then extended by
the libcurl client inside libmamba, adding the versions of libcurl, openssl, zlib,
libssh2 and nghttp2.

When using `mamba` directly in the CLI, its user agent will start with `mamba/<version>`.
"""

import base64
import os
import json
import sys
from subprocess import run, check_output, PIPE
from importlib_metadata import version

import pytest


from .channel_testing_utils import (
    create_with_channel,
    http_server_auth_none_debug_repodata,
    http_server_auth_none_debug_packages,
)


@pytest.mark.parametrize("solver", ("classic", "libmamba"))
def test_user_agent_conda_info(solver):
    env = os.environ.copy()
    env["CONDA_EXPERIMENTAL_SOLVER"] = solver
    out = check_output(["conda", "info", "--json"], env=env)
    info = json.loads(out)
    assert "conda/" in info["user_agent"]
    if solver == "classic":
        assert "solver/" not in info["user_agent"]
    elif solver == "libmamba":
        assert "solver/libmamba" in info["user_agent"]
        assert f"conda-libmamba-solver/{version('conda-libmamba-solver')}" in info["user_agent"]
        assert f"libmambapy/{version('libmambapy')}" in info["user_agent"]


def assert_libmamba_user_agent(stdout):
    """
    Conda's network stack (based on requests) has a nice way of reporting connection
    errors, which includes the headers as part of the report. All we need to do is force
    an error on the server side (see _reposerver.py 'none-debug-*' mockup servers) and we
    will see how the client is viewed by the server! Awesome! See `check_user_agent_requests``
    for the easy way out :)

    Libmamba's network stack (based on libcurl) does not have nice reports :( If we force an
    error to obtain details about the request (and hence the client's user agent), all we get
    is the error code and the URL. The hack is to make the server fail with 404s, by redirecting
    the pertinent request to a fake URL. The fake URL has been engineered to contain the client
    headers as a base64 string in the path (I know, ew!), so it appears in the exception message.

    We then parse the exception message, retrieve the base64 chunk in the URL, decode it and...
    ta-da! The user agent will be there!
    """
    # The exception message is in a single line that looks like this:
    #  Multi-download failed. Reason: Transfer finalized, status: 404
    #  [http://localhost:8000/headers/SG9zdDogb...yIEdNVAoK] 469 bytes
    # we want to decode the base64 encoded path, which will give a chunk of text,
    # and then find the user-agent line
    error = "Multi-download failed. Reason: Transfer finalized, status: 404"
    for line in stdout.splitlines():
        if error in line:
            _, address_and_suffix = line.split("[")
            address, _ = address_and_suffix.split("]")
            b64_headers = address.split("/")[-1]
            headers = base64.b64decode(b64_headers).decode("utf-8")
            break
    else:
        raise AssertionError("Couldn't find the error message containing payload")

    for line in headers.splitlines():
        if line.startswith("User-Agent:"):
            assert "conda/" in line
            assert "solver/libmamba" in line
            assert f"conda-libmamba-solver/{version('conda-libmamba-solver')}" in line
            assert f"libmambapy/{version('libmambapy')}" in line
            assert "libcurl/" in line
            break
    else:
        raise AssertionError("Couldn't find the User-Agent info in headers!")


def assert_requests_user_agent(stdout, solver, request_type="repodata"):
    """
    With conda classic, the request handler uses `requests`. An invalid response from the server
    will raise an exception that already contains the User-Agent headers in the payload.

    See `assert_libmamba_user_agent` docstring for more details.
    """
    # the output might contain more than one json dict
    # tip: conda separates json payloads with nul (\x00)
    # we split by nul and keep the last chunk surrounded by {}
    payload = None
    for chunk in stdout.split("\x00"):
        chunk = chunk.strip()
        if chunk.startswith("{") and chunk.endswith("}"):
            payload = chunk
    if payload is None:
        raise AssertionError(f"Could not find a JSON payload in the output: {stdout}")
    data = json.loads(payload)
    if request_type == "repodata":
        assert data["exception_name"] == "UnavailableInvalidChannel"
        assert data["reason"] == "FILE NOT FOUND"
        details = data["response_details"]

    elif request_type == "packages":
        assert data["error"] == "Multiple Errors Encountered."
        assert data["exception_name"] == "CondaMultiError"
        error = data["errors"][0]
        assert error["exception_name"] == "CondaHTTPError"
        assert error["reason"] == "FILE NOT FOUND"
        details = error["response_details"]

    for line in details.splitlines():
        if line.startswith("> User-Agent:"):
            assert "conda/" in line
            if solver == "classic":
                assert "solver/" not in line
            elif solver == "libmamba":
                assert "conda/" in line
                assert "solver/libmamba" in line
                assert f"conda-libmamba-solver/{version('conda-libmamba-solver')}" in line
                assert f"libmambapy/{version('libmambapy')}" in line
                if request_type == "repodata":
                    assert "libcurl/" in line
                elif request_type == "packages":
                    assert "requests/" in line
            break
    else:
        raise AssertionError("Couldn't find the User-Agent info in headers!")


def test_user_agent_libmamba_repodata(http_server_auth_none_debug_repodata):
    process = create_with_channel(
        http_server_auth_none_debug_repodata,
        solver="libmamba",
        check=False,
        stdout=PIPE,
        stderr=PIPE,
        text=True,
    )
    assert_libmamba_user_agent(process.stdout)


def test_user_agent_libmamba_packages(http_server_auth_none_debug_packages, tmp_path):
    run([sys.executable, "-m", "conda", "clean", "--tarballs", "--packages", "--yes"])
    env = os.environ.copy()
    env["CONDA_PKGS_DIRS"] = str(tmp_path)
    process = create_with_channel(
        http_server_auth_none_debug_packages,
        solver="libmamba",
        check=False,
        stdout=PIPE,
        stderr=PIPE,
        text=True,
        env=env,
    )
    print("-- STDOUT --")
    print(process.stdout)
    print("-- STDERR --")
    print(process.stderr)
    assert_requests_user_agent(process.stdout, solver="libmamba", request_type="packages")


def test_user_agent_classic_repodata(http_server_auth_none_debug_repodata):
    process = create_with_channel(
        http_server_auth_none_debug_repodata,
        solver="classic",
        check=False,
        stdout=PIPE,
        stderr=PIPE,
        text=True,
    )
    assert_requests_user_agent(process.stdout, solver="classic")


def test_user_agent_classic_packages(http_server_auth_none_debug_packages, tmp_path):
    run([sys.executable, "-m", "conda", "clean", "--tarballs", "--packages", "--yes"])

    env = os.environ.copy()
    env["CONDA_PKGS_DIRS"] = str(tmp_path)
    process = create_with_channel(
        http_server_auth_none_debug_packages,
        solver="classic",
        check=False,
        stdout=PIPE,
        stderr=PIPE,
        text=True,
        env=env,
    )
    print("-- STDOUT --")
    print(process.stdout)
    print("-- STDERR --")
    print(process.stderr)
    assert_requests_user_agent(process.stdout, solver="classic", request_type="packages")
