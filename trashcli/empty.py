from .trash import TopTrashDirRules, TrashDir, path_of_backup_copy
from .trash import TrashDirsScanner
from .trash import EX_OK
from .trash import PrintHelp
from .trash import PrintVersion
from .trash import EX_USAGE
from .trash import ParseTrashInfo
import os
import sys
from trashcli.list_mount_points import os_mount_points
from datetime import datetime
from trashcli.fs import FileSystemReader
from trashcli.fs import FileRemover
from trashcli import trash


def main(argv=sys.argv,
         stdout=sys.stdout,
         stderr=sys.stderr,
         environ=os.environ):
    return EmptyCmd(
        out=stdout,
        err=stderr,
        environ=environ,
        list_volumes=os_mount_points,
        now=datetime.now,
        file_reader=FileSystemReader(),
        getuid=os.getuid,
        file_remover=FileRemover(),
        version=trash.version,
    ).run(*argv)


class EmptyCmd:
    def __init__(self,
                 out,
                 err,
                 environ,
                 list_volumes,
                 now,
                 file_reader,
                 getuid,
                 file_remover,
                 version):

        self.out = out
        self.err = err
        self.file_reader = file_reader
        self.environ = environ
        self.getuid = getuid
        self.list_volumes = list_volumes
        self.version = version
        self._now = now
        self.file_remover = file_remover
        self._dustman = DeleteAnything()

    def run(self, *argv):
        self.program_name = os.path.basename(argv[0])
        self.exit_code = EX_OK

        parse = Parser(
            on_help=PrintHelp(self.description, self.out).my_print_help,
            on_version=PrintVersion(self.out, self.version).print_version,
            on_invalid_option=self.report_invalid_option_usage,
            on_trash_dir=self.empty_trashdir,
            on_argument=self.set_max_age_in_days,
            on_default=self.empty_all_trashdirs,
        )
        parse.parse_argv(argv)

        return self.exit_code

    def set_max_age_in_days(self, arg):
        max_age_in_days = int(arg)
        self._dustman = DeleteAccordingDate(self.file_reader.contents_of,
                                            self._now,
                                            max_age_in_days)

    def report_invalid_option_usage(self, program_name, option):
        self.println_err("{program_name}: invalid option -- '{option}'"
                         .format(**locals()))
        self.exit_code |= EX_USAGE

    def println_err(self, msg):
        self.err.write("{}\n".format(msg))

    def description(self, program_name, printer):
        printer.usage('Usage: %s [days]' % program_name)
        printer.summary('Purge trashed files.')
        printer.options(
           "  --version   show program's version number and exit",
           "  -h, --help  show this help message and exit")
        printer.bug_reporting()

    def empty_trashdir(self, specific_dir):
        self.delete_all_things_under_trash_dir(specific_dir, None)

    def empty_all_trashdirs(self):
        scanner = TrashDirsScanner(self.environ,
                                   self.getuid,
                                   self.list_volumes,
                                   TopTrashDirRules(self.file_reader))

        for event, args in scanner.scan_trash_dirs():
            if event == TrashDirsScanner.Found:
                path, volume = args
                self.delete_all_things_under_trash_dir(path, volume)

    def delete_all_things_under_trash_dir(self, trash_dir_path, _volume_path):
        trash_dir = TrashDir(self.file_reader)
        for trash_info in trash_dir.list_trashinfo(trash_dir_path):
            self.delete_trashinfo_and_backup_copy(trash_info)
        for orphan in trash_dir.list_orphans(trash_dir_path):
            self.delete_orphan(orphan)

    def delete_trashinfo_and_backup_copy(self, trashinfo_path):
        trashcan = self.make_trashcan()
        self._dustman.delete_if_ok(trashinfo_path, trashcan)

    def delete_orphan(self, path_to_backup_copy):
        trashcan = self.make_trashcan()
        trashcan.delete_orphan(path_to_backup_copy)

    def make_trashcan(self):
        file_remover_with_error = FileRemoveWithErrorHandling(self.file_remover,
                                                              self.print_cannot_remove_error)
        trashcan = CleanableTrashcan(file_remover_with_error)
        return trashcan

    def print_cannot_remove_error(self, _exc, path):
        error_message = "cannot remove {path}".format(path=path)
        self.println_err("{program_name}: {msg}".format(
            program_name=self.program_name,
            msg=error_message))

    def println(self, line):
        self.out.write(line + '\n')


