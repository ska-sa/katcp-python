.. _How to Contribute:

********
Tutorial
********

 .. module:: katcp

*****************
How to Contribute
*****************

Everyone is welcome to contribute to the katcp-python project.
If you don't feel comfortable with writing core katcp we are looking for
contributors to documentation or/and tests.

Another option is to report bugs, problems and new ideas as issues.
Please be very detailed.

Workflow
--------

A Git workflow with branches for each issue/feature is used.

* There is no special policy regarding commit messages. The first line should be short
  (50 chars or less) and contain summary of all changes.  Additional detail can be included
  after a blank line.
* Pull requests are normally made to master branch.  An exception is when hotfixing a
  release - in this case the merge target would be to the release branch.

reStructuredText and Sphinx
---------------------------

Documentation is written in reStructuredText_ and built with Sphinx_ - it's easy to contribute.
It also uses autodoc_ importing docstrings from the katcp package.

Source code standard
--------------------

All code should be PEP8_ compatible, with more details and exception described in our guidelines_.

.. note:: The accepted policy is that your code **cannot** introduce more
          issues than it solves!

You can also use other tools for checking PEP8_ compliance for your
personal use. One good example of such a tool is Flake8_ which combines PEP8_
and PyFlakes_. There are plugins_ for various IDEs so that you can use your
favourite tool easily.

Releasing a new version
-----------------------

From time to time a new version is released.  Anyone who wishes to see some
features of the master branch released is free to request a new release.  One of the
maintainers can make the release.  The basic steps required are as follows:

Pick a version number
  * Semantic version numbering is used:  <major>.<minor>.<patch>
  * Small changes are done as patch releases.  For these the version
    number should correspond the current development number since each
    release process finishes with a version bump.
  * Patch release example:
      - ``0.6.3.devN`` (current master branch)
      - changes to ``0.6.3`` (the actual release)
      - changes to ``0.6.4.dev0`` (bump the patch version at the end of the release process)

Create an issue in Github
  * This is to inform the community that a release is planned.
  * Use a checklist similar to the one below:

    | Task list:
    | - [ ] Read steps in the How to Contribute docs for making a release
    | - [ ] Edit the changelog and release notes files
    | - [ ] Make sure Jenkins tests are still passing on master branch
    | - [ ] Make sure the documentation is updated for master (readthedocs)
    | - [ ] Create a release tag on GitHub, from master branch
    | - [ ] Make sure the documentation is updated for release (readthedocs)
    | - [ ] Upload the new version to PyPI
    | - [ ] Fill the release description on GitHub
    | - [ ] Close this issue

  * A check list is this form on github can be ticked off as the work progresses.

Make a branch from ``master`` to prepare the release
  * Example branch name: ``user/ajoubert/prepare-v0.6.3``.
  * Edit the ``CHANGELOG`` and release notes (in ``docs/releasenotes.rst``).
    Include *all* pull requests since the previous release.
  * Create a pull request to get these changes reviewed before proceeding.

Make sure Jenkins is OK on ``master`` branch
  * All tests on Jenkins_ must be passing.
    If not, bad luck - you'll have to fix it first, and go back a few steps...

Make sure the documentation is ok on master
  * Log in to https://readthedocs.org.
  * Get account permissions for https://readthedocs.org/projects/katcp-python from another
    maintainer, if necessary.
  * Readthedocs *should* automatically build the docs for each:
      - push to master (latest docs)
      - new tags (e.g v0.6.3)
  * If it doesn't work automatically, then:
      - Trigger manually here:  https://readthedocs.org/projects/katcp-python/builds/

Create a release tag on GitHub
  * On the Releases page, use "Draft a new release".
  * Tag must match the format of previous tags, e.g. ``v0.6.3``.
  * Target must be the ``master`` branch.

Make sure the documentation is updated for the newly tagged release
  * If the automated build doesn't work automatically, then:
      - Trigger manually here:  https://readthedocs.org/projects/katcp-python/builds/
  * Set the new version to "active" here:
    https://readthedocs.org/dashboard/katcp-python/versions/

Upload the new version to PyPI
  * Log in to https://pypi.org.
  * Get account permissions for katcp from another contributor, if necessary.
  * If necessary, pip install twine: https://pypi.org/project/twine/
  * Build update from the tagged commit:
      - ``$ git clean -xfd  # Warning - remove all non-versioned files and directories``
      - ``$ git fetch``
      - ``$ git checkout v0.6.3``
      - ``$ python setup.py sdist bdist_wheel``
  * Upload to testpypi_, and make sure all is well:
      - ``$ twine upload -r testpypi dist/katcp-0.6.3.tar.gz``
  * Test installation (in a virtualenv):
      - ``$ pip install -i https://test.pypi.org/simple/ katcp``
  * Upload the source tarball and wheel to the real PyPI:
      - ``$ twine upload dist/katcp-0.6.3.tar.gz``
      - ``$ twine upload dist/katcp-0.6.3-py2-none-any.whl``

Fill in the release description on GitHub
  * Content must be the same as the details in the changelog.

Close off release issue in Github
  * All the items on the check list should be ticked off by now.
  * Close the issue.


.. _guidelines: https://docs.google.com/document/d/1aZoIyR9tz5rCWr2qJKuMTmKp2IzHlFjrCFrpDDHFypM
.. _autodoc: https://pypi.python.org/pypi/autodoc
.. _PEP8: https://www.python.org/dev/peps/pep-0008
.. _Flake8: https://gitlab.com/pycqa/flake8
.. _PyFlakes: https://github.com/PyCQA/pyflakes
.. _plugins: https://gitlab.com/pycqa/flake8/issues/286
.. _reStructuredText: http://docutils.sourceforge.net/rst.html
.. _Sphinx: http://www.sphinx-doc.org/en/stable
.. _PyLint: https://www.pylint.org
.. _Jenkins: http://ci.camlab.kat.ac.za/view/Multibranch%20Master/job/katcp-multibranch/job/master/
.. _testpypi: https://test.pypi.org
