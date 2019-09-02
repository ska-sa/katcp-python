from __future__ import print_function
from __future__ import division
from __future__ import absolute_import

# Copyright 2009 National Research Foundation (South African Radio Astronomy Observatory)
# BSD license - see LICENSE for details

from future import standard_library
standard_library.install_aliases()
from builtins import *
import os
from setuptools import setup, find_packages


this_directory = os.path.abspath(os.path.dirname(__file__))

files = {"Readme": "README.md", "Changelog": "CHANGELOG.md"}

long_description = ""
for name, filename in list(files.items()):
    long_description += "## {}\n".format(name)
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
    include_package_data=True,
    packages=find_packages(),
    scripts=["scripts/katcp-exampleserver.py", "scripts/katcp-exampleclient.py"],
    url="https://github.com/ska-sa/katcp-python",
    download_url="http://pypi.python.org/pypi/katcp",
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
        "Programming Language :: Python :: 3.3",
        "Programming Language :: Python :: 3.4",
        "Programming Language :: Python :: 3.5",
        "Programming Language :: Python :: 3.6",
        "Programming Language :: Python :: 3.7",
        "Programming Language :: Python :: 2",
        "Topic :: Software Development :: Libraries :: Python Modules",
        "Topic :: Scientific/Engineering :: Astronomy",
    ],
    platforms=["OS Independent"],
    python_requires=">=2.7, !=3.0.*, !=3.1.*, !=3.2.*, <4",
    keywords="kat kat7 ska MeerKAT",
    setup_requires=["katversion"],
    use_katversion=True,
    install_requires=[
        "ply",
        "future",
        "futures; python_version<'3'",
        "tornado>=4.3, <5.0; python_version<'3'",
        "tornado>=4.3, <7.0; python_version>='3'",
    ],
    # install extras by running pip install .[doc,<another_extra>]
    extras_require={
        "doc": [
            "sphinx>=1.2.3, <2.0",
            "docutils>=0.12, <1.0",
            "sphinx_rtd_theme>=0.1.5, <1.0",
            "numpydoc>=0.5, <1.0",
        ]
    },
    zip_safe=False,
    test_suite="nose.collector",
)
