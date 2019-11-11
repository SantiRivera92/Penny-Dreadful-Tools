import os
import subprocess
import sys
import time
from pathlib import Path
from typing import List

from plumbum import FG, local
from plumbum.commands.processes import ProcessExecutionError

from generate_readme import generate_readme
from shared import configuration
from shared.pd_exception import InvalidArgumentException, TestFailedException

ON_PROD = configuration.get_bool('production')
if ON_PROD:
    sys.stderr.write('DO NOT RUN dev.py ON PROD\n')
    sys.exit(1)

def run() -> None:
    try:
        run_dangerously()
    except InvalidArgumentException as e:
        sys.stderr.write(str(e) + '\n')
        sys.exit(1)
    except TestFailedException as e:
        sys.stderr.write(str(e) + '\n')
        sys.exit(2)
    except ProcessExecutionError as e:
        sys.stderr.write('Process failed: ' + str(e) + '\n')
        sys.exit(3)
    except Exception as e:
        sys.stderr.write('Unexpected problem: ' + str(e) + '\n')
        sys.exit(4)

def run_dangerously() -> None:
    try:
        cmd = sys.argv[1].lower()
        args = sys.argv[2:]
    except IndexError:
        raise InvalidArgumentException('Please supply an argument.')
    if cmd in ('unit', 'pytest'):
        unit(args)
    elif cmd == 'functional':
        unit(args, 'functional')
    elif cmd == 'perf':
        unit(args, 'perf')
    elif cmd in ('lint', 'pylint'):
        lint(args)
    elif cmd in ('types', 'mypy'):
        mypy(args)
    elif cmd == 'mypy-strict':
        mypy(args, True)
    elif cmd == 'jslint':
        jslint()
    elif cmd == 'jsfix':
        jsfix()
    elif cmd in ('nuke_db', 'reset_db'):
        reset_db()
    elif cmd in ('imports', 'isort', 'sort'):
        sort()
    elif cmd in ('fix-sorts', 'fix-imports', 'fiximports'):
        sort(True)
    elif cmd in ('pr', 'pull-request'):
        pull_request(args)
    elif cmd == 'buildjs':
        buildjs()
    elif cmd == 'popclean':
        popclean()
    elif cmd == 'readme':
        generate_readme()
    elif cmd == 'coverage':
        coverage()
    elif cmd == 'watch':
        watch()
    elif cmd == 'branch':
        branch(args)
    elif cmd == 'check':
        check(args)
    elif cmd in ('test', 'tests'):
        test(args)
    elif cmd == 'push':
        push(args)
    elif cmd == 'release':
        release(args)
    else:
        raise InvalidArgumentException('Unrecognised command {cmd}.'.format(cmd=cmd))

def lint(argv: List[str]) -> None:
    """
    Invoke Pylint with our preferred options
    """
    print('>>>> Running pylint')
    args = ['--rcfile=.pylintrc', # Load rcfile first.
            '--ignored-modules=alembic,MySQLdb,flask_sqlalchemy,distutils.dist', # override ignored-modules (codacy hack)
            '--load-plugins', 'pylint_quotes, pylint_monolith', # Plugins
            '--reports=n', # Don't show reports.
            '-f', 'parseable', # Machine-readable output.
            '-j', '4' # Use four cores for speed.
           ]
    args.extend(argv or find_files(file_extension='py'))
    # pylint: disable=import-outside-toplevel
    import pylint.lint
    linter = pylint.lint.Run(args, do_exit=False)
    if linter.linter.msg_status:
        raise TestFailedException(linter.linter.msg_status)

def mypy(argv: List[str], strict: bool = False) -> None:
    """
    Invoke mypy with our preferred options.
    Strict Mode enables additional checks that are currently failing (that we plan on integrating once they pass)
    """
    print('>>>> Typechecking')
    args = [
        '--ignore-missing-imports',     # Don't complain about 3rd party libs with no stubs
        '--disallow-untyped-calls',     # Strict Mode.  All function calls must have a return type.
        '--warn-redundant-casts',
        '--disallow-incomplete-defs',   # All parameters must have type definitions.
        '--check-untyped-defs'          # Typecheck on all methods, not just typed ones.
        ]
    if strict:
        args.append('--disallow-untyped-defs') # All methods must be typed.
    args.extend(argv or [
        '.'                             # Invoke on the entire project.
        ])
    # pylint: disable=import-outside-toplevel
    from mypy import api
    result = api.run(args)

    if result[0]:
        print(result[0])  # stdout

    if result[1]:
        sys.stderr.write(result[1])  # stderr

    print('Exit status: {code} ({english})'.format(code=result[2], english='Failure' if result[2] else 'Success'))
    if result[2]:
        raise TestFailedException(result[2])

def unit(argv: List[str], m: str = 'not functional and not perf') -> None:
    """
    Literally just prepare the DB and then invoke pytest.
    """
    print(f'>>>> Running unit tests with "{m}"')
    # pylint: disable=import-outside-toplevel
    import pytest
    from magic import multiverse, oracle
    multiverse.init()
    oracle.init()
    if argv and not argv[0].startswith('-'):
        to_find = argv.pop(0)
        argv.extend(find_files(to_find, 'py'))
    argv.extend(['--cov-report=', '-x', '-m', m])
    code = pytest.main(argv)
    if os.environ.get('TRAVIS') == 'true':
        upload_coverage()
    if code:
        raise TestFailedException(code)

