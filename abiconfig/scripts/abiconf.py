#!/usr/bin/env python
"""
Provides commands to interact with the collection of abiconf configuration files
and automate the compilation of Abinit on clusters.
"""
from __future__ import unicode_literals, division, print_function, absolute_import

import sys
import os
import argparse
import time
import shutil

from pprint import pprint
from socket import gethostname
from abiconfig.core.utils import get_ncpus, marquee, is_string, which, chunks, pprint_table, find_abinit_toptree
from abiconfig.core import termcolor
from abiconfig.core.termcolor import cprint, colored
from abiconfig.core.options import AbinitConfigureOptions, ConfigMeta, Config, ConfigList, get_actemplate_string
from abiconfig.core import release


def get_configs(options):
    """
    Return list of configuration files found if clusters if -b is not used else
    buildbot configuration files.
    """
    if getattr(options, "buildbot", False):
        return ConfigList.get_buildbot_configs()
    else:
        return ConfigList.get_clusters()


def abiconf_new(options):
    """Generate new configuration file."""
    template = get_actemplate_string()
    new_filename = options.new_filename
    if new_filename is None:
        new_filename = gethostname() + "-compiler-mpi-libs-extra.ac"

    with open(new_filename, "wt") as f:
        f.write(template)
    return 0


def abiconf_opts(options):
    """List available configure options."""
    confopts = AbinitConfigureOptions.from_myoptions_conf()

    if options.optnames is None or not options.optnames:
        # Print all options.
        cprint(marquee("Available options"), "yellow")
        table = [("name", "default", "status")]

        if options.verbose == 0:
            for opt in confopts.values():
                table.append((opt.name, str(opt.default), opt.status))
            pprint_table(table)
            print("\nUse -v for further information")

        else:
            for opt in confopts.values():
                cprint(marquee(opt.name), "yellow")
                print(opt)

    else:
        for optname in options.optnames:
            opt = confopts[optname]
            cprint(marquee(opt.name), "yellow")
            print(opt)

    return 0


def abiconf_bbcov(options):
    """Analyse the coverage of autoconf options in the Abinit test farm."""
    # Either build configs from internal directories or from command-line arguments.
    paths = options.paths
    if paths is None or not paths:
        # No argument provided --> Find directory with buildbot ac files and read them.
        # Assume we are inside an Abinit package.
        configs = ConfigList.get_buildbot_configs()
    else:
        # paths can be a directory name or list of files.
        if len(paths) == 1 and os.path.isdir(paths[0]):
            configs = ConfigList.from_dir(paths[0])
        else:
            configs = ConfigList.from_files(paths)

    return configs.buildbot_coverage(AbinitConfigureOptions.from_myoptions_conf(),
                                     verbose=options.verbose)


def abiconf_hostname(options):
    """Find configuration files for this hostname."""

    def show_hostnames():
        cprint(marquee("Available hostnames"), "yellow")
        all_hosts = sorted({conf.meta["hostname"] for conf in configs})
        for chunk in chunks(all_hosts, 7):
            cprint(", ".join(chunk), "blue")

    if options.show_hostnames:
        show_hostnames()
        return 0

    hostname = gethostname() if options.hostname is None else options.hostname
    nfound = 0
    configs = get_configs(options)
    for conf in configs:
        # TODO: Should handle foo.bar.be case
        #if not (hostname in conf.meta["keywords"] or hostname in conf.basename):
        print(conf)
        if not hostname in conf.meta["hostname"]:
            continue
        nfound += 1
        cprint(marquee(conf.basename), "yellow")
        if options.verbose:
            conf.cprint()
        else:
            pprint(conf.meta)

    if nfound == 0:
        cprint("No configuration file for `%s`. Will print internal list." % hostname, "red")
        show_hostnames()
    else:
        if options.verbose == 0: print("\nUse -v for further information")

    return 0


def abiconf_list(options):
    """List all configuration files."""
    configs = get_configs(options)

    width = 92
    for i, config in enumerate(configs):
        if options.verbose == 0:
            if i == 0: cprint(marquee("Available configuration files"), "yellow")
            print("[%d] %s" % (i, config.basename))
        else:
            cprint(marquee(config.basename, width=width), "yellow")
            config.cprint()
            print(width * "=")

    if options.verbose == 0: print("\nUse -v for further information")
    return 0


def abiconf_show(options):
    """Find configuration file from its basename and print it to terminal."""
    if options.basename is None or not options.basename:
        confopts = AbinitConfigureOptions.from_myoptions_conf()
        return abiconf_list(options)

    configs = get_configs(options)
    for i, config in enumerate(configs):
        if config.basename == options.basename:
            print(config)
            return 0
    else:
        cprint("Cannot find configuration file for `%s`" % options.basename, "red")
        return abiconf_list(options)


def abiconf_keys(options):
    """Find configuration files containing keywords."""
    configs = get_configs(options)
    if options.keys is None or not options.keys:
        # Print list of available keywords.
        all_keys = set()
        for conf in configs:
            all_keys.update(conf.meta["keywords"])

        cprint(marquee("Available keywords"), "yellow")
        for chunk in chunks(all_keys, 7):
            cprint(", ".join(chunk), "magenta")

    else:
        # Find configuration files containing keywords.
        keys = options.keys
        if is_string(keys): keys = [keys]
        keys = set(keys)
        nfound = 0
        for conf in configs:
            if keys.issubset(conf.meta["keywords"]):
                nfound += 1
                print("")
                cprint(marquee(conf.basename), "yellow")
                if options.verbose:
                    conf.cprint()
                else:
                    pprint(conf.meta)

        if options.verbose == 0 and nfound != 0:
            print("\nUse -v for further information")

    return 0


def abiconf_script(options):
    """Generate submission script from configuration file."""
    path = options.path
    if path is None:
        return abiconf_list(options)

    if os.path.exists(path):
        conf = Config.from_file(path)
    else:
        configs = ConfigList.get_clusters()
        for conf in configs:
            if conf.basename == path: break
        else:
            cprint("Cannot find %s in internal list" % path, "red")
            return abiconf_list(options)

    print(conf.get_script_str())
    return 0


def abiconf_convert(options):
    """Read a configuration file without metadata section and convert it."""
    path = options.path
    try:
        Config.from_file(path)
        cprint("%s is already a valid abiconf file. Nothing to do" % path, "magenta")
        return 0
    except:
        pass

    # Build template with metadata section
    lines = ConfigMeta.get_template_lines()

    # Add it to the file.
    with open(path, "rt") as fh:
        lines += fh.readlines()
    with open(path, "wt") as fh:
        fh.writelines(lines)

    conf = Config.from_file(path)
    print(conf)
    return 0


