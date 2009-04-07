#!/usr/bin/env python
from setuptools import setup, find_packages

setup (
    name = "katcp",
    version = "trunk",
    description = "Karoo Array Telescope Communication Protocol library'",
    author = "Simon Cross",
    author_email = "simon.cross@ska.ac.za",
    packages = find_packages(),
    scripts = [
        "scripts/katcp-exampleserver.py",
        "scripts/katcp-exampleclient.py",
    ],
    url='http://ska.ac.za/',
    classifiers=[
        "Development Status :: 3 - Alpha",
        "Intended Audience :: Developers",
        "License :: Other/Proprietary License",
        "Operating System :: OS Independent",
        "Programming Language :: Python",
        "Topic :: Software Development :: Libraries :: Python Modules",
        "Topic :: Scientific/Engineering :: Astronomy",
    ],
    platforms = [ "OS Independent" ],
    keywords="kat kat7 ska",
    zip_safe = False,
    # Bitten Test Suite
    test_suite = "katcp.test.suite",
)
