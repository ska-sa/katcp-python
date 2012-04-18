#!/usr/bin/env python
from setuptools import setup, find_packages
from katcp import version

setup (
    name = "katcp",
    version = version.VERSION_STR,
    description = "Karoo Array Telescope Communication Protocol library",
    author = "Simon Cross",
    author_email = "simon.cross@ska.ac.za",
    packages = find_packages(),
    scripts = [
        "scripts/katcp-exampleserver.py",
        "scripts/katcp-exampleclient.py",
    ],
    url='http://pypi.python.org/pypi/katcp',
    download_url='http://pypi.python.org/pypi/katcp',
    license="BSD",
    classifiers=[
        "Development Status :: 3 - Alpha",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: BSD License",
        "Operating System :: OS Independent",
        "Programming Language :: Python",
        "Topic :: Software Development :: Libraries :: Python Modules",
        "Topic :: Scientific/Engineering :: Astronomy",
    ],
    platforms = [ "OS Independent" ],
    install_requires = ["nose", "unittest2", "mock"],
    keywords="kat kat7 ska",
    zip_safe = False,
    # Bitten Test Suite
    test_suite = "nose.collector",
)
