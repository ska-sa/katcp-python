#!/usr/bin/env python
from setuptools import setup, find_packages
from katcp import version

version_str = version.VERSION_STR
git_branch, git_revision = version.get_git_revision()
if git_branch:
    version_str = version_str+'git-{0}-{1}'.format(git_branch, git_revision)


setup (
    name = "katcp",
    version = version_str,
    description = "Karoo Array Telescope Communication Protocol library",
    author = "SKA SA KAT-7 / MeerKAT CAM team",
    author_email = "cam@ska.ac.za",
    packages = find_packages(),
    scripts = [
        "scripts/katcp-exampleserver.py",
        "scripts/katcp-exampleclient.py",
    ],
    url='https://github.com/ska-sa/katcp-python',
    download_url='http://pypi.python.org/pypi/katcp',
    license="BSD",
    classifiers=[
        "Development Status :: 5 - Production/Stable",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: BSD License",
        "Operating System :: OS Independent",
        "Programming Language :: Python :: 2",
        "Topic :: Software Development :: Libraries :: Python Modules",
        "Topic :: Scientific/Engineering :: Astronomy",
    ],
    platforms = [ "OS Independent" ],
    install_requires = ["ply", "twisted", "nose", "unittest2", "mock", "ProxyTypes"],
    keywords="kat kat7 ska MeerKAT",
    zip_safe = False,
    # Bitten Test Suite
    test_suite = "nose.collector",
)
