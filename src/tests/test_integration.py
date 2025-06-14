"""Use tox or pytest to run the test-suite."""

from collections import namedtuple

import os
import shlex
import shutil
import pytest
import pathlib
import tempfile
import textwrap
import subprocess
import sys

from unittest import mock

from pydocstyle import checker, violations


__all__ = ()


class SandboxEnv:
    """An isolated environment where pydocstyle can be run.

    Since running pydocstyle as a script is affected by local config files,
    it's important that tests will run in an isolated environment. This class
    should be used as a context manager and offers utility methods for adding
    files to the environment and changing the environment's configuration.

    """

    Result = namedtuple('Result', ('out', 'err', 'code'))

    def __init__(
        self,
        script_name='pydocstyle',
        section_name='pydocstyle',
        config_name='tox.ini',
    ):
        """Initialize the object."""
        self.tempdir = None
        self.script_name = script_name
        self.section_name = section_name
        self.config_name = config_name

    def write_config(self, prefix='', name=None, **kwargs):
        """Change an environment config file.

        Applies changes to `tox.ini` relative to `tempdir/prefix`.
        If the given path prefix does not exist it is created.

        """
        base = os.path.join(self.tempdir, prefix) if prefix else self.tempdir
        if not os.path.isdir(base):
            self.makedirs(base)

        name = self.config_name if name is None else name
        if name.endswith('.toml'):
            def convert_value(val):
                return (
                    repr(val).lower()
                    if isinstance(val, bool)
                    else repr(val)
                )
        else:
            def convert_value(val):
                return val

        with open(os.path.join(base, name), 'wt') as conf:
            conf.write(f"[{self.section_name}]\n")
            for k, v in kwargs.items():
                conf.write("{} = {}\n".format(
                    k.replace('_', '-'), convert_value(v)
                ))

    def open(self, path, *args, **kwargs):
        """Open a file in the environment.

        The file path should be relative to the base of the environment.

        """
        return open(os.path.join(self.tempdir, path), *args, **kwargs)

    def get_path(self, name, prefix=''):
        return os.path.join(self.tempdir, prefix, name)

    def makedirs(self, path, *args, **kwargs):
        """Create a directory in a path relative to the environment base."""
        os.makedirs(os.path.join(self.tempdir, path), *args, **kwargs)

    def invoke(self, args="", target=None):
        """Run pydocstyle on the environment base folder with the given args.

        If `target` is not None, will run pydocstyle on `target` instead of
        the environment base folder.

        """
        run_target = self.tempdir if target is None else \
            os.path.join(self.tempdir, target)

        cmd = shlex.split("{} {} {}"
                          .format(self.script_name, run_target, args),
                          posix=False)
        p = subprocess.Popen(cmd,
                             stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE)
        out, err = p.communicate()
        return self.Result(out=out.decode('utf-8'),
                           err=err.decode('utf-8'),
                           code=p.returncode)

    def __enter__(self):
        self.tempdir = tempfile.mkdtemp()
        # Make sure we won't be affected by other config files
        self.write_config()
        return self

    def __exit__(self, *args, **kwargs):
        shutil.rmtree(self.tempdir)
        pass


@pytest.fixture(scope="module")
def install_package(request):
    """Install the package in development mode for the tests.

    This is so we can run the integration tests on the installed console
    script.
    """
    cwd = os.path.join(os.path.dirname(__file__), '..', '..')
    subprocess.check_call(
        [sys.executable, "-m", "pip", "install", "-e", "."], cwd=cwd
    )
    yield
    subprocess.check_call(
        [sys.executable, "-m", "pip", "uninstall", "-y", "pydocstyle"], cwd=cwd
    )


@pytest.fixture(scope="function", params=['ini', 'toml'])
def env(request):
    """Add a testing environment to a test method."""
    sandbox_settings = {
        'ini': {
            'section_name': 'pydocstyle',
            'config_name': 'tox.ini',
        },
        'toml': {
            'section_name': 'tool.pydocstyle',
            'config_name': 'pyproject.toml',
        },
    }[request.param]
    with SandboxEnv(**sandbox_settings) as test_env:
        yield test_env


pytestmark = pytest.mark.usefixtures("install_package")


def parse_errors(err):
    """Parse `err` to a dictionary of {filename: error_codes}.

    This is for test purposes only. All file names should be different.

    """
    result = {}
    py_ext = '.py'
    lines = err.split('\n')
    while lines:
        curr_line = lines.pop(0)
        filename = curr_line[:curr_line.find(py_ext) + len(py_ext)]
        if lines:
            err_line = lines.pop(0).strip()
            err_code = err_line.split(':')[0]
            basename = os.path.basename(filename)
            result.setdefault(basename, set()).add(err_code)

    return result


def test_pep257_conformance():
    """Test that we conform to PEP 257."""
    base_dir = (pathlib.Path(__file__).parent / '..').resolve()
    excluded = base_dir / 'tests' / 'test_cases'
    src_files = (str(path) for path in base_dir.glob('**/*.py')
                 if excluded not in path.parents)

    ignored = {'D104', 'D105'}
    select = violations.conventions.pep257 - ignored
    errors = list(checker.check(src_files, select=select))
    assert errors == [], errors


