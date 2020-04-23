#!/usr/bin/env python
# Copyright 2008 National Research Foundation (South African Radio Astronomy Observatory)
# BSD license - see LICENSE for details

from __future__ import absolute_import, division, print_function

import os

from setuptools import find_packages, setup


this_directory = os.path.abspath(os.path.dirname(__file__))

files = {"Readme": "README.md", "Changelog": "CHANGELOG.md"}

long_description = ""
for name, filename in files.items():
    if name != 'Readme':
        long_description += "# {}\n".format(name)
    with open(os.path.join(this_directory, filename)) as _f:
        file_contents = _f.read()
    long_description += file_contents + "\n\n"


setup(
    name="katcp",
    description="Karoo Array Telescope Communication Protocol library",
    long_description=long_description,
    long_description_content_type="text/markdown",
    author="MeerKAT CAM Team",
    author_email="cam@ska.ac.za",
    packages=find_packages(),
    url="https://github.com/ska-sa/katcp-python",
    download_url="https://pypi.org/project/katcp/",
    license="BSD",
    classifiers=[
        "Development Status :: 5 - Production/Stable",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: BSD License",
        "Operating System :: OS Independent",
        "Programming Language :: Python",
        "Programming Language :: Python :: 2",
        "Programming Language :: Python :: 2.7",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.5",
        "Programming Language :: Python :: 3.6",
        "Programming Language :: Python :: 3.7",
        "Programming Language :: Python :: 3.8",
        "Topic :: Software Development :: Libraries :: Python Modules",
        "Topic :: Scientific/Engineering :: Astronomy",
    ],
    platforms=["OS Independent"],
    python_requires=">=2.7, !=3.0.*, !=3.1.*, !=3.2.*, !=3.3.*, !=3.4.*",
    keywords="kat kat7 ska MeerKAT",
    setup_requires=["katversion"],
    use_katversion=True,
    install_requires=[
        "ply",
        "future",
        "futures; python_version<'3'",
        "tornado>=4.3, <5",
    ],
    zip_safe=False,
    test_suite="nose.collector",
)
