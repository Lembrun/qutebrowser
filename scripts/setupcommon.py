# vim: ft=python fileencoding=utf-8 sts=4 sw=4 et:

# Copyright 2014-2021 Florian Bruhin (The Compiler) <mail@qutebrowser.org>

# This file is part of qutebrowser.
#
# qutebrowser is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# qutebrowser is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with qutebrowser.  If not, see <https://www.gnu.org/licenses/>.


"""Data used by setup.py and the PyInstaller qutebrowser.spec."""

import sys
import os
import pathlib
import subprocess
sys.path.insert(0, str(pathlib.Path(__file__).parent / '..'))


if sys.hexversion >= 0x03000000:
    open_file = open
else:
    import codecs
    open_file = codecs.open


BASEDIR = pathlib.Path(__file__).resolve().parent / '..'


def _call_git(gitpath, *args):
    """Call a git subprocess."""
    return subprocess.run(
        ['git'] + list(args),
        cwd=gitpath, check=True,
        stdout=subprocess.PIPE).stdout.decode('UTF-8').strip()


def _git_str():
    """Try to find out git version.

    Return:
        string containing the git commit ID and timestamp.
        None if there was an error or we're not in a git repo.
    """
    if BASEDIR is None:
        return None
    if not (BASEDIR / ".git").is_dir():
        return None
    try:
        # https://stackoverflow.com/questions/21017300/21017394#21017394
        commit_hash = _call_git(BASEDIR, 'describe', '--match=NeVeRmAtCh',
                                '--always', '--dirty')
        date = _call_git(BASEDIR, 'show', '-s', '--format=%ci', 'HEAD')
        branch = _call_git(BASEDIR, 'rev-parse', '--abbrev-ref', 'HEAD')
        return '{} on {} ({})'.format(commit_hash, branch, date)
    except (subprocess.CalledProcessError, OSError):
        return None


def write_git_file():
    """Write the git-commit-id file with the current commit."""
    gitstr = _git_str()
    if gitstr is None:
        gitstr = ''
    path = BASEDIR / 'qutebrowser' / 'git-commit-id'
    with path.open('w', encoding='ascii') as f:
        f.write(gitstr)
