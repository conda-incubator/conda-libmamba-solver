import os
import logging
from collections import defaultdict
from tempfile import NamedTemporaryFile
from typing import Iterable, Union

from conda.base.constants import REPODATA_FN
from conda.base.context import context
from conda.common.io import Spinner
from conda.common.serialize import json_dump, json_load
from conda.core.subdir_data import SubdirData
from conda.models.channel import Channel
from conda.models.match_spec import MatchSpec
from conda.models.records import PackageRecord
import libmambapy as api

from . import __version__
from .mamba_utils import get_index, set_channel_priorities
from .state import IndexHelper
from .utils import escape_channel_url

log = logging.getLogger(f"conda.{__name__}")


class LibMambaIndexHelper(IndexHelper):
    _LIBMAMBA_PROTOCOLS = ("http", "https", "file")

    def __init__(
        self,
        installed_records: Iterable[PackageRecord] = (),
        channels: Iterable[Union[Channel, str]] = None,
        subdirs: Iterable[str] = None,
        repodata_fn: str = REPODATA_FN,
    ):
        # Check channel support
        if channels is None:
            channels = context.channels
        self._channels = channels
        if subdirs is None:
            subdirs = context.subdirs
        self._subdirs = subdirs
        self._repodata_fn = repodata_fn

        self._repos = []
        self._pool = api.Pool()

        installed_repo = self._load_from_records(self._pool, "installed", installed_records)
        self._repos.append(installed_repo)

        self._channel_lookup = None
        self._index = self._load_channels()

        self._query = api.Query(self._pool)
        self._format = api.QueryFormat.JSON

    @staticmethod
    def _generate_channel_lookup(index):
        lookup = {}
        for _, info in index:
            try:  # libmamba api
                url = info["channel"].platform_url(info["platform"], with_credentials=False)
            except AttributeError:  # conda api
                url = info["channel"].urls(with_credentials=False, subdirs=(info["platform"],))[0]
            lookup[url] = info
        return lookup

    @staticmethod
    def _load_from_records(
        pool: api.Pool, repo_name: str, records: Iterable[PackageRecord] = ()
    ) -> api.Repo:
        # export installed records to a temporary json file
        exported = {"packages": {}}
        additional_infos = {}
        for record in records:
            exported["packages"][record.fn] = {
                **record.dist_fields_dump(),
                "depends": record.depends,
                "constrains": record.constrains,
                "build": record.build,
            }
            info = api.ExtraPkgInfo()
            if record.noarch:
                info.noarch = record.noarch.value
            if record.url:
                info.repo_url = record.url
            additional_infos[record.name] = info

        with NamedTemporaryFile(suffix=".json", delete=False, mode="w") as f:
            f.write(json_dump(exported))

        installed = api.Repo(pool, repo_name, f.name, "")
        installed.add_extra_pkg_info(additional_infos)
        installed.set_installed()
        os.unlink(f.name)

        return installed

    def _load_channels(self):
        """
        Handle all types of channels with a hybrid approach that uses
        libmamba whenever possible, but delegates to conda internals as
        a fallback. The important bit is to maintain order so priorities
        are computed adequately. This is not as easy as it looks, given
        the unspecified Channel spec.

        A user can pass a channel in several ways:
        - By name (e.g. defaults or conda-forge).
        - By URL (e.g. https://conda.anaconda.org/pytorch)
        - By URL + subdir (e.g. https://conda.anaconda.org/pytorch/noarch)

        All of those are acceptable, but libmamba will eventually expect a list
        of URLs with subdirs so it can delegate to libcurl. libcurl expects
        _escaped_ URLs (with %-encoded espaces, for example: %20). This means
        that libmamba will see a different URL compared to the user-provided one.
        libmamba might also modify the URL to include tokens.

        To sum up, we might obtain a different URL back, so we can't just blindly
        use the input URLs as dictionary keys to keep references. Instead, we will
        have to rely on the input order, very carefully.
        """
        # Libmamba only supports a subset of the protocols offered in conda
        # We first filter the channel list to use the appropriate loaders
        # We will finally merge everything together in the right order
        urls_by_loader = []
        _libmamba_urls = []
        for channel in self._channels:
            # channel can be str or Channel; make sure it's Channel!
            channel_obj = Channel(channel)
            channel_urls = channel_obj.urls(with_credentials=True, subdirs=self._subdirs)
            if channel_obj.scheme and not channel_obj.scheme in self._LIBMAMBA_PROTOCOLS:
                # These channels are loaded with conda
                log.debug(
                    "Channel %s not supported by libmamba. Using conda.core.SubdirData",
                    channel_obj,
                )
                for url in channel_urls:
                    urls_by_loader.append(["conda", url])
            else:
                # These channels are loaded with libmamba
                for url in channel_urls:
                    # This fixes test_activate_deactivate_modify_path_bash
                    # and other local channels (path to url) issues
                    url = url.rstrip("/").rsplit("/", 1)[0]  # remove subdir
                    url = escape_channel_url(url)
                    urls_by_loader.append(["libmamba", url])
                    _libmamba_urls.append(url)

        free_channel = "https://repo.anaconda.com/pkgs/free"
        if context.restore_free_channel and free_channel not in _libmamba_urls:
            urls_by_loader.append(["libmamba", free_channel])

        # now we stack contiguous blocks together so the loader can operate in batches
        # [(A, 1), (A, 2), (A, 3), (B, 1), (A, 4)] -> [(A, [1, 2, 3]), (B, [1]), (A, [4])]
        contiguous_urls_by_loader = [[urls_by_loader[0][0], urls_by_loader[0][1:]]]
        for loader, url in urls_by_loader[1:]:
            current_loader = contiguous_urls_by_loader[-1][0]
            if loader == current_loader:
                contiguous_urls_by_loader[-1][1].append(url)
            else:
                contiguous_urls_by_loader.append([loader, [url]])

        full_index = []
        for loader, urls in contiguous_urls_by_loader:
            if loader == "libmamba":
                # delegate to libmamba loaders
                full_index += get_index(
                    channel_urls=urls,
                    prepend=False,
                    use_local=context.use_local,
                    platform=self._subdirs,
                    repodata_fn=self._repodata_fn,
                )
            else:
                # load channels with conda, if needed
                for url in urls:
                    with Spinner(
                        url,
                        enabled=not context.verbosity and not context.quiet,
                        json=context.json,
                    ):
                        channel = Channel(url)
                        subdir_data = SubdirData(channel, repodata_fn=self._repodata_fn)
                        subdir_data.load()
                        repo = self._load_from_records(self._pool, url, subdir_data.iter_records())
                        full_index.append(
                            (
                                repo,
                                {
                                    "platform": channel.subdir,
                                    "url": url,
                                    "channel": channel,
                                    "loaded_with": "conda.core.subdir_data.SubdirData",
                                },
                            )
                        )

        set_channel_priorities(self._pool, full_index, self._repos)

        self._channel_lookup = self._generate_channel_lookup(full_index)

        return full_index

    def whoneeds(self, query: str):
        return self._query.whoneeds(query, self._format)

    def depends(self, query: str):
        return self._query.depends(query, self._format)

    def search(self, query: str):
        return self._query.find(query, self._format)

    def explicit_pool(self, specs: Iterable[MatchSpec]) -> Iterable[str]:
        """
        Returns all the package names that (might) depend on the passed specs
        """
        explicit_pool = set()
        for spec in specs:
            result_str = self.depends(spec.dist_str())
            result = json_load(result_str)
            for pkg in result["result"]["pkgs"]:
                explicit_pool.add(pkg["name"])
        return tuple(explicit_pool)