def test_ignore_list():
    """Test that `ignore`d errors are not reported in the API."""
    function_to_check = textwrap.dedent('''
        def function_with_bad_docstring(foo):
            """ does spacinwithout a period in the end
            no blank line after one-liner is bad. Also this - """
            return foo
    ''')
    expected_error_codes = {'D100', 'D400', 'D401', 'D205', 'D209', 'D210',
                            'D403', 'D415', 'D213'}
    mock_open = mock.mock_open(read_data=function_to_check)
    from pydocstyle import checker
    with mock.patch.object(
            checker.tk, 'open', mock_open, create=True):
        # Passing a blank ignore here explicitly otherwise
        # checkers takes the pep257 ignores by default.
        errors = tuple(checker.check(['filepath'], ignore={}))
        error_codes = {error.code for error in errors}
        assert error_codes == expected_error_codes

    # We need to recreate the mock, otherwise the read file is empty
    mock_open = mock.mock_open(read_data=function_to_check)
    with mock.patch.object(
            checker.tk, 'open', mock_open, create=True):
        ignored = {'D100', 'D202', 'D213'}
        errors = tuple(checker.check(['filepath'], ignore=ignored))
        error_codes = {error.code for error in errors}
        assert error_codes == expected_error_codes - ignored


def test_skip_errors():
    """Test that `ignore`d errors are not reported in the API."""
    function_to_check = textwrap.dedent('''
        def function_with_bad_docstring(foo):  # noqa: D400, D401, D403, D415
            """ does spacinwithout a period in the end
            no blank line after one-liner is bad. Also this - """
            return foo
    ''')
    expected_error_codes = {'D100', 'D205', 'D209', 'D210', 'D213'}
    mock_open = mock.mock_open(read_data=function_to_check)
    from pydocstyle import checker
    with mock.patch.object(
            checker.tk, 'open', mock_open, create=True):
        # Passing a blank ignore here explicitly otherwise
        # checkers takes the pep257 ignores by default.
        errors = tuple(checker.check(['filepath'], ignore={}))
        error_codes = {error.code for error in errors}
        assert error_codes == expected_error_codes

    skipped_error_codes = {'D400', 'D401', 'D403', 'D415'}
    # We need to recreate the mock, otherwise the read file is empty
    mock_open = mock.mock_open(read_data=function_to_check)
    with mock.patch.object(
            checker.tk, 'open', mock_open, create=True):
        errors = tuple(checker.check(['filepath'], ignore={},
                                     ignore_inline_noqa=True))
        error_codes = {error.code for error in errors}
        assert error_codes == expected_error_codes | skipped_error_codes


def test_run_as_named_module():
    """Test that pydocstyle can be run as a "named module".

    This means that the following should run pydocstyle:

        python -m pydocstyle

    """
    # Add --match='' so that no files are actually checked (to make sure that
    # the return code is 0 and to reduce execution time).
    cmd = [sys.executable, "-m", "pydocstyle", "--match=''"]
    p = subprocess.Popen(cmd,
                         stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE)
    out, err = p.communicate()
    assert p.returncode == 0, out.decode('utf-8') + err.decode('utf-8')


def test_config_file(env):
    """Test that options are correctly loaded from a config file.

    This test create a temporary directory and creates two files in it: a
    Python file that has two violations (D100 and D103) and a config
    file (tox.ini). This test alternates settings in the config file and checks
    that we give the correct output.

    """
    with env.open('example.py', 'wt') as example:
        example.write(textwrap.dedent("""\
            def foo():
                pass
        """))

    env.write_config(ignore='D100')
    out, err, code = env.invoke()
    assert code == 1
    assert 'D100' not in out
    assert 'D103' in out

    env.write_config(ignore='')
    out, err, code = env.invoke()
    assert code == 1
    assert 'D100' in out
    assert 'D103' in out

    env.write_config(ignore='D100,D103')
    out, err, code = env.invoke()
    assert code == 0
    assert 'D100' not in out
    assert 'D103' not in out

    env.write_config(ignore='D10')
    _, err, code = env.invoke()
    assert code == 0
    assert 'D100' not in err
    assert 'D103' not in err


def test_sectionless_config_file(env):
    """Test that config files without a valid section name issue a warning."""
    with env.open('config.ini', 'wt') as conf:
        conf.write('[pdcstl]')
        config_path = conf.name

    _, err, code = env.invoke(f'--config={config_path}')
    assert code == 0
    assert 'Configuration file does not contain a pydocstyle section' in err

    with env.open('example.py', 'wt') as example:
        example.write(textwrap.dedent("""\
            def foo():
                pass
        """))

    with env.open('tox.ini', 'wt') as conf:
        conf.write('[pdcstl]\n')
        conf.write('ignore = D100')

    out, err, code = env.invoke()
    assert code == 1
    assert 'D100' in out
    assert 'file does not contain a pydocstyle section' not in err


@pytest.mark.parametrize(
    # Don't parametrize over 'pyproject.toml'
    # since this test applies only to '.ini' files
    'env', ['ini'], indirect=True
)
def test_multiple_lined_config_file(env):
    """Test that .ini files with multi-lined entries are parsed correctly."""
    with env.open('example.py', 'wt') as example:
        example.write(textwrap.dedent("""\
            class Foo(object):
                "Doc string"
                def foo():
                    pass
        """))

    select_string = ('D100,\n'
                     '  #D103,\n'
                     ' D204, D300 # Just remember - don\'t check D103!')
    env.write_config(select=select_string)

    out, err, code = env.invoke()
    assert code == 1
    assert 'D100' in out
    assert 'D204' in out
    assert 'D300' in out
    assert 'D103' not in out


