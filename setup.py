#!/usr/bin/env python
import re

from setuptools import setup, find_packages
from katcp import version

version_str = version.VERSION_STR
git_branch, git_revision = version.get_git_revision()

# Only add git revision info if this is not a final versioned release
if git_branch and not re.match(r'^\d+\.\d+\.\d+$', version_str):
    version_str = version_str+'git-{0}-{1}'.format(git_branch, git_revision)

setup(
    name="katcp",
    version=version_str,
    description="Karoo Array Telescope Communication Protocol library",
    author="SKA SA KAT-7 / MeerKAT CAM team",
    author_email="cam@ska.ac.za",
    packages=find_packages(),
    scripts=[
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
    platforms=["OS Independent"],
    keywords="kat kat7 ska MeerKAT",
    install_requires=[
        "ply",
        "tornado>=4.0",
        "futures",
        "ProxyTypes"
    ],
    # run tests and install these requirements with python setup.py nosetests
    tests_require=[
        "linecache2",
        "traceback2",
        "unittest2",
        "nose",
        "mock",
        "pylint",
        "coverage",
        "nosexcover"
    ],
    zip_safe=False,
    test_suite="nose.collector",
)
