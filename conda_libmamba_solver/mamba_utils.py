# Copyright (C) 2019, QuantStack
# SPDX-License-Identifier: BSD-3-Clause

# TODO: Temporarily vendored from mamba.utils v0.19 on 2021.12.02
# Decide what to do with it when we split into a plugin
# 2022.02.15: updated vendored parts to v0.21.2

import json
import os
import tempfile
import urllib.parse
from collections import OrderedDict

try:
    from importlib.metadata import version
except ImportError:
    from importlib_metadata import version

from conda.base.constants import ChannelPriority
from conda.base.context import context
from conda.common.serialize import json_dump
from conda.common.url import join_url
from conda.core.index import _supplement_index_with_system, check_whitelist
from conda.core.prefix_data import PrefixData
from conda.gateways.connection.session import CondaHttpAuth
from conda.models.channel import Channel as CondaChannel
from conda.models.records import PackageRecord

import libmambapy as api


def mamba_version():
    return version("libmambapy")


def get_index(
    channel_urls=(),
    prepend=True,
    platform=None,
    use_local=False,
    use_cache=False,
    unknown=None,
    prefix=None,
    repodata_fn="repodata.json",
):
    if isinstance(platform, str):
        platform = [platform, "noarch"]

    all_channels = []
    if use_local:
        all_channels.append("local")
    all_channels.extend(channel_urls)
    if prepend:
        all_channels.extend(context.channels)
    check_whitelist(all_channels)

    # Remove duplicates but retain order
    all_channels = list(OrderedDict.fromkeys(all_channels))

    dlist = api.DownloadTargetList()

    index = []

    def fixup_channel_spec(spec):
        at_count = spec.count("@")
        if at_count > 1:
            first_at = spec.find("@")
            spec = spec[:first_at] + urllib.parse.quote(spec[first_at]) + spec[first_at + 1 :]
        if platform:
            spec = spec + "[" + ",".join(platform) + "]"
        return spec

    all_channels = list(map(fixup_channel_spec, all_channels))
    pkgs_dirs = api.MultiPackageCache(context.pkgs_dirs)
    api.create_cache_dir(str(pkgs_dirs.first_writable_path))

    for channel in api.get_channels(all_channels):
        for channel_platform, url in channel.platform_urls(with_credentials=True):
            full_url = CondaHttpAuth.add_binstar_token(url)

            sd = api.SubdirData(channel, channel_platform, full_url, pkgs_dirs, repodata_fn)

            index.append((sd, {"platform": channel_platform, "url": url, "channel": channel}))
            dlist.add(sd)

    is_downloaded = dlist.download(api.MAMBA_DOWNLOAD_FAILFAST)

    if not is_downloaded:
        raise RuntimeError("Error downloading repodata.")

    return index


def load_channels(
    pool,
    channels,
    repos,
    has_priority=None,
    prepend=True,
    platform=None,
    use_local=False,
    use_cache=True,
    repodata_fn="repodata.json",
):
    index = get_index(
        channel_urls=channels,
        prepend=prepend,
        platform=platform,
        use_local=use_local,
        repodata_fn=repodata_fn,
        use_cache=use_cache,
    )

    if has_priority is None:
        has_priority = context.channel_priority in [
            ChannelPriority.STRICT,
            ChannelPriority.FLEXIBLE,
        ]

    subprio_index = len(index)
    if has_priority:
        # first, count unique channels
        n_channels = len(set([entry["channel"].canonical_name for _, entry in index]))
        current_channel = index[0][1]["channel"].canonical_name
        channel_prio = n_channels

    for subdir, entry in index:
        # add priority here
        if has_priority:
            if entry["channel"].canonical_name != current_channel:
                channel_prio -= 1
                current_channel = entry["channel"].canonical_name
            priority = channel_prio
        else:
            priority = 0
        if has_priority:
            # NOTE: -- this is the whole reason we are vendoring this file --
            # We are patching this from 0 to 1, starting with mamba 0.19
            # Otherwise, test_create::test_force_remove fails :shrug:
            subpriority = 1
        else:
            subpriority = subprio_index
            subprio_index -= 1

        if not subdir.loaded() and entry["platform"] != "noarch":
            # ignore non-loaded subdir if channel is != noarch
            continue

        if context.verbosity != 0 and not context.json:
            print(
                "Channel: {}, platform: {}, prio: {} : {}".format(
                    entry["channel"], entry["platform"], priority, subpriority
                )
            )
            print("Cache path: ", subdir.cache_path())

        repo = subdir.create_repo(pool)
        repo.set_priority(priority, subpriority)
        repos.append(repo)

    return index


