"""Behavioral hidden subgraders. Executed only in evaluator containers, never Agent.

No patch matching. Each group gets a fresh Python process and controlled env.
"""
import contextlib
import inspect
import json
import os
from pathlib import Path
import sys
import warnings

sys.path.insert(0, '/workspace/src')
import click
from click.testing import CliRunner

assert Path(click.__file__).is_relative_to('/workspace/src/click')
RUNNER = CliRunner()


def invoke(command, args=(), env=None, **kwargs):
    result = RUNNER.invoke(command, list(args), env={'FOO': None, 'BAR': None, **(env or {})}, standalone_mode=False, **kwargs)
    assert result.exit_code == 0, (result.exit_code, result.output, repr(result.exception))
    return result.return_value


def argument(args=(), env=None, default_map=None, scalar=False):
    @click.command()
    @click.argument('items', nargs=1 if scalar else -1, required=False, envvar='FOO', default='fallback' if scalar else ('fallback',))
    @click.pass_context
    def cli(ctx, items):
        return items, ctx.get_parameter_source('items').name
    return invoke(cli, args, env, default_map=default_map)


def precedence(state):
    if state == 'primary':
        assert argument(['a', 'b'], {'FOO':'env-a env-b'}) == (('a', 'b'), 'COMMANDLINE')
        assert argument(['a', 'a'], {'FOO':'env'})[0] == ('a', 'a')
    elif state == 'edge':
        assert argument(env={'FOO':'red blue'}) == (('red', 'blue'), 'ENVIRONMENT')
        assert argument(env={'FOO':''}) == (('fallback',), 'DEFAULT')
        assert argument(default_map={'items':['map']}) == (('map',), 'DEFAULT_MAP')
        assert argument(env={'FOO':'env'}, default_map={'items':['map']})[0] == ('env',)
        assert argument(['cli'], {'FOO':''}, {'items':['map']})[0] == ('cli',)
    elif state == 'exception':
        assert argument(['cli'], {'FOO':'env'})[1] == 'COMMANDLINE'
        assert argument(['cli'], default_map={'items':['map']})[1] == 'COMMANDLINE'
    else:
        assert argument(['cli'], {'FOO':'env'}, scalar=True) == ('cli', 'COMMANDLINE')
        assert argument(env={'FOO':'env'}, scalar=True) == ('env', 'ENVIRONMENT')


def flags(args=(), default=True, reverse=False):
    opts = [click.Option(['--without-x','setting'], flag_value=False), click.Option(['--with-x','setting'], flag_value=True, default=default)]
    if reverse:
        opts.reverse()
    @click.pass_context
    def callback(ctx, setting):
        return setting, ctx.get_parameter_source('setting').name
    return invoke(click.Command('cli', params=opts, callback=callback), args)


def flag_precedence(state):
    if state == 'primary':
        assert flags(['--without-x']) == (False, 'COMMANDLINE')
    elif state == 'edge':
        for reverse in (False, True):
            for default in (True, False, None):
                for args, expected in [(['--with-x'], True), (['--without-x'], False), (['--with-x','--without-x'],False), (['--without-x','--with-x'],True), (['--without-x','--without-x'],False)]:
                    assert flags(args, default, reverse)[0] is expected
    elif state == 'exception':
        assert flags(['--without-x'])[1] == 'COMMANDLINE'
        assert flags(['--with-x'])[1] == 'COMMANDLINE'
    else:
        for default in (True, False, None):
            assert flags(default=default) == (default, 'DEFAULT')
        @click.command()
        @click.option('--yes/--no', default=True)
        def cli(yes):
            return yes
        assert invoke(cli, ['--no']) is False
        assert invoke(cli) is True


def option(args=(), env=None, **kwargs):
    @click.command()
    @click.option('--value', envvar='FOO', **kwargs)
    def cli(value):
        return value
    return invoke(cli, args, env)


def multi(state):
    if state == 'primary':
        assert option(env={'FOO':'red blue red'}, multiple=True) == ('red','blue','red')
    elif state == 'edge':
        assert option(env={'FOO':'1 2'}, nargs=2, type=int) == (1,2)
        assert option(env={'FOO':'1 2 3 4'}, multiple=True, nargs=2, type=int) == ((1,2),(3,4))
        assert option(env={'FOO':'alice 7 bob 9'}, multiple=True, type=(str,int)) == (('alice',7),('bob',9))
        assert option(env={'FOO':os.pathsep.join(['/alpha space','/beta'])}, multiple=True, type=click.Path()) == ('/alpha space','/beta')
    elif state == 'exception':
        @click.command()
        @click.option('--value', multiple=True, type=int, envvar='FOO')
        def cli(value):
            pass
        result = RUNNER.invoke(cli, env={'FOO':'1 invalid'})
        assert result.exit_code == 2 and 'Invalid value' in result.output
    else:
        assert option(env={'FOO':'red blue'}) == 'red blue'
        assert option(env={'FOO':''}, multiple=True) == ()
        assert option(['--value','a','--value','b'], {'FOO':'ignored'}, multiple=True) == ('a','b')


def lifecycle_run(kind):
    events = []
    class Resource:
        def __enter__(self):
            events.append('enter')
            return self
        def __exit__(self, typ, value, tb):
            events.append(('release', None if typ is None else typ.__name__))
    @click.command()
    @click.pass_context
    def cli(ctx):
        ctx.with_resource(Resource())
        ctx.call_on_close(lambda: events.append('callback'))
        if kind == 'click':
            raise click.ClickException('visible failure')
        if kind == 'abort':
            ctx.abort()
        if kind == 'exit':
            ctx.exit(7)
        if kind == 'direct':
            raise click.exceptions.Exit(9)
        if kind == 'python':
            raise ValueError('unexpected')
    result = RUNNER.invoke(cli)
    expected = {'click':1,'abort':1,'exit':7,'direct':9,'python':1,'normal':0}[kind]
    assert result.exit_code == expected, (kind, result.exit_code)
    assert events[0] == 'enter' and events[1] == 'callback' and len(events) == 3, events
    assert events[2][0] == 'release'
    if kind == 'click':
        assert 'Error: visible failure' in result.output
    if kind == 'python':
        assert isinstance(result.exception, ValueError)
    return events