@pytest.mark.parametrize(
    # Don't parametrize over 'tox.ini' since
    # this test applies only to '.toml' files
    'env', ['toml'], indirect=True
)
def test_accepts_select_error_code_list(env):
    """Test that .ini files with multi-lined entries are parsed correctly."""
    with env.open('example.py', 'wt') as example:
        example.write(textwrap.dedent("""\
            class Foo(object):
                "Doc string"
                def foo():
                    pass
        """))

    env.write_config(select=['D100', 'D204', 'D300'])

    out, err, code = env.invoke()
    assert code == 1
    assert 'D100' in out
    assert 'D204' in out
    assert 'D300' in out
    assert 'D103' not in out


def test_config_path(env):
    """Test that options are correctly loaded from a specific config file.

    Make sure that a config file passed via --config is actually used and that
    normal config file discovery is disabled.

    """
    with env.open('example.py', 'wt') as example:
        example.write(textwrap.dedent("""\
            def foo():
                pass
        """))

    # either my_config.ini or my_config.toml
    config_ext = env.config_name.split('.')[-1]
    config_name = 'my_config.' + config_ext

    env.write_config(ignore='D100')
    env.write_config(name=config_name, ignore='D103')

    out, err, code = env.invoke()
    assert code == 1
    assert 'D100' not in out
    assert 'D103' in out

    out, err, code = env.invoke('--config={} -d'
                                .format(env.get_path(config_name)))
    assert code == 1, out + err
    assert 'D100' in out
    assert 'D103' not in out


def test_non_existent_config(env):
    out, err, code = env.invoke('--config=does_not_exist')
    assert code == 2


def test_verbose(env):
    """Test that passing --verbose prints more information."""
    with env.open('example.py', 'wt') as example:
        example.write('"""Module docstring."""\n')

    out, _, code = env.invoke()
    assert code == 0
    assert 'example.py' not in out

    out, _, code = env.invoke(args="--verbose")
    assert code == 0
    assert 'example.py' in out


def test_count(env):
    """Test that passing --count correctly prints the error num."""
    with env.open('example.py', 'wt') as example:
        example.write(textwrap.dedent("""\
            def foo():
                pass
        """))

    out, err, code = env.invoke(args='--count')
    assert code == 1
    assert '2' in out
    # The error count should be in the last line of the output.
    # -2 since there is a newline at the end of the output.
    assert '2' == out.split('\n')[-2].strip()


def test_select_cli(env):
    """Test choosing error codes with `--select` in the CLI."""
    with env.open('example.py', 'wt') as example:
        example.write(textwrap.dedent("""\
            def foo():
                pass
        """))

    out, err, code = env.invoke(args="--select=D100")
    assert code == 1
    assert 'D100' in out
    assert 'D103' not in out


def test_select_config(env):
    """Test choosing error codes with `select` in the config file."""
    with env.open('example.py', 'wt') as example:
        example.write(textwrap.dedent("""\
            class Foo(object):
                "Doc string"
                def foo():
                    pass
        """))

    env.write_config(select="D100,D3")
    out, err, code = env.invoke()
    assert code == 1
    assert 'D100' in out
    assert 'D300' in out
    assert 'D103' not in out


def test_add_select_cli(env):
    """Test choosing error codes with --add-select in the CLI."""
    with env.open('example.py', 'wt') as example:
        example.write(textwrap.dedent("""\
            class Foo(object):
                "Doc string"
                def foo():
                    pass
        """))

    env.write_config(select="D100")
    out, err, code = env.invoke(args="--add-select=D204,D3")
    assert code == 1
    assert 'D100' in out
    assert 'D204' in out
    assert 'D300' in out
    assert 'D103' not in out


def test_add_ignore_cli(env):
    """Test choosing error codes with --add-ignore in the CLI."""
    with env.open('example.py', 'wt') as example:
        example.write(textwrap.dedent("""\
            class Foo(object):
                def foo():
                    pass
        """))

    env.write_config(select="D100,D101")
    out, err, code = env.invoke(args="--add-ignore=D101")
    assert code == 1
    assert 'D100' in out
    assert 'D101' not in out
    assert 'D103' not in out


def test_wildcard_add_ignore_cli(env):
    """Test choosing error codes with --add-ignore in the CLI."""
    with env.open('example.py', 'wt') as example:
        example.write(textwrap.dedent("""\
            class Foo(object):
                "Doc string"
                def foo():
                    pass
        """))

    env.write_config(select="D203,D300")
    out, err, code = env.invoke(args="--add-ignore=D30")
    assert code == 1
    assert 'D203' in out
    assert 'D300' not in out


@pytest.mark.parametrize(
    # Don't parametrize over 'pyproject.toml'
    # since this test applies only to '.ini' files
    'env', ['ini'], indirect=True
)
def test_ignores_whitespace_in_fixed_option_set(env):
    with env.open('example.py', 'wt') as example:
        example.write("class Foo(object):\n    'Doc string'")
    env.write_config(ignore="D100,\n  # comment\n  D300")
    out, err, code = env.invoke()
    assert code == 1
    assert 'D300' not in out
    assert err == ''