def init_api_context(verbosity: int = context.verbosity, use_mamba_experimental: bool = False):
    api_ctx = api.Context()

    api_ctx.json = context.json
    api_ctx.dry_run = context.dry_run
    if context.json:
        api.cancel_json_output()
        context.always_yes = True
        context.quiet = True
        if use_mamba_experimental:
            context.json = False

    # we want verbosity by default, we'll control stdout in our logging
    # but the file logs will be DEBUG
    api_ctx.verbosity = verbosity
    api_ctx.set_verbosity(verbosity)
    api_ctx.quiet = context.quiet
    api_ctx.offline = context.offline
    api_ctx.local_repodata_ttl = context.local_repodata_ttl
    api_ctx.use_index_cache = context.use_index_cache
    api_ctx.always_yes = context.always_yes
    api_ctx.channels = context.channels
    api_ctx.platform = context.subdir

    if "MAMBA_EXTRACT_THREADS" in os.environ:
        try:
            max_threads = int(os.environ["MAMBA_EXTRACT_THREADS"])
            api_ctx.extract_threads = max_threads
        except ValueError:
            v = os.environ["MAMBA_EXTRACT_THREADS"]
            raise ValueError(
                f"Invalid conversion of env variable 'MAMBA_EXTRACT_THREADS' from value '{v}'"
            )

    def get_base_url(url, name=None):
        tmp = url.rsplit("/", 1)[0]
        if name:
            if tmp.endswith(name):
                return tmp.rsplit("/", 1)[0]
        return tmp

    api_ctx.channel_alias = str(get_base_url(context.channel_alias.url(with_credentials=True)))

    additional_custom_channels = {}
    for el in context.custom_channels:
        if context.custom_channels[el].canonical_name not in ["local", "defaults"]:
            additional_custom_channels[el] = get_base_url(
                context.custom_channels[el].url(with_credentials=True), el
            )
    api_ctx.custom_channels = additional_custom_channels

    additional_custom_multichannels = {}
    for el in context.custom_multichannels:
        if el not in ["defaults", "local"]:
            additional_custom_multichannels[el] = []
            for c in context.custom_multichannels[el]:
                additional_custom_multichannels[el].append(
                    get_base_url(c.url(with_credentials=True))
                )
    api_ctx.custom_multichannels = additional_custom_multichannels

    api_ctx.default_channels = [
        get_base_url(x.url(with_credentials=True)) for x in context.default_channels
    ]

    if context.ssl_verify is False:
        api_ctx.ssl_verify = "<false>"
    elif context.ssl_verify is not True:
        api_ctx.ssl_verify = context.ssl_verify
    api_ctx.target_prefix = context.target_prefix
    api_ctx.root_prefix = context.root_prefix
    api_ctx.conda_prefix = context.conda_prefix
    api_ctx.pkgs_dirs = context.pkgs_dirs
    api_ctx.envs_dirs = context.envs_dirs

    api_ctx.connect_timeout_secs = int(round(context.remote_connect_timeout_secs))
    api_ctx.max_retries = context.remote_max_retries
    api_ctx.retry_backoff = context.remote_backoff_factor
    api_ctx.add_pip_as_python_dependency = context.add_pip_as_python_dependency
    api_ctx.use_only_tar_bz2 = context.use_only_tar_bz2

    if context.channel_priority is ChannelPriority.STRICT:
        api_ctx.channel_priority = api.ChannelPriority.kStrict
    elif context.channel_priority is ChannelPriority.FLEXIBLE:
        api_ctx.channel_priority = api.ChannelPriority.kFlexible
    elif context.channel_priority is ChannelPriority.DISABLED:
        api_ctx.channel_priority = api.ChannelPriority.kDisabled

    return api_ctx


def to_conda_channel(channel, platform):
    if channel.scheme == "file":
        return CondaChannel.from_value(channel.platform_url(platform, with_credentials=False))

    return CondaChannel(
        channel.scheme,
        channel.auth,
        channel.location,
        channel.token,
        channel.name,
        platform,
        channel.package_filename,
    )


def to_package_record_from_subjson(entry, pkg, jsn_string):
    channel_url = entry["url"]
    info = json.loads(jsn_string)
    info["fn"] = pkg
    info["channel"] = to_conda_channel(entry["channel"], entry["platform"])
    info["url"] = join_url(channel_url, pkg)
    package_record = PackageRecord(**info)
    return package_record


# TODO: This one can be deleted once merged


def get_installed_packages(prefix, show_channel_urls=None):
    result = {"packages": {}}

    # Currently, we need to have pip interop disabled :/
    installed = {rec: rec for rec in PrefixData(prefix, pip_interop_enabled=False).iter_records()}

    # add virtual packages as installed packages
    # they are packages installed on the system that conda can do nothing
    # about (e.g. glibc)
    # if another version is needed, installation just fails
    # they don't exist anywhere (they start with __)
    _supplement_index_with_system(installed)
    installed = list(installed)

    for prec in installed:
        json_rec = prec.dist_fields_dump()
        json_rec["depends"] = prec.depends
        json_rec["constrains"] = prec.constrains
        json_rec["build"] = prec.build
        result["packages"][prec.fn] = json_rec

    return installed, result


# TODO: This one can be deleted once merged

installed_pkg_recs = None

# TODO: This one can be deleted once merged


def get_installed_jsonfile(prefix):
    global installed_pkg_recs
    installed_pkg_recs, output = get_installed_packages(prefix, show_channel_urls=True)
    installed_json_f = tempfile.NamedTemporaryFile("w", delete=False)
    installed_json_f.write(json_dump(output))
    installed_json_f.flush()
    return installed_json_f, installed_pkg_recs