def lifecycle(state):
    if state == 'primary':
        lifecycle_run('click')
    elif state == 'edge':
        for kind in ('abort','exit','direct'):
            lifecycle_run(kind)
    elif state == 'exception':
        assert lifecycle_run('python')[-1] == ('release','ValueError')
        events = []
        class Suppress:
            def __enter__(self): return self
            def __exit__(self, typ, value, tb):
                events.append(typ)
                return typ is ValueError
        ctx = click.Context(click.Command('cli'))
        with ctx:
            ctx.with_resource(Suppress())
            raise ValueError('suppressed')
        assert events == [ValueError]
        assert click.get_current_context(silent=True) is None
    else:
        lifecycle_run('normal')
        events = []
        ctx = click.Context(click.Command('cli'))
        with ctx:
            ctx.call_on_close(lambda:events.append('outer'))
            with ctx:
                ctx.call_on_close(lambda:events.append('inner'))
            assert not events
        ctx.close()
        assert events == ['inner','outer']


def group_fixture():
    root = click.Group('root')
    command = click.Command('build-assets', callback=lambda:'built')
    root.add_command(command)
    root.add_command(command, 'asset-alias')
    root.add_command(click.Command('plain', callback=lambda:'plain'))
    root.add_command(click.Command('secret', hidden=True, callback=lambda:'secret'))
    nested = click.Group('nested-group')
    nested.add_command(click.Command('child-task', callback=lambda:'child'))
    root.add_command(nested)
    return root, command


def routing(state):
    root, command = group_fixture()
    ctx = click.Context(root)
    if state == 'primary':
        assert root.get_command(ctx,'build-assets') is command
        assert invoke(root,['build-assets']) == 'built'
    elif state == 'edge':
        assert root.list_commands(ctx) == sorted(['build-assets','asset-alias','plain','secret','nested-group'])
        result = RUNNER.invoke(root,['--help'])
        assert result.exit_code == 0 and 'build-assets' in result.output and 'asset-alias' in result.output
        assert invoke(root,['asset-alias']) == 'built'
        assert invoke(root,['nested-group','child-task']) == 'child'
        assert 'child-task' in RUNNER.invoke(root,['nested-group','--help']).output
    elif state == 'exception':
        for name in ('unknown','build_assets'):
            assert root.get_command(ctx,name) is None
            result = RUNNER.invoke(root,[name])
            assert result.exit_code == 2 and 'No such command' in result.output

        # A registered underscore is a literal command name. Hyphen/underscore
        # equivalence would silently expand the accepted command language.
        underscore_root = click.Group('underscore-root')
        underscore_command = click.Command('under_score', callback=lambda:'underscore')
        underscore_root.add_command(underscore_command)
        underscore_ctx = click.Context(underscore_root)
        assert underscore_root.get_command(underscore_ctx, 'under_score') is underscore_command
        assert invoke(underscore_root, ['under_score']) == 'underscore'
        assert underscore_root.get_command(underscore_ctx, 'under-score') is None
        result = RUNNER.invoke(underscore_root, ['under-score'])
        assert result.exit_code == 2 and 'No such command' in result.output
    else:
        assert invoke(root,['plain']) == 'plain'
        assert invoke(root,['secret']) == 'secret'
        assert 'secret' not in RUNNER.invoke(root,['--help']).output
        assert invoke(root,['PLAIN'], token_normalize_func=str.lower) == 'plain'

        # Normalization is a fallback, not an override for an exact registered
        # spelling. Both commands are legal and must remain distinguishable.
        collision_root = click.Group('collision-root')
        collision_root.add_command(click.Command('MiXeD', callback=lambda:'exact'))
        collision_root.add_command(click.Command('mixed', callback=lambda:'normalized'))
        assert invoke(collision_root, ['MiXeD'], token_normalize_func=str.lower) == 'exact'
        assert invoke(collision_root, ['MIXED'], token_normalize_func=str.lower) == 'normalized'


def warning_contract(state):
    ctx = click.Context(click.Command('cli'))
    ctx._protected_args = ['one']
    with warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter('always')
        line = inspect.currentframe().f_lineno + 1
        value = ctx.protected_args
        if state == 'primary':
            assert str(captured[0].message) == "'protected_args' is deprecated and will be removed in Click 9.0. 'args' will contain remaining unparsed tokens."
        elif state == 'edge':
            assert captured[0].category is DeprecationWarning
            assert captured[0].filename == __file__ and captured[0].lineno == line
        elif state == 'exception':
            value = ctx.protected_args
            assert len(captured) == 2 and str(captured[0].message) == str(captured[1].message)
        else:
            assert value is ctx._protected_args and value == ['one']
            assert ctx.args == [] and ctx.params == {}
            assert argument(['cli'], scalar=True) == ('cli','COMMANDLINE')


GRADERS = {'SK01-T01':precedence,'SK01-T02':flag_precedence,'SK02-T01':multi,'SK05-T01':lifecycle,'SK06-T01':routing,'NS-01':warning_contract}

if __name__ == '__main__':
    GRADERS[sys.argv[1]](sys.argv[2])
    print(json.dumps({'state':sys.argv[2], 'passed':True}))
