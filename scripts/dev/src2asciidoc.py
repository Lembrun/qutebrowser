#!/usr/bin/env python3
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

"""Generate asciidoc source for qutebrowser based on docstrings."""

import os
import pathlib
import sys
import shutil
import inspect
import subprocess
import tempfile
import argparse

sys.path.insert(0, str(pathlib.Path(__file__) / '..' / '..'))

# We import qutebrowser.app so all @cmdutils-register decorators are run.
import qutebrowser.app
from qutebrowser import qutebrowser, commands
from qutebrowser.extensions import loader
from qutebrowser.commands import argparser
from qutebrowser.config import configdata, configtypes
from qutebrowser.utils import docutils, usertypes
from qutebrowser.misc import objects
from scripts import asciidoc2html, utils

FILE_HEADER = """
// DO NOT EDIT THIS FILE DIRECTLY!
// It is autogenerated by running:
//   $ python3 scripts/dev/src2asciidoc.py
// vim: readonly:

""".lstrip()


class UsageFormatter(argparse.HelpFormatter):

    """Patched HelpFormatter to include some asciidoc markup in the usage.

    This does some horrible things, but the alternative would be to reimplement
    argparse.HelpFormatter while copying 99% of the code :-/
    """

    def __init__(self, prog, indent_increment=2, max_help_position=24,
                 width=200):
        """Override __init__ to set a fixed width as default."""
        super().__init__(prog, indent_increment, max_help_position, width)

    def _format_usage(self, usage, actions, groups, _prefix):
        """Override _format_usage to not add the 'usage:' prefix."""
        return super()._format_usage(usage, actions, groups, '')

    def _get_default_metavar_for_optional(self, action):
        """Do name transforming when getting metavar."""
        return argparser.arg_name(action.dest.upper())

    def _get_default_metavar_for_positional(self, action):
        """Do name transforming when getting metavar."""
        return argparser.arg_name(action.dest)

    def _metavar_formatter(self, action, default_metavar):
        """Override _metavar_formatter to add asciidoc markup to metavars.

        Most code here is copied from Python 3.10's argparse.py.
        """
        if action.metavar is not None:
            result = "'{}'".format(action.metavar)
        elif action.choices is not None:
            choice_strs = [str(choice) for choice in action.choices]
            result = ('{' + ','.join('*{}*'.format(e) for e in choice_strs) +
                      '}')
        else:
            result = "'{}'".format(default_metavar)

        def fmt(tuple_size):
            """Format the result according to the tuple size."""
            if isinstance(result, tuple):
                return result
            else:
                return (result, ) * tuple_size
        return fmt

    def _format_actions_usage(self, actions, groups):
        """Override _format_actions_usage to add asciidoc markup to flags.

        Because argparse.py's _format_actions_usage is very complex, we first
        monkey-patch the option strings to include the asciidoc markup, then
        run the original method, then undo the patching.
        """
        old_option_strings = {}
        for action in actions:
            old_option_strings[action] = action.option_strings[:]
            action.option_strings = ['*{}*'.format(s)
                                     for s in action.option_strings]
        ret = super()._format_actions_usage(actions, groups)
        for action in actions:
            action.option_strings = old_option_strings[action]
        return ret

    def _format_args(self, action, default_metavar):
        """Backport simplified star nargs usage.

        https://github.com/python/cpython/pull/17106
        """
        if sys.version_info >= (3, 9) or action.nargs != argparse.ZERO_OR_MORE:
            return super()._format_args(action, default_metavar)

        get_metavar = self._metavar_formatter(action, default_metavar)
        metavar = get_metavar(1)
        assert len(metavar) == 1
        return f'[{metavar[0]} ...]'


def _open_file(name, mode='w'):
    """Open a file with a preset newline/encoding mode."""
    return open(name, mode, newline='\n', encoding='utf-8')


def _get_cmd_syntax(_name, cmd):
    """Get the command syntax for a command.

    We monkey-patch the parser's formatter_class here to use our UsageFormatter
    which adds some asciidoc markup.
    """
    old_fmt_class = cmd.parser.formatter_class
    cmd.parser.formatter_class = UsageFormatter
    usage = cmd.parser.format_usage().rstrip()
    cmd.parser.formatter_class = old_fmt_class
    return usage


def _get_command_quickref(cmds):
    """Generate the command quick reference."""
    out = []
    out.append('[options="header",width="75%",cols="25%,75%"]')
    out.append('|==============')
    out.append('|Command|Description')
    for name, cmd in cmds:
        desc = inspect.getdoc(cmd.handler).splitlines()[0]
        out.append('|<<{name},{name}>>|{desc}'.format(name=name, desc=desc))
    out.append('|==============')
    return '\n'.join(out)


