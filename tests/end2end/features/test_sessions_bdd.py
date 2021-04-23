# vim: ft=python fileencoding=utf-8 sts=4 sw=4 et:

# Copyright 2016-2021 Florian Bruhin (The Compiler) <mail@qutebrowser.org>
#
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

import pathlib
import logging

import pytest
import pytest_bdd as bdd
bdd.scenarios('sessions.feature')


@pytest.fixture(autouse=True)
def turn_on_scroll_logging(quteproc):
    quteproc.turn_on_scroll_logging()


@bdd.when(bdd.parsers.parse('I have a "{name}" session file:\n{contents}'))
def create_session_file(quteproc, name, contents):
    filename = (pathlib.Path(quteproc.basedir) / 'data' / 'sessions' /
                name).with_suffix('.yml')
    filename.write_text(contents, encoding='utf-8')


@bdd.when(bdd.parsers.parse('I replace "{pattern}" by "{replacement}" in the '
                            '"{name}" session file'))
def session_replace(quteproc, server, pattern, replacement, name):
    # First wait until the session was actually saved
    quteproc.wait_for(category='message', loglevel=logging.INFO,
                      message='Saved session {}.'.format(name))
    filename = (pathlib.Path(quteproc.basedir) / 'data' /
                'sessions' / name).with_suffix('.yml')
    replacement = replacement.replace('(port)', str(server.port))  # yo dawg
    data = filename.read_text(encoding='utf-8')
    filename.write_text(data.replace(pattern, replacement), encoding='utf-8')


@bdd.then(bdd.parsers.parse("the session {name} should exist"))
def session_should_exist(quteproc, name):
    filename = (pathlib.Path(quteproc.basedir) / 'data' /
                'sessions' / name).with_suffix('.yml')
    assert pathlib.Path(filename).exists()


@bdd.then(bdd.parsers.parse("the session {name} should not exist"))
def session_should_not_exist(quteproc, name):
    filename = (pathlib.Path(quteproc.basedir) / 'data' /
                'sessions' / name).with_suffix('.yml')
    assert not pathlib.Path(filename).exists()