@pytest.mark.parametrize(
    # Don't parametrize over 'tox.ini' since
    # this test applies only to '.toml' files
    'env', ['toml'], indirect=True
)
def test_accepts_ignore_error_code_list(env):
    with env.open('example.py', 'wt') as example:
        example.write("class Foo(object):\n    'Doc string'")
    env.write_config(ignore=['D100', 'D300'])
    out, err, code = env.invoke()
    assert code == 1
    assert 'D300' not in out
    assert err == ''


def test_bad_wildcard_add_ignore_cli(env):
    """Test adding a non-existent error codes with --add-ignore."""
    with env.open('example.py', 'wt') as example:
        example.write(textwrap.dedent("""\
            class Foo(object):
                "Doc string"
                def foo():
                    pass
        """))

    env.write_config(select="D203,D300")
    out, err, code = env.invoke(args="--add-ignore=D3004")
    assert code == 1
    assert 'D203' in out
    assert 'D300' in out
    assert 'D3004' not in out
    assert ('Error code passed is not a prefix of any known errors: D3004'
            in err)


def test_overload_function(env):
    """Functions decorated with @overload trigger D418 error."""
    with env.open('example.py', 'wt') as example:
        example.write(textwrap.dedent('''\
        from typing import overload


        @overload
        def overloaded_func(a: int) -> str:
            ...


        @overload
        def overloaded_func(a: str) -> str:
            """Foo bar documentation."""
            ...


        def overloaded_func(a):
            """Foo bar documentation."""
            return str(a)

        '''))
    env.write_config(ignore="D100")
    out, err, code = env.invoke()
    assert code == 1
    assert 'D418' in out
    assert 'D103' not in out


def test_overload_async_function(env):
    """Async functions decorated with @overload trigger D418 error."""
    with env.open('example.py', 'wt') as example:
        example.write(textwrap.dedent('''\
        from typing import overload


        @overload
        async def overloaded_func(a: int) -> str:
            ...


        @overload
        async def overloaded_func(a: str) -> str:
            """Foo bar documentation."""
            ...


        async def overloaded_func(a):
            """Foo bar documentation."""
            return str(a)

        '''))
    env.write_config(ignore="D100")
    out, err, code = env.invoke()
    assert code == 1
    assert 'D418' in out
    assert 'D103' not in out


def test_overload_method(env):
    """Methods decorated with @overload trigger D418 error."""
    with env.open('example.py', 'wt') as example:
        example.write(textwrap.dedent('''\
        from typing import overload

        class ClassWithMethods:
            @overload
            def overloaded_method(a: int) -> str:
                ...


            @overload
            def overloaded_method(a: str) -> str:
                """Foo bar documentation."""
                ...


            def overloaded_method(a):
                """Foo bar documentation."""
                return str(a)

        '''))
    env.write_config(ignore="D100")
    out, err, code = env.invoke()
    assert code == 1
    assert 'D418' in out
    assert 'D102' not in out
    assert 'D103' not in out


def test_overload_method_valid(env):
    """Valid case for overload decorated Methods.

    This shouldn't throw any errors.
    """
    with env.open('example.py', 'wt') as example:
        example.write(textwrap.dedent('''\
        from typing import overload

        class ClassWithMethods:
            """Valid docstring in public Class."""

            @overload
            def overloaded_method(a: int) -> str:
                ...


            @overload
            def overloaded_method(a: str) -> str:
                ...


            def overloaded_method(a):
                """Foo bar documentation."""
                return str(a)

        '''))
    env.write_config(ignore="D100, D203")
    out, err, code = env.invoke()
    assert code == 0


def test_overload_function_valid(env):
    """Valid case for overload decorated functions.

    This shouldn't throw any errors.
    """
    with env.open('example.py', 'wt') as example:
        example.write(textwrap.dedent('''\
        from typing import overload


        @overload
        def overloaded_func(a: int) -> str:
            ...


        @overload
        def overloaded_func(a: str) -> str:
            ...


        def overloaded_func(a):
            """Foo bar documentation."""
            return str(a)

        '''))
    env.write_config(ignore="D100")
    out, err, code = env.invoke()
    assert code == 0


def test_overload_async_function_valid(env):
    """Valid case for overload decorated async functions.

    This shouldn't throw any errors.
    """
    with env.open('example.py', 'wt') as example:
        example.write(textwrap.dedent('''\
        from typing import overload


        @overload
        async def overloaded_func(a: int) -> str:
            ...


        @overload
        async def overloaded_func(a: str) -> str:
            ...


        async def overloaded_func(a):
            """Foo bar documentation."""
            return str(a)

        '''))
    env.write_config(ignore="D100")
    out, err, code = env.invoke()
    assert code == 0


def test_overload_nested_function(env):
    """Nested functions decorated with @overload trigger D418 error."""
    with env.open('example.py', 'wt') as example:
        example.write(textwrap.dedent('''\
        from typing import overload

        def function_with_nesting():
            """Valid docstring in public function."""
            @overload
            def overloaded_func(a: int) -> str:
                ...


            @overload
            def overloaded_func(a: str) -> str:
                """Foo bar documentation."""
                ...


            def overloaded_func(a):
                """Foo bar documentation."""
                return str(a)
            '''))
    env.write_config(ignore="D100")
    out, err, code = env.invoke()
    assert code == 1
    assert 'D418' in out
    assert 'D103' not in out