def _get_setting_quickref():
    """Generate the settings quick reference."""
    out = []
    out.append('')
    out.append('[options="header",width="75%",cols="25%,75%"]')
    out.append('|==============')
    out.append('|Setting|Description')
    for opt in sorted(configdata.DATA.values()):
        desc = opt.description.splitlines()[0]
        out.append('|<<{},{}>>|{}'.format(opt.name, opt.name, desc))
    out.append('|==============')
    return '\n'.join(out)


def _get_configtypes():
    """Get configtypes classes to document."""
    predicate = lambda e: (
        inspect.isclass(e) and
        # pylint: disable=protected-access
        e not in [configtypes.BaseType, configtypes.MappingType,
                  configtypes._Numeric, configtypes.FontBase] and
        # pylint: enable=protected-access
        issubclass(e, configtypes.BaseType))
    yield from inspect.getmembers(configtypes, predicate)


def _get_setting_types_quickref():
    """Generate the setting types quick reference."""
    out = []
    out.append('[[types]]')
    out.append('[options="header",width="75%",cols="25%,75%"]')
    out.append('|==============')
    out.append('|Type|Description')

    for name, typ in _get_configtypes():
        parser = docutils.DocstringParser(typ)
        desc = parser.short_desc
        if parser.long_desc:
            desc += '\n\n' + parser.long_desc
        out.append('|{}|{}'.format(name, desc))

    out.append('|==============')
    return '\n'.join(out)


def _get_command_doc(name, cmd):
    """Generate the documentation for a command."""
    output = ['[[{}]]'.format(name)]
    output += ['=== {}'.format(name)]
    syntax = _get_cmd_syntax(name, cmd)
    if syntax != name:
        output.append('Syntax: +:{}+'.format(syntax))
        output.append("")
    parser = docutils.DocstringParser(cmd.handler)
    output.append(parser.short_desc)
    if parser.long_desc:
        output.append("")
        output.append(parser.long_desc)

    output += list(_get_command_doc_args(cmd, parser))
    output += list(_get_command_doc_count(cmd, parser))
    output += list(_get_command_doc_notes(cmd))

    output.append("")
    output.append("")
    return '\n'.join(output)


def _get_command_doc_args(cmd, parser):
    """Get docs for the arguments of a command.

    Args:
        cmd: The Command to get the docs for.
        parser: The DocstringParser to use.

    Yield:
        Strings which should be added to the docs.
    """
    if cmd.pos_args:
        yield ""
        yield "==== positional arguments"
        for arg, name in cmd.pos_args:
            try:
                yield "* +'{}'+: {}".format(name, parser.arg_descs[arg])
            except KeyError as e:
                raise KeyError("No description for arg {} of command "
                               "'{}'!".format(e, cmd.name)) from e

    if cmd.opt_args:
        yield ""
        yield "==== optional arguments"
        for arg, (long_flag, short_flag) in cmd.opt_args.items():
            try:
                yield '* +*{}*+, +*{}*+: {}'.format(short_flag, long_flag,
                                                    parser.arg_descs[arg])
            except KeyError as e:
                raise KeyError("No description for arg {} of command "
                               "'{}'!".format(e, cmd.name)) from e


def _get_command_doc_count(cmd, parser):
    """Get docs for the count of a command.

    Args:
        cmd: The Command to get the docs for.
        parser: The DocstringParser to use.

    Yield:
        Strings which should be added to the docs.
    """
    for param in inspect.signature(cmd.handler).parameters.values():
        if cmd.get_arg_info(param).value in cmd.COUNT_COMMAND_VALUES:
            yield ""
            yield "==== count"
            try:
                yield parser.arg_descs[param.name]
            except KeyError:
                try:
                    yield parser.arg_descs['count']
                except KeyError as e:
                    raise KeyError("No description for count arg {!r} of "
                                   "command {!r}!"
                                   .format(param.name, cmd.name)) from e


def _get_command_doc_notes(cmd):
    """Get docs for the notes of a command.

    Args:
        cmd: The Command to get the docs for.
        parser: The DocstringParser to use.

    Yield:
        Strings which should be added to the docs.
    """
    if (cmd.maxsplit is not None or cmd.no_cmd_split or
            cmd.no_replace_variables and cmd.name != "spawn"):
        yield ""
        yield "==== note"
        if cmd.maxsplit is not None:
            yield ("* This command does not split arguments after the last "
                   "argument and handles quotes literally.")
        if cmd.no_cmd_split:
            yield ("* With this command, +;;+ is interpreted literally "
                   "instead of splitting off a second command.")
        if cmd.no_replace_variables and cmd.name != "spawn":
            yield r"* This command does not replace variables like +\{url\}+."


