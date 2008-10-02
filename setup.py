#!/usr/bin/env python
from recursivebuild import setup
from recursivebuild.dependencies import Dependency

import os
conradRootDir = os.environ["CONRAD_PROJECT_ROOT"]

dep = Dependency()
dep.add_package()

setup (
    name = "katcp",
    description = "Karoo Array Telescope Communication Protocol library'",
    author = "Simon Cross",
    author_email = "simon.cross@ska.ac.za",
    packages = [ "katcp" ],
    dependency = dep,
    test_suite = "test.suite",
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
    keywords="kat kat7 ska",
    zip_safe = True
)