def test_overload_nested_function_valid(env):
    """Valid case for overload decorated nested functions.

    This shouldn't throw any errors.
    """
    with env.open('example.py', 'wt') as example:
        example.write(textwrap.dedent('''\
        from typing import overload

        def function_with_nesting():
            """Add a docstring to a function."""
            @overload
            def overloaded_func(a: int) -> str:
                ...


            @overload
            def overloaded_func(a: str) -> str:
                ...


            def overloaded_func(a):
                """Foo bar documentation."""
                return str(a)
            '''))
    env.write_config(ignore="D100")
    out, err, code = env.invoke()
    assert code == 0


def test_conflicting_select_ignore_config(env):
    """Test that select and ignore are mutually exclusive."""
    env.write_config(select="D100", ignore="D101")
    _, err, code = env.invoke()
    assert code == 2
    assert 'mutually exclusive' in err


def test_conflicting_select_convention_config(env):
    """Test that select and convention are mutually exclusive."""
    env.write_config(select="D100", convention="pep257")
    _, err, code = env.invoke()
    assert code == 2
    assert 'mutually exclusive' in err


def test_conflicting_ignore_convention_config(env):
    """Test that select and convention are mutually exclusive."""
    env.write_config(ignore="D100", convention="pep257")
    _, err, code = env.invoke()
    assert code == 2
    assert 'mutually exclusive' in err


def test_missing_docstring_in_package(env):
    """Make sure __init__.py files are treated as packages."""
    with env.open('__init__.py', 'wt') as init:
        pass  # an empty package file
    out, err, code = env.invoke()
    assert code == 1
    assert 'D100' not in out  # shouldn't be treated as a module
    assert 'D104' in out  # missing docstring in package


def test_illegal_convention(env):
    """Test that illegal convention names are dealt with properly."""
    _, err, code = env.invoke('--convention=illegal_conv')
    assert code == 2, err
    assert "Illegal convention 'illegal_conv'." in err
    assert 'Possible conventions' in err
    assert 'pep257' in err
    assert 'numpy' in err


def test_empty_select_cli(env):
    """Test excluding all error codes with `--select=` in the CLI."""
    with env.open('example.py', 'wt') as example:
        example.write(textwrap.dedent("""\
            def foo():
                pass
        """))

    _, _, code = env.invoke(args="--select=")
    assert code == 0


def test_empty_select_config(env):
    """Test excluding all error codes with `select=` in the config file."""
    with env.open('example.py', 'wt') as example:
        example.write(textwrap.dedent("""\
            def foo():
                pass
        """))

    env.write_config(select="")
    _, _, code = env.invoke()
    assert code == 0


def test_empty_select_with_added_error(env):
    """Test excluding all errors but one."""
    with env.open('example.py', 'wt') as example:
        example.write(textwrap.dedent("""\
            def foo():
                pass
        """))

    env.write_config(select="")
    out, err, code = env.invoke(args="--add-select=D100")
    assert code == 1
    assert 'D100' in out
    assert 'D101' not in out
    assert 'D103' not in out


def test_pep257_convention(env):
    """Test that the 'pep257' convention options has the correct errors."""
    with env.open('example.py', 'wt') as example:
        example.write(textwrap.dedent('''
            class Foo(object):


                """Docstring for this class"""
                def foo():
                    pass


            # Original PEP-257 example from -
            # https://www.python.org/dev/peps/pep-0257/
            def complex(real=0.0, imag=0.0):
                """Form a complex number.

                Keyword arguments:
                real -- the real part (default 0.0)
                imag -- the imaginary part (default 0.0)
                """
                if imag == 0.0 and real == 0.0:
                    return complex_zero
        '''))

    env.write_config(convention="pep257")
    out, err, code = env.invoke()
    assert code == 1
    assert 'D100' in out
    assert 'D211' in out
    assert 'D203' not in out
    assert 'D212' not in out
    assert 'D213' not in out
    assert 'D413' not in out


def test_numpy_convention(env):
    """Test that the 'numpy' convention options has the correct errors."""
    with env.open('example.py', 'wt') as example:
        example.write(textwrap.dedent('''
            class Foo(object):
                """Docstring for this class.

                returns
                 ------
                """
                def __init__(self):
                    pass
        '''))

    env.write_config(convention="numpy")
    out, err, code = env.invoke()
    assert code == 1
    assert 'D107' not in out
    assert 'D213' not in out
    assert 'D215' in out
    assert 'D405' in out
    assert 'D409' in out
    assert 'D414' in out
    assert 'D410' not in out
    assert 'D413' not in out


def test_google_convention(env):
    """Test that the 'google' convention options has the correct errors."""
    with env.open('example.py', 'wt') as example:
        example.write(textwrap.dedent('''
            def func(num1, num2, num_three=0):
                """Docstring for this function.

                Args:
                    num1 (int): Number 1.
                    num2: Number 2.
                """


            class Foo(object):
                """Docstring for this class.

                Attributes:

                    test: Test

                returns:
                """
                def __init__(self):
                    pass
        '''))

    env.write_config(convention="google")
    out, err, code = env.invoke()
    assert code == 1
    assert 'D107' in out
    assert 'D213' not in out
    assert 'D215' not in out
    assert 'D405' in out
    assert 'D409' not in out
    assert 'D410' not in out
    assert 'D412' in out
    assert 'D413' not in out
    assert 'D414' in out
    assert 'D417' in out