def _get_action_metavar(action, nargs=1):
    """Get the metavar to display for an argparse action.

    Args:
        action: The argparse action to get the metavar for.
        nargs: The nargs setting for the related argument.
    """
    if action.metavar is not None:
        if isinstance(action.metavar, str):
            elems = [action.metavar] * nargs
        else:
            elems = action.metavar
        return ' '.join("'{}'".format(e) for e in elems)
    elif action.choices is not None:
        choices = ','.join(str(e) for e in action.choices)
        return "'{{{}}}'".format(choices)
    else:
        return "'{}'".format(action.dest.upper())


def _format_action_args(action):
    """Get an argument string based on an argparse action."""
    if action.nargs is None:
        return _get_action_metavar(action)
    elif action.nargs == '?':
        return '[{}]'.format(_get_action_metavar(action))
    elif action.nargs == '*':
        return '[{mv} [{mv} ...]]'.format(mv=_get_action_metavar(action))
    elif action.nargs == '+':
        return '{mv} [{mv} ...]'.format(mv=_get_action_metavar(action))
    elif action.nargs == '...':
        return '...'
    else:
        return _get_action_metavar(action, nargs=action.nargs)


def _format_action(action):
    """Get an invocation string/help from an argparse action."""
    if action.help == argparse.SUPPRESS:
        return None
    if not action.option_strings:
        invocation = '*{}*::'.format(_get_action_metavar(action))
    else:
        parts = []
        if action.nargs == 0:
            # Doesn't take a value, so the syntax is -s, --long
            parts += ['*{}*'.format(s) for s in action.option_strings]
        else:
            # Takes a value, so the syntax is -s ARGS or --long ARGS.
            args_string = _format_action_args(action)
            for opt in action.option_strings:
                parts.append('*{}* {}'.format(opt, args_string))
        invocation = ', '.join(parts) + '::'
    return '{}\n    {}\n'.format(invocation, action.help)


def generate_commands(filename):
    """Generate the complete commands section."""
    with _open_file(filename) as f:
        f.write(FILE_HEADER)
        f.write("= Commands\n\n")
        f.write(commands.__doc__)
        normal_cmds = []
        other_cmds = []
        debug_cmds = []
        for name, cmd in objects.commands.items():
            if cmd.deprecated:
                continue
            if usertypes.KeyMode.normal not in cmd.modes:
                other_cmds.append((name, cmd))
            elif cmd.debug:
                debug_cmds.append((name, cmd))
            else:
                normal_cmds.append((name, cmd))
        normal_cmds.sort()
        other_cmds.sort()
        debug_cmds.sort()
        f.write("\n")
        f.write("== Normal commands\n")
        f.write(".Quick reference\n")
        f.write(_get_command_quickref(normal_cmds) + '\n')
        for name, cmd in normal_cmds:
            f.write(_get_command_doc(name, cmd))
        f.write("\n")
        f.write("== Commands not usable in normal mode\n")
        f.write(".Quick reference\n")
        f.write(_get_command_quickref(other_cmds) + '\n')
        for name, cmd in other_cmds:
            f.write(_get_command_doc(name, cmd))
        f.write("\n")
        f.write("== Debugging commands\n")
        f.write("These commands are mainly intended for debugging. They are "
                "hidden if qutebrowser was started without the "
                "`--debug`-flag.\n")
        f.write("\n")
        f.write(".Quick reference\n")
        f.write(_get_command_quickref(debug_cmds) + '\n')
        for name, cmd in debug_cmds:
            f.write(_get_command_doc(name, cmd))


def _generate_setting_backend_info(f, opt):
    """Generate backend information for the given option."""
    all_backends = [usertypes.Backend.QtWebKit, usertypes.Backend.QtWebEngine]
    if opt.raw_backends is not None:
        for name, conditional in sorted(opt.raw_backends.items()):
            if conditional is True:
                pass
            elif conditional is False:
                f.write("\nOn {}, this setting is unavailable.\n".format(name))
            else:
                f.write("\nOn {}, this setting requires {} or newer.\n"
                        .format(name, conditional))
    elif opt.backends == all_backends:
        pass
    elif opt.backends == [usertypes.Backend.QtWebKit]:
        f.write("\nThis setting is only available with the QtWebKit "
                "backend.\n")
    elif opt.backends == [usertypes.Backend.QtWebEngine]:
        f.write("\nThis setting is only available with the QtWebEngine "
                "backend.\n")
    else:
        raise ValueError("Invalid value {!r} for opt.backends"
                         .format(opt.backends))


