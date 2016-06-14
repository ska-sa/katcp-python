# version.py
# -*- coding: utf8 -*-
# vim:fileencoding=utf8 ai ts=4 sts=4 et sw=4
# Copyright 2009 SKA South Africa (http://ska.ac.za/)
# BSD license - see COPYING for details

"""KATCP version information."""

import pkg_resources
import sys
import subprocess


VERSION = (0, 6, 0, 'final', 0)

BASE_VERSION_STR = '.'.join([str(x) for x in VERSION[:3]])
VERSION_STR = {
    'final': BASE_VERSION_STR,
    'alpha': BASE_VERSION_STR + 'a' + str(VERSION[4]),
    'rc': BASE_VERSION_STR + 'rc' + str(VERSION[4]),
}[VERSION[3]]


# Build-state information that assumes the package has already been installed.
# Can be used by device servers to get more detailed build info for build-state
# informs.

def construct_package_build_info(package, version):
    """Construct a base build info tuple.

    Parameters
    ----------
    package : str
        Name of the package to get build info for
    version : tuple
        The static version tuple containing (major, minor, point).
        Only the first three entries are used.

    Returns
    -------
    build_info : tuple of (major, minor, release)
        The base build information.

    """
    def get_git_rev(dist):
        # See if there is a git version string
        git_offs = dist.version.find('git-')
        if git_offs < 0:
            return None                   # no git info found
        return dist.version[git_offs:]

    try:
        dist = pkg_resources.get_distribution(package)
        # ver needs to be a list since tuples in Python <= 2.5 don't have
        # a .index method.
        ver = list(dist.parsed_version)
        # Check if we have a git revision
        rev = get_git_rev(dist)
        if rev is None:
            # Else try and get an SVN revision
            rev = "r%d" % int(ver[ver.index("*r")+1])
    except (pkg_resources.DistributionNotFound,
            ValueError, IndexError, TypeError):
        rev = "unknown"

    return version[:2] + (rev,)


def get_git_revision():
    """Attempt to find the git revision of this package's source.

    Usually called by setup.py.

    """
    # Backported implementation of subprocess.check_output from Python >= 2.7
    if sys.version_info < (2, 7):
        def check_output(*popenargs, **kwargs):
            """Run command with arguments and return its output as byte string.

            Backported from Python 2.7 as implemented as pure python in stdlib.

            >>> check_output(['/usr/bin/python', '--version'])
            Python 2.6.2

            """
            # Code borrowed from https://gist.github.com/1027906
            process = subprocess.Popen(stdout=subprocess.PIPE,
                                       *popenargs, **kwargs)
            output, unused_err = process.communicate()
            retcode = process.poll()
            if retcode:
                cmd = kwargs.get("args")
                if cmd is None:
                    cmd = popenargs[0]
                error = subprocess.CalledProcessError(retcode, cmd)
                error.output = output
                raise error
            return output
    else:
        check_output = subprocess.check_output

    # See if we are installing from git repository; retrieve branch name if so
    try:
        git_branch = check_output(
            ['git', 'rev-parse', '--abbrev-ref', 'HEAD']).strip()
    except Exception:
        git_branch = None

    if git_branch:
        # Get the git revision if this is a git repo
        git_revision = check_output(['git', 'rev-parse', 'HEAD']).strip()
    else:
        git_revision = None

    return git_branch, git_revision