def test_config_file_inheritance(env):
    """Test configuration files inheritance.

    The test creates 2 configuration files:

    env_base
    +-- tox.ini
    |   This configuration will set `select=`.
    +-- A
        +-- tox.ini
        |   This configuration will set `inherit=false`.
        +-- test.py
            The file will contain code that violates D100,D103.

    When invoking pydocstyle, the first config file found in the base directory
    will set `select=`, so no error codes should be checked.
    The `A/tox.ini` configuration file sets `inherit=false` but has an empty
    configuration, therefore the default convention will be checked.

    We expect pydocstyle to ignore the `select=` configuration and raise all
    the errors stated above.

    """
    env.write_config(select='')
    env.write_config(prefix='A', inherit=False)

    with env.open(os.path.join('A', 'test.py'), 'wt') as test:
        test.write(textwrap.dedent("""\
            def bar():
                pass
        """))

    out, err, code = env.invoke()

    assert code == 1
    assert 'D100' in out
    assert 'D103' in out


def test_config_file_cumulative_add_ignores(env):
    """Test that add-ignore is cumulative.

    env_base
    +-- tox.ini
    |   This configuration will set `select=D100,D103` and `add-ignore=D100`.
    +-- base.py
    |   Will violate D100,D103
    +-- A
        +-- tox.ini
        |   This configuration will set `add-ignore=D103`.
        +-- a.py
            Will violate D100,D103.

    The desired result is that `base.py` will fail with D103 and
    `a.py` will pass.

    """
    env.write_config(select='D100,D103', add_ignore='D100')
    env.write_config(prefix='A', add_ignore='D103')

    test_content = textwrap.dedent("""\
        def foo():
            pass
    """)

    with env.open('base.py', 'wt') as test:
        test.write(test_content)

    with env.open(os.path.join('A', 'a.py'), 'wt') as test:
        test.write(test_content)

    out, err, code = env.invoke()

    err = parse_errors(out)

    assert code == 1
    assert 'base.py' in err, err
    assert 'a.py' not in err, err
    assert 'D100' not in err['base.py'], err
    assert 'D103' in err['base.py'], err


def test_config_file_cumulative_add_select(env):
    """Test that add-select is cumulative.

    env_base
    +-- tox.ini
    |   This configuration will set `select=` and `add-select=D100`.
    +-- base.py
    |   Will violate D100,D103
    +-- A
        +-- tox.ini
        |   This configuration will set `add-select=D103`.
        +-- a.py
            Will violate D100,D103.

    The desired result is that `base.py` will fail with D100 and
    `a.py` will fail with D100,D103.

    """
    env.write_config(select='', add_select='D100')
    env.write_config(prefix='A', add_select='D103')

    test_content = textwrap.dedent("""\
        def foo():
            pass
    """)

    with env.open('base.py', 'wt') as test:
        test.write(test_content)

    with env.open(os.path.join('A', 'a.py'), 'wt') as test:
        test.write(test_content)

    out, err, code = env.invoke()

    err = parse_errors(out)

    assert code == 1
    assert 'base.py' in err, err
    assert 'a.py' in err, err
    assert err['base.py'] == {'D100'}, err
    assert err['a.py'] == {'D100', 'D103'}, err


def test_config_file_convention_overrides_select(env):
    """Test that conventions override selected errors.

    env_base
    +-- tox.ini
    |   This configuration will set `select=D103`.
    +-- base.py
    |   Will violate D100.
    +-- A
        +-- tox.ini
        |   This configuration will set `convention=pep257`.
        +-- a.py
            Will violate D100.

    The expected result is that `base.py` will be clear of errors and
    `a.py` will violate D100.

    """
    env.write_config(select='D103')
    env.write_config(prefix='A', convention='pep257')

    test_content = ""

    with env.open('base.py', 'wt') as test:
        test.write(test_content)

    with env.open(os.path.join('A', 'a.py'), 'wt') as test:
        test.write(test_content)

    out, err, code = env.invoke()

    assert code == 1
    assert 'D100' in out, out
    assert 'base.py' not in out, out
    assert 'a.py' in out, out


def test_cli_overrides_config_file(env):
    """Test that the CLI overrides error codes selected in the config file.

    env_base
    +-- tox.ini
    |   This configuration will set `select=D103` and `match-dir=foo`.
    +-- base.py
    |   Will violate D100.
    +-- A
        +-- a.py
            Will violate D100,D103.

    We shall run with `--convention=pep257`.
    We expect `base.py` to be checked and violate `D100` and that `A/a.py` will
    not be checked because of `match-dir=foo` in the config file.

    """
    env.write_config(select='D103', match_dir='foo')

    with env.open('base.py', 'wt') as test:
        test.write("")

    env.makedirs('A')
    with env.open(os.path.join('A', 'a.py'), 'wt') as test:
        test.write(textwrap.dedent("""\
            def foo():
                pass
        """))

    out, err, code = env.invoke(args="--convention=pep257")

    assert code == 1
    assert 'D100' in out, out
    assert 'D103' not in out, out
    assert 'base.py' in out, out
    assert 'a.py' not in out, out