def _generate_setting_option(f, opt):
    """Generate documentation for a single section."""
    f.write("\n")
    f.write('[[{}]]'.format(opt.name) + "\n")
    f.write("=== {}".format(opt.name) + "\n")
    f.write(opt.description + "\n")
    if opt.restart:
        f.write("\nThis setting requires a restart.\n")
    if opt.supports_pattern:
        f.write("\nThis setting supports URL patterns.\n")
    if opt.no_autoconfig:
        f.write("\nThis setting can only be set in config.py.\n")
    _generate_setting_backend_info(f, opt)
    f.write("\n")
    typ = opt.typ.get_name().replace(',', '&#44;')
    f.write('Type: <<types,{typ}>>\n'.format(typ=typ))
    f.write("\n")

    valid_values = opt.typ.get_valid_values()
    if valid_values is not None and valid_values.generate_docs:
        f.write("Valid values:\n")
        f.write("\n")
        for val in valid_values:
            try:
                desc = valid_values.descriptions[val]
                f.write(" * +{}+: {}".format(val, desc) + "\n")
            except KeyError:
                f.write(" * +{}+".format(val) + "\n")
        f.write("\n")

    f.write("Default: {}\n".format(opt.typ.to_doc(opt.default)))


def generate_settings(filename):
    """Generate the complete settings section."""
    configdata.init()
    with _open_file(filename) as f:
        f.write(FILE_HEADER)
        f.write("= Setting reference\n\n")
        f.write("== All settings\n")
        f.write(_get_setting_quickref() + "\n")
        for opt in sorted(configdata.DATA.values()):
            _generate_setting_option(f, opt)
        f.write("\n== Setting types\n")
        f.write(_get_setting_types_quickref() + "\n")


def _format_block(filename, what, data):
    """Format a block in a file.

    The block is delimited by markers like these:
        // QUTE_*_START
        ...
        // QUTE_*_END

    The * part is the part which should be given as 'what'.

    Args:
        filename: The file to change.
        what: What to change (authors, options, etc.)
        data; A list of strings which is the new data.
    """
    what = what.upper()
    oshandle, tmpname = tempfile.mkstemp()
    try:
        with _open_file(filename, mode='r') as infile, \
                _open_file(oshandle, mode='w') as temp:
            found_start = False
            found_end = False
            for line in infile:
                if line.strip() == '// QUTE_{}_START'.format(what):
                    temp.write(line)
                    temp.write(''.join(data))
                    found_start = True
                elif line.strip() == '// QUTE_{}_END'.format(what.upper()):
                    temp.write(line)
                    found_end = True
                elif (not found_start) or found_end:
                    temp.write(line)
        if not found_start:
            raise Exception("Marker '// QUTE_{}_START' not found in "
                            "'{}'!".format(what, filename))
        if not found_end:
            raise Exception("Marker '// QUTE_{}_END' not found in "
                            "'{}'!".format(what, filename))
    except:
        os.remove(tmpname)
        raise
    else:
        os.remove(filename)
        shutil.move(tmpname, filename)


def regenerate_manpage(filename):
    """Update manpage OPTIONS using an argparse parser."""
    parser = qutebrowser.get_argparser()
    groups = []
    # positionals, optionals and user-defined groups
    # pylint: disable=protected-access
    for group in parser._action_groups:
        groupdata = []
        groupdata.append('=== {}'.format(group.title))
        if group.description is not None:
            groupdata.append(group.description)
        for action in group._group_actions:
            action_data = _format_action(action)
            if action_data is not None:
                groupdata.append(action_data)
        groups.append('\n'.join(groupdata))
    # pylint: enable=protected-access
    options = '\n'.join(groups)
    # epilog
    if parser.epilog is not None:
        options += parser.epilog
    _format_block(filename, 'options', options)


def regenerate_cheatsheet():
    """Generate cheatsheet PNGs based on the SVG."""
    files = [
        ('doc/img/cheatsheet-small.png', 300, 185),
        ('doc/img/cheatsheet-big.png', 3342, 2060),
    ]

    for filename, x, y in files:
        subprocess.run(['inkscape', '-o', filename, '-b', 'white',
                        '-w', str(x), '-h', str(y),
                        'misc/cheatsheet.svg'], check=True)
        subprocess.run(['optipng', filename], check=True)


def main():
    """Regenerate all documentation."""
    utils.change_cwd()
    loader.load_components(skip_hooks=True)
    print("Generating manpage...")
    regenerate_manpage('doc/qutebrowser.1.asciidoc')
    print("Generating settings help...")
    generate_settings('doc/help/settings.asciidoc')
    print("Generating command help...")
    generate_commands('doc/help/commands.asciidoc')
    if '--cheatsheet' in sys.argv:
        print("Regenerating cheatsheet .pngs")
        regenerate_cheatsheet()
    if '--html' in sys.argv:
        asciidoc2html.main()


if __name__ == '__main__':
    main()