class FileRemoveWithErrorHandling:
    def __init__(self, file_remover, on_error):
        self.file_remover = file_remover
        self.on_error = on_error

    def remove_file(self, path):
        try:
            return self.file_remover.remove_file(path)
        except OSError as e:
            self.on_error(e, path)

    def remove_file_if_exists(self, path):
        try:
            return self.file_remover.remove_file_if_exists(path)
        except OSError as e:
            self.on_error(e, path)


class DeleteAccordingDate:
    def __init__(self, contents_of, now, max_age_in_days):
        self._contents_of = contents_of
        self._now = now
        self.max_age_in_days = max_age_in_days

    def delete_if_ok(self, trashinfo_path, trashcan):
        contents = self._contents_of(trashinfo_path)
        ParseTrashInfo(
            on_deletion_date=IfDate(
                OlderThan(self.max_age_in_days, self._now),
                lambda: trashcan.delete_trashinfo_and_backup_copy(trashinfo_path)
            ),
        )(contents)


class DeleteAnything:

    def delete_if_ok(self, trashinfo_path, trashcan):
        trashcan.delete_trashinfo_and_backup_copy(trashinfo_path)


class IfDate:
    def __init__(self, date_criteria, then):
        self.date_criteria = date_criteria
        self.then = then

    def __call__(self, date2):
        if self.date_criteria(date2):
            self.then()


class OlderThan:
    def __init__(self, days_ago, now):
        from datetime import timedelta
        self.limit_date = now() - timedelta(days=days_ago)

    def __call__(self, deletion_date):
        return deletion_date < self.limit_date


class CleanableTrashcan:
    def __init__(self, file_remover):
        self._file_remover = file_remover

    def delete_orphan(self, path_to_backup_copy):
        self._file_remover.remove_file(path_to_backup_copy)

    def delete_trashinfo_and_backup_copy(self, trashinfo_path):
        backup_copy = path_of_backup_copy(trashinfo_path)
        self._file_remover.remove_file_if_exists(backup_copy)
        self._file_remover.remove_file(trashinfo_path)


class Parser:
    def __init__(self,
                 on_help,
                 on_version,
                 on_trash_dir,
                 on_invalid_option,
                 on_argument,
                 on_default):
        self.on_help = on_help
        self.on_version = on_version
        self.on_trash_dir = on_trash_dir
        self.on_invalid_option = on_invalid_option
        self.on_argument = on_argument
        self.on_default = on_default

    def parse_argv(self, argv):
        program_name = os.path.basename(argv[0])
        result, args = self.parse_argv2(argv[1:])
        self.use_parsed_values(program_name, result, args)

    def use_parsed_values(self, program_name, result, args):
        if result == 'print_version':
            self.on_version(program_name)
        elif result == 'print_help':
            self.on_help(program_name)
        elif result == 'invalid_option':
            invalid_option, = args
            self.on_invalid_option(program_name, invalid_option)
        elif result == 'on_trash_dir':
            value, = args
            self.on_trash_dir(value)
        elif result == 'default':
            arguments, = args
            for argument in arguments:
                self.on_argument(argument)
            self.on_default()

    def parse_argv2(self, args):
        from getopt import getopt, GetoptError

        try:
            options, arguments = getopt(args,
                                        'h',
                                        ['help', 'version', 'trash-dir='])
        except GetoptError as e:
            invalid_option = e.opt
            return 'invalid_option', (invalid_option,)
        else:
            for option, value in options:
                if option in ('--help', '-h'):
                    return 'print_help', ()
                if option == '--version':
                    return 'print_version', ()
                if option == '--trash-dir':
                    return 'on_trash_dir', (value,)
            return 'default', (arguments,)