def test_cli_match_overrides_config_file(env):
    """Test that the CLI overrides the match clauses in the config file.

    env_base
    +-- tox.ini
    |   This configuration will set `match-dir=foo`.
    +-- base.py
    |   Will violate D100,D103.
    +-- A
        +-- tox.ini
        |   This configuration will set `match=bar.py`.
        +-- a.py
            Will violate D100.

    We shall run with `--match=a.py` and `--match-dir=A`.
    We expect `base.py` will not be checked and that `A/a.py` will be checked.

    """
    env.write_config(match_dir='foo')
    env.write_config(prefix='A', match='bar.py')

    with env.open('base.py', 'wt') as test:
        test.write(textwrap.dedent("""\
            def foo():
                pass
        """))

    with env.open(os.path.join('A', 'a.py'), 'wt') as test:
        test.write("")

    out, err, code = env.invoke(args="--match=a.py --match-dir=A")

    assert code == 1
    assert 'D100' in out, out
    assert 'D103' not in out, out
    assert 'base.py' not in out, out
    assert 'a.py' in out, out


def test_config_file_convention_overrides_ignore(env):
    """Test that conventions override ignored errors.

    env_base
    +-- tox.ini
    |   This configuration will set `ignore=D100,D103`.
    +-- base.py
    |   Will violate D100,D103.
    +-- A
        +-- tox.ini
        |   This configuration will set `convention=pep257`.
        +-- a.py
            Will violate D100,D103.

    The expected result is that `base.py` will be clear of errors and
    `a.py` will violate D103.

    """
    env.write_config(ignore='D100,D103')
    env.write_config(prefix='A', convention='pep257')

    test_content = textwrap.dedent("""\
        def foo():
            pass
    """)

    with env.open('base.py', 'wt') as test:
        test.write(test_content)

    with env.open(os.path.join('A', 'a.py'), 'wt') as test:
        test.write(test_content)

    out, err, code = env.invoke()

    assert code == 1
    assert 'D100' in out, out
    assert 'D103' in out, out
    assert 'base.py' not in out, out
    assert 'a.py' in out, out


def test_config_file_ignore_overrides_select(env):
    """Test that ignoring any error overrides selecting errors.

    env_base
    +-- tox.ini
    |   This configuration will set `select=D100`.
    +-- base.py
    |   Will violate D100,D101,D102.
    +-- A
        +-- tox.ini
        |   This configuration will set `ignore=D102`.
        +-- a.py
            Will violate D100,D101,D102.

    The expected result is that `base.py` will violate D100 and
    `a.py` will violate D100,D101.

    """
    env.write_config(select='D100')
    env.write_config(prefix='A', ignore='D102')

    test_content = textwrap.dedent("""\
        class Foo(object):
            def bar():
                pass
    """)

    with env.open('base.py', 'wt') as test:
        test.write(test_content)

    with env.open(os.path.join('A', 'a.py'), 'wt') as test:
        test.write(test_content)

    out, err, code = env.invoke()

    err = parse_errors(out)

    assert code == 1
    assert 'base.py' in err, err
    assert 'a.py' in err, err
    assert err['base.py'] == {'D100'}, err
    assert err['a.py'] == {'D100', 'D101'}, err


def test_config_file_nearest_to_checked_file(env):
    """Test that the configuration to each file is the nearest one.

    In this test there will be 2 identical files in 2 branches in the directory
    tree. Both of them will violate the same error codes, but their config
    files will contain different ignores.

    env_base
    +-- tox.ini
    |   This configuration will set `convention=pep257` and `add-ignore=D100`
    +-- base.py
    |   Will violate D100,D101,D102.
    +-- A
    |   +-- a.py
    |       Will violate D100,D101,D102.
    +-- B
        +-- tox.ini
        |   Will set `add-ignore=D101`
        +-- b.py
            Will violate D100,D101,D102.

    We should see that `a.py` and `base.py` act the same and violate
    D101,D102 (since they are both configured by `tox.ini`) and that
    `b.py` violates D102, since it's configured by `B/tox.ini` as well.

    """
    env.write_config(convention='pep257', add_ignore='D100')
    env.write_config(prefix='B', add_ignore='D101')

    test_content = textwrap.dedent("""\
        class Foo(object):
            def bar():
                pass
    """)

    with env.open('base.py', 'wt') as test:
        test.write(test_content)

    env.makedirs('A')
    with env.open(os.path.join('A', 'a.py'), 'wt') as test:
        test.write(test_content)

    with env.open(os.path.join('B', 'b.py'), 'wt') as test:
        test.write(test_content)

    out, err, code = env.invoke()

    err = parse_errors(out)

    assert code == 1
    assert 'base.py' in err, err
    assert 'a.py' in err, err
    assert 'b.py' in err, err
    assert err['base.py'] == {'D101', 'D102'}, err
    assert err['a.py'] == {'D101', 'D102'}, err
    assert err['b.py'] == {'D102'}, err