# pylint: disable=pointless-statement
def upload_coverage() -> None:
    print('>>>> Upload coverage')
    # pylint: disable=import-outside-toplevel
    from shared import fetch_tools
    fetch_tools.store('https://codecov.io/bash', 'codecov.sh')
    python3 = local['python3']
    python3['-m', 'coverage', 'xml', '-i']
    bash = local['bash']
    bash['codecov.sh'] & FG

# pylint: disable=import-outside-toplevel
def sort(fix: bool = False) -> None:
    """
    This method is messy, and is a reduced form of isort.main.main()
    """
    print('>>>> Checking imports')
    from isort import SortImports
    import isort.main
    config = isort.main.from_path('.')
    if fix:
        config['recursive'] = True
        config['check'] = False
        config['ask_to_apply'] = False
    else:
        config['check'] = True
    file_names = isort.main.iter_source_code(['.'], config, [])
    wrong_sorted_files = False
    for file_name in file_names:
        try:
            sort_attempt = SortImports(file_name, check=config['check'])
            incorrectly_sorted = sort_attempt.incorrectly_sorted
            if incorrectly_sorted:
                wrong_sorted_files = True
        except IOError as e:
            print('WARNING: Unable to parse file {0} due to {1}'.format(file_name, e))
    if wrong_sorted_files:
        raise TestFailedException(2)

# pylint: disable=import-outside-toplevel
def reset_db() -> None:
    """
    Handle with care.
    """
    print('>>>> Reset db')
    import decksite.database
    decksite.database.db().nuke_database()
    import magic.database
    magic.database.db().nuke_database()

def push(args: List[str]) -> None:
    print('>>>> Stashing local changes')
    label = 'dev-py-stash-at-' + str(time.time())
    subprocess.check_call(['git', 'stash', 'save', label])
    print('>>>> Rebasing branch on Master')
    subprocess.check_call(['git', 'pull', 'origin', 'master', '--rebase'])
    print('>>>> Checking')
    test(args)
    print('>>>> Pushing')
    branch = subprocess.check_output(['git', 'rev-parse', '--abbrev-ref', 'HEAD']).strip().decode()
    subprocess.check_call(['git', 'push', '--set-upstream', 'origin', branch])
    print('>>>> Checking for stashed changes')
    output = subprocess.check_output(['git', 'stash', 'list'], stderr=subprocess.STDOUT)
    if label in str(output):
        print('>>>> Popping stashed changes')
        subprocess.call(['git', 'stash', 'pop'])

def pull_request(argv: List[str]) -> None:
    print('>>>> Pull request')
    subprocess.check_call(['hub', 'pull-request', *argv])

def buildjs() -> None:
    print('>>>> Building javascript')
    subprocess.check_call(['webpack', '--config=decksite/webpack.config.js'])

def jslint(fix: bool = False) -> None:
    print('>>>> Linting javascript')
    files = find_files(file_extension='js') + find_files(file_extension='jsx')
    cmd = ['./node_modules/.bin/eslint']
    if fix:
        cmd.append('--fix')
    subprocess.check_call(cmd + files)

def jsfix() -> None:
    print('>>>> Fixing js')
    jslint(fix=True)

def coverage() -> None:
    print('>>>> Coverage')
    subprocess.check_call(['coverage', 'run', 'dev.py', 'tests'])
    subprocess.check_call(['coverage', 'xml'])
    subprocess.check_call(['coverage', 'report'])

def watch() -> None:
    print('>>>> Watching')
    subprocess.check_call(['npm', 'run', 'watch'])

# Make a branch based off of current (remote) master with all your local changes preserved (but not added).
def branch(args: List[str]) -> None:
    if not args:
        print('Usage: dev.py branch <branch_name>')
        return
    branch = args.pop(0)
    print('>>>> Creating branch {branch}')
    subprocess.check_call(['git', 'stash', '-a'])
    subprocess.check_call(['git', 'clean', '-fxd'])
    subprocess.check_call(['git', 'checkout', 'master'])
    subprocess.check_call(['git', 'pull'])
    subprocess.check_call(['git', 'checkout', '-b', branch])
    try:
        subprocess.check_call(['git', 'stash', 'pop'])
    except subprocess.CalledProcessError:
        popclean()

# If you try and git stash and then git stash pop when decksite is running locally you get in a mess.
# This cleans up for you.
def popclean() -> None:
    print('>>>> Popping safely into messy directory.')
    output = subprocess.check_output(['git', 'stash', 'pop'], stderr=subprocess.STDOUT)
    lines = output.decode().split('\n')
    already = [line.split(' ')[1] for line in lines if 'already' in line]
    if not already:
        return
    for f in already:
        os.remove(f)
    subprocess.check_call(['git', 'stash', 'pop'])

def check(args: List[str]) -> None:
    lint(args)
    jslint()
    mypy(args)
    sort()

def test(args: List[str]) -> None:
    check(args)
    unit(args)

def release(args: List[str]) -> None:
    push(args)
    pull_request(args)

def find_files(needle: str = '', file_extension: str = '') -> List[str]:
    paths = subprocess.check_output(['git', 'ls-files']).strip().decode().split('\n')
    paths = [p for p in paths if 'logsite_migrations' not in p]
    if file_extension:
        paths = [p for p in paths if p.endswith(file_extension)]
    if needle:
        paths = [p for p in paths if needle in os.path.basename(p)]
    return paths

if __name__ == '__main__':
    run()