def abiconf_workon(options):
    """
    Compile the code with the settings and the modules specified
    in the autoconf file.
    """
    # If confname is not specified, print full list and return
    if options.confname is None:
        print("Available configuration files.")
        return abiconf_list(options)

    configs = get_configs(options)

    confname = options.confname
    if os.path.exists(confname):
        if os.path.isfile(confname):
            # Init conf from local file
            conf = Config.from_file(confname)
        else:
            raise RuntimeError("Found directory with same name as AC file!")
    else:
        # Find it in the abiconf database.
        for conf in configs:
            if conf.basename == confname or conf.path == confname: break
        else:
            cprint("Cannot find configuration file associated to `%s`" % confname, "red")
            return abiconf_list(options)

    if options.verbose:
        #cprint("Reading configuration file %s" % confname, "yellow")
        print("Configuration file:")
        print(conf)

    # Script must be executed inside the abinit source tree.
    #abinit_top = find_abinit_toptree()

    cwd = os.getcwd()
    workdir = os.path.join(cwd, "_build_" + confname)
    script = os.path.join(workdir, "workon_" + confname + ".sh")
    acfile = os.path.join(workdir, conf.basename)

    # Look before you leap.
    if os.path.exists(workdir):
        if not options.remove:
            cprint("Build directory `%s` already exists. Use `-r to remove it`. Returning" % workdir, "red")
            return 1
        else:
            shutil.rmtree(workdir)

    # Create build directory, copy ac file.
    # generare shell script to load modules, run configure and make.
    cprint("Creating build directory %s" % workdir, "yellow")
    os.mkdir(workdir)
    shutil.copy(conf.path, acfile)

    # Write shell script to start new with modules and run it.
    has_nag = "nag" in conf.meta["keywords"]
    nthreads = options.jobs
    if nthreads == 0: nthreads = max(1, get_ncpus() // 2)

    with open(script, "w+") as fh:
        fh.write("#!/bin/bash\n")
        fh.write("# Generated by abiconf.py on %s\n" % time.strftime("%c"))
        fh.write("cd %s\n" % workdir)
        for cmd in conf.meta.get("pre_configure", []):
            fh.write("%s\n" % cmd)

        conf_lines = [
            "[ ! -f __configure_done__ ] && ../configure --with-config-file='%s' && touch __configure_done__\n" % os.path.basename(acfile),
        ]
        if has_nag:
            # taken from pre_configure_nag.sh
            conf_lines.insert(0, "sed -i -e 's/ -little/& \| -library/' -e 's/\-\\#\\#\\#/& -dryrun/' ../configure\n")
            # taken from post_configure_nag.sh
            conf_lines.append("sed -i -e 's/\t\$.FCFLAGS. \\//' src/98_main/Makefile\n")

        fh.writelines(conf_lines)

        for cmd in conf.meta.get("post_configure", []):
            fh.write("%s\n" % cmd)

        # command > >(tee stdout.log) 2> >(tee stderr.log >&2)
        # http://stackoverflow.com/questions/692000/how-do-i-write-stderr-to-a-file-while-using-tee-with-a-pipe
        #cprint("`make stdout` redirected to make.stdout file", "yellow")
        #cprint("`make stderr` redirected to make.stderr file", "yellow")
        fh.write("make -j%d > >(tee make.stdout) 2> >(tee make.stderr >&2) \n" % nthreads)

        for cmd in conf.meta.get("post_make", []):
            fh.write("%s\n" % cmd)
        fh.write("# make check\n")

        fh.seek(0)

        if options.verbose:
            cprint("abiconf script:", "yellow")
            for line in fh.readlines():
                print(line, end="")

    retcode = 0
    if not options.make:
        cprint("Use:\n\t`source %s`\n\nto configure/make\n" % os.path.relpath(script), "yellow")
    else:
	# The code gets stuck here if -jN. Should find better approach
        os.chdir(workdir)
        retcode = os.system(". %s" % script)
        if retcode != 0:
            cprint("make returned retcode %s" % retcode, "red")
            stderr_path = os.path.join(workdir, "make.stderr")
            with open(stderr_path, "rt") as fh:
                err = fh.read()
                if err:
                    cprint("Errors found in %s" % stderr_path, "red")
                    cprint(err, "red")
        os.chdir(cwd)

    path = os.path.join(workdir, "template_job.sh")
    cprint("Writing submission script template to %s" % os.path.relpath(path), "yellow")
    with open(path, "wt") as fh:
        fh.write(conf.get_script_str())

    path = os.path.join(workdir, "launch_runtests_job.sh")
    cprint("Writing submission script for runtests.py to %s" % os.path.relpath(path), "yellow")
    with open(path, "wt") as fh:
        fh.write(conf.get_runtests_script_str())

    return retcode


def main():

    def str_examples():
        return """\
Usage example:
    abiconf.py hostname [HOST]       => Find configuration files for hostname HOST.
    abiconf.py list                  => List all configuration files.
    abiconf.py workon [ACNAME]       => Create build directory and compile the code with this
                                        configuration file.
    abiconf.py script [ACNAME]       => Generate job script template.
    abiconf.py keys intel mkl        => Find configuration files with these keywords.
    abiconf.py doc                   => Print documented template.
    abiconf.py opts [opt_name]       => List available configure options.
    abiconf.py get [ACNAME]          => Get a copy of the configuration file.
    abiconf.py new [FILENAME]        => Generate template file.
    abiconf.py convert acfile        => Add metadata section to an old autoconf file.

Options for developers
    abiconf.py bbcov    [DIRorFILEs]   => Test autoconf options coverage
"""

    def show_examples_and_exit(error_code=1):
        """Display the usage of the script."""
        print(str_examples())
        sys.exit(error_code)

    import argparse
    # Parent parser for common options.
    copts_parser = argparse.ArgumentParser(add_help=False)
    copts_parser.add_argument('-v', '--verbose', default=0, action='count', # -vv --> verbose=2
                              help='Verbose, can be supplied multiple times to increase verbosity.')
    copts_parser.add_argument('--no-colors', default=False, action="store_true", help='Disable ASCII colors.')

    # Parent parser for command that have a `buildbot` variant.
    bb_parser = argparse.ArgumentParser(add_help=False)
    bb_parser.add_argument('-b', '--buildbot', default=False, action='store_true',
                           help=("Activate buildbot mode. Configuration files are read from "
                                 "~abinit/doc/build/config-examples"))

    # Build the main parser.
    parser = argparse.ArgumentParser(epilog=str_examples(), formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('-V', '--version', action='version', version=release.__version__)

    # Create the parsers for the sub-commands
    subparsers = parser.add_subparsers(dest='command', help='sub-command help', description="Valid subcommands")

    # Subparser for hostname command.
    p_hostname = subparsers.add_parser('hostname', parents=[copts_parser, bb_parser], help=abiconf_hostname.__doc__)
    p_hostname.add_argument("hostname", nargs="?", default=None,
                            help="Find configuration file for this hostname. If not given hostname is autodetected.")
    p_hostname.add_argument("-s", "--show-hostnames", default=False, action="store_true",
                            help="List available hostnames.")

    # Subparser for list command.
    p_list = subparsers.add_parser('list', parents=[copts_parser, bb_parser], help=abiconf_list.__doc__)

    p_show = subparsers.add_parser('show', parents=[copts_parser, bb_parser], help=abiconf_show.__doc__)
    p_show.add_argument("basename", nargs="?", default=None, help="Name of the configuration file.")

    # Subparser for keys command.
    p_keys = subparsers.add_parser('keys', parents=[copts_parser, bb_parser], help=abiconf_keys.__doc__)
    p_keys.add_argument("keys", nargs="*", default=None,
                            help="Find configuration files with these keywords. "
                                 "Show available keywords if no value is provided.")

    # Subparser for script.
    p_script = subparsers.add_parser('script', parents=[copts_parser], help=abiconf_script.__doc__)
    p_script.add_argument('path', nargs="?", default=None,
                          help="Configuration file or database entry. None to print all files.")

    # Subparser for convert.
    p_conv = subparsers.add_parser('convert', parents=[copts_parser], help=abiconf_convert.__doc__)
    p_conv.add_argument('path', help="Configuration file in old format.")

    # Subparser for new command.
    p_new = subparsers.add_parser('new', parents=[copts_parser], help=abiconf_new.__doc__)
    p_new.add_argument('new_filename', nargs="?", default=None, help="Name of new configuration file.")

    # Subparser for doc command.
    p_doc = subparsers.add_parser('doc', parents=[copts_parser], help="Print documented template.")

    # Subparser for opts command.
    p_opts = subparsers.add_parser('opts', parents=[copts_parser], help=abiconf_opts.__doc__)
    p_opts.add_argument('optnames', nargs="*", default=None, help="Select options to show.")

    # Subparser for bb_cov command.
    p_bbcov = subparsers.add_parser('bbcov', parents=[copts_parser], help=abiconf_bbcov.__doc__)
    p_bbcov.add_argument('paths', nargs="*", default=None, help="ac file or directory with ac files.")

    # Subparser for workon command.
    p_workon = subparsers.add_parser('workon', parents=[copts_parser, bb_parser], help=abiconf_workon.__doc__)
    p_workon.add_argument('confname', nargs="?", default=None,
                          help="Configuration file to be used. Either abiconf basename or local file.")
    p_workon.add_argument("-m", '--make', action="store_true", default=False, help="Run configure/make. Default: False.")
    p_workon.add_argument("-j", '--jobs', type=int, default=0, help="Number of threads used to compile/make.")
    p_workon.add_argument("-r", '--remove', default=False, action="store_true", help="Remove build directory.")

    try:
        options = parser.parse_args()
    except Exception as exc:
        show_examples_and_exit(1)

    if options.no_colors:
        # Disable colors
        termcolor.enable(False)

    if options.command == "doc":
        template = get_actemplate_string()
        for line in template.splitlines():
            if len(line) > 2 and line[0] == "#" and line[1] != " ":
                # Option
                i = line.find("=")
                if i != -1:
                    print(colored(line[:i], "yellow"), line[i:], sep="")
                else:
                    cprint(line, "yellow")
            else:
                # Comment
                cprint(line, "blue")
        return 0

    else:
        # Dispatch.
        return globals()["abiconf_" + options.command](options)


if __name__ == "__main__":
    sys.exit(main())