def test_config_file_nearest_match_re(env):
    """Test that the `match` and `match-dir` options are handled correctly.

    env_base
    +-- tox.ini
    |   This configuration will set `convention=pep257` and `add-ignore=D100`.
    +-- A
        +-- tox.ini
        |   Will set `match-dir=C`.
        +-- B
        |   +-- b.py
        |       Will violate D100,D103.
        +-- C
            +-- tox.ini
            |   Will set `match=bla.py`.
            +-- c.py
            |   Will violate D100,D103.
            +-- bla.py
                Will violate D100.

    We expect the call to pydocstyle to be successful, since `b.py` and
    `c.py` are not supposed to be found by the re.

    """
    env.write_config(convention='pep257', add_ignore='D100')
    env.write_config(prefix='A', match_dir='C')
    env.write_config(prefix=os.path.join('A', 'C'), match='bla.py')

    content = textwrap.dedent("""\
        def foo():
            pass
    """)

    env.makedirs(os.path.join('A', 'B'))
    with env.open(os.path.join('A', 'B', 'b.py'), 'wt') as test:
        test.write(content)

    with env.open(os.path.join('A', 'C', 'c.py'), 'wt') as test:
        test.write(content)

    with env.open(os.path.join('A', 'C', 'bla.py'), 'wt') as test:
        test.write('')

    _, _, code = env.invoke()

    assert code == 0


def test_syntax_error_multiple_files(env):
    """Test that a syntax error in a file doesn't prevent further checking."""
    for filename in ('first.py', 'second.py'):
        with env.open(filename, 'wt') as fobj:
            fobj.write("[")

    out, err, code = env.invoke(args="-v")
    assert code == 1
    assert 'first.py: Cannot parse file' in err
    assert 'second.py: Cannot parse file' in err


def test_indented_function(env):
    """Test that nested functions do not cause IndentationError."""
    env.write_config(ignore='D')
    with env.open("test.py", 'wt') as fobj:
        fobj.write(textwrap.dedent('''\
            def foo():
                def bar(a):
                    """A docstring

                    Args:
                        a : An argument.
                    """
                    pass
        '''))
    out, err, code = env.invoke(args="-v")
    assert code == 0
    assert "IndentationError: unexpected indent" not in err


def test_only_comment_file(env):
    """Test that file with only comments does only cause D100."""
    with env.open('comments.py', 'wt') as comments:
        comments.write(
            '#!/usr/bin/env python3\n'
            '# -*- coding: utf-8 -*-\n'
            '# Useless comment\n'
            '# Just another useless comment\n'
        )

    out, _, code = env.invoke()
    assert 'D100' in out
    out = out.replace('D100', '')
    for err in {'D1', 'D2', 'D3', 'D4'}:
        assert err not in out
    assert code == 1


def test_comment_plus_docstring_file(env):
    """Test that file with comments and docstring does not cause errors."""
    with env.open('comments_plus.py', 'wt') as comments_plus:
        comments_plus.write(
            '#!/usr/bin/env python3\n'
            '# -*- coding: utf-8 -*-\n'
            '# Useless comment\n'
            '# Just another useless comment\n'
            '"""Module docstring."""\n'
        )

    out, _, code = env.invoke()
    assert '' == out
    assert code == 0


def test_only_comment_with_noqa_file(env):
    """Test that file with noqa and only comments does not cause errors."""
    with env.open('comments.py', 'wt') as comments:
        comments.write(
            '#!/usr/bin/env python3\n'
            '# -*- coding: utf-8 -*-\n'
            '# Useless comment\n'
            '# Just another useless comment\n'
            '# noqa: D100\n'
        )

    out, _, code = env.invoke()
    assert 'D100' not in out
    assert code == 0


def test_comment_with_noqa_plus_docstring_file(env):
    """Test that file with comments, noqa, docstring does not cause errors."""
    with env.open('comments_plus.py', 'wt') as comments_plus:
        comments_plus.write(
            '#!/usr/bin/env python3\n'
            '# -*- coding: utf-8 -*-\n'
            '# Useless comment\n'
            '# Just another useless comment\n'
            '# noqa: D400\n'
            '"""Module docstring without period"""\n'
        )

    out, _, code = env.invoke()
    assert '' == out
    assert code == 0


def test_ignore_self_only_init(env):
    """Test that ignore_self_only_init works ignores __init__ with only self."""
    with env.open('example.py', 'wt') as example:
        example.write(textwrap.dedent("""\
            class Foo:
                def __init__(self):
                    pass
        """))

    env.write_config(ignore_self_only_init=True, select="D107")
    out, err, code = env.invoke()
    assert '' == out
    assert code == 0

def test_match_considers_basenames_for_path_args(env):
    """Test that `match` option only considers basenames for path arguments.

    The test environment consists of a single empty module `test_a.py`. The
    match option is set to a pattern that ignores test_ prefixed .py filenames.
    When pydocstyle is invoked with full path to `test_a.py`, we expect it to
    succeed since match option will match against just the file name and not
    full path.
    """
    # Ignore .py files prefixed with 'test_'
    env.write_config(select='D100', match='(?!test_).+.py')

    # Create an empty module (violates D100)
    with env.open('test_a.py', 'wt') as test:
        test.write('')

    # env.invoke calls pydocstyle with full path to test_a.py
    out, _, code = env.invoke(target='test_a.py')
    assert '' == out
    assert code == 0