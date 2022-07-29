# conda-libmamba-solver

[![Anaconda-Server Badge](https://anaconda.org/main/conda-libmamba-solver/badges/version.svg)](https://anaconda.org/main/conda-libmamba-solver)
[![Anaconda-Server Badge](https://anaconda.org/main/conda-libmamba-solver/badges/latest_release_date.svg)](https://anaconda.org/main/conda-libmamba-solver)
[![GitHub Workflow Status](https://img.shields.io/github/workflow/status/conda-incubator/conda-libmamba-solver/CI?label=CI&logo=github&style=flat-square)](https://github.com/conda-incubator/conda-libmamba-solver/actions/workflows/ci.yml)
[![CalVer version used YY.MM.MICRO](https://img.shields.io/badge/calver-YY.MM.MICRO-22bfda.svg?style=flat-square)](https://calver.org)

The fast mamba solver, now in conda!

## What is this exactly?

conda-libmamba-solver is a new (experimental) solver for the
[conda package manager](https://docs.conda.io/) which uses the solver from the
[mamba project](https://mamba.readthedocs.io/) behind the scenes, while
carefully implementing conda's functionality and expected behaviors on top.
The library used by mamba to do the heavy-lifting is called [libsolv](https://github.com/openSUSE/libsolv).

Additional information about the project can be found in the blog post on Anaconda's weblog:
[A Faster Solver for Conda: Libmamba](https://www.anaconda.com/blog/a-faster-conda-for-a-growing-community).

## Getting started

The new libmamba integrations are experimental, but you can get a taste of how they are working
so far by following these instructions.

Before we start: to use the libmamba integrations you need to update the `conda` installation
in your `base` environment to at least 4.12.0 or higher.

1. `conda-libmamba-solver` needs to be present in your `base` environment.

First make sure you are running conda 4.12.0 or higher:
```bash
conda update -n base conda
```

Then install conda-libmamba-solver:
```bash
conda install -n base conda-libmamba-solver
```

2. Now you can experiment with different things. `--dry-run` is specially useful to check how
different solvers interact. The main switch you need to take care of is the _experimental solver_
option:

```bash
# Using default (classic) solver
$ conda create -n demo scipy --dry-run
# This is equivalent
$ conda create -n demo scipy --dry-run --experimental-solver=classic
# Using libmamba integrations
$ conda create -n demo scipy --dry-run --experimental-solver=libmamba
```

> Hint: You can also enable the experimental solver with the `CONDA_EXPERIMENTAL_SOLVER`
> environment variable: `CONDA_EXPERIMENTAL_SOLVER=libmamba conda install ...`

3. Use `time` to measure how different solvers perform. Take into account that repodata
retrieval is cached across attempts, so only consider timings after warming that up:

```bash
# Warm up the repodata cache
$ conda create -n demo scipy --dry-run
# Timings for original solver
$ time conda create -n demo scipy --dry-run --experimental-solver=classic
# Timings for libmamba integrations
$ time conda create -n demo scipy --dry-run --experimental-solver=libmamba
```

> `conda create` commands will have similar performance because it's a very simple action! However,
> things change once you factor in existing environments. Simple commands like `conda install scipy`
> show ~2x speedups already.

4. If you need extra details on _why_ solvers are working in that way, increase verbosity. Output
might get too long for your terminal buffer, so consider using a pager like `less`:

```bash
# Verbosity can be expressed with 1, 2 or 3 `v`
$ conda create -n demo scipy --dry-run -vvv --experimental-solver=libmamba  2>&1 | less
```

## FAQ

### Where can I provide feedback (e.g. bug reports)?

If something is not working as expected please:

1. Go to https://github.com/conda/conda/issues/new/choose
2. Choose the "Libmamba Solver Feedback (Experimental Feature)" option
3. Fill out the issue form as complete as possible
4. Attach the log file as printed in your terminal output (if applicable)

The conda team will regularly triage the feedback and respond to your issue.

### How do I uninstall it?

If you don't want to use the experimental solver anymore, you can uninstall it with:

```
$ conda remove conda-libmamba-solver
```

### How do I configure conda to use the experimental solver permanently?

Use the following command to always use `libmamba` as your default solver:

```
$ conda config --set experimental_solver libmamba
```

To undo this change permanently, run:

```
$ conda config --remove-key experimental_solver
```
