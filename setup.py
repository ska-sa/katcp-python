#!/usr/bin/env python

from setuptools import setup, find_packages

setup(
    name="katcp",
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
    setup_requires=["katversion"],
    use_katversion=True,
    install_requires=[
        "ply",
        "tornado>=4.3, <5",
        "futures",
        "future"
    ],
    # run tests and install these requirements with python setup.py nosetests
    tests_require=[
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
