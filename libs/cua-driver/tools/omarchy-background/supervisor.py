"""One serialized supervisor per desktop; MCP connections do not own app lifetime."""
import argparse
import contextlib
import json
import os
from pathlib import Path
import re
import select
import signal
import socket
import subprocess
import sys
import time

from background import HERE, STATE, IDLE_SECONDS, Session, alive, identity, write_json


def directory_for(session_id):
    if not re.fullmatch(r'session-[0-9a-f]{24}', session_id):
        raise ValueError('Invalid session_id')
    return STATE / session_id


def sessions():
    result = []
    for file in STATE.glob('session-*/status.json'):
        with contextlib.suppress(OSError, ValueError):
            data = json.loads(file.read_text())
            lease = json.loads((file.parent/'lease.json').read_text())
            if alive(lease['owner']) and alive(lease['worker']):
                result.append(data)
    return result


class Client:
    def __init__(self, session_id):
        self.directory = directory_for(session_id)

    def exists(self):
        return (self.directory/'control.sock').exists()

    @classmethod
    def start(cls, output, driver, dependencies=None, lifetime='review'):
        if lifetime not in ('review', 'disposable'):
            raise ValueError('lifetime must be review or disposable')
        command = [sys.executable, str(HERE/'supervisor.py'), 'serve',
                   '--output', output, '--driver', driver, '--lifetime', lifetime,
                   '--controller', json.dumps(identity(os.getpid()))]
        if dependencies:
            command += ['--dependencies', dependencies]
        p = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                             stderr=subprocess.DEVNULL, start_new_session=True)
        try:
            if not select.select([p.stdout], [], [], 90)[0]:
                raise TimeoutError('Desktop supervisor startup timed out')
            response = json.loads(p.stdout.readline())
            if 'error' in response:
                raise RuntimeError(response['error'])
            return cls(response['session_id'])
        except BaseException:
            p.terminate()
            p.wait(timeout=10)
            raise
        finally:
            p.stdout.close()

    def call(self, method, arguments=None):
        with socket.socket(socket.AF_UNIX) as connection:
            connection.settimeout(70)
            connection.connect(str(self.directory/'control.sock'))
            connection.sendall((json.dumps({'method':method,'arguments':arguments or {},
                                            'controller':identity(os.getpid())})+'\n').encode())
            with connection.makefile('rb') as stream:
                response = json.loads(stream.readline(100*1024*1024))
        if 'error' in response:
            raise RuntimeError(response['error'])
        return response['result']


def supervise(args):
    session = None
    controller = json.loads(args.controller)
    closing = False
    launched = False
    launch_time = 0
    last_poll = 0
    def stopped(_sig, _frame):
        raise SystemExit(0)
    signal.signal(signal.SIGTERM, stopped)
    signal.signal(signal.SIGINT, stopped)
    try:
        session = Session(args.output, args.driver, args.dependencies)
        directory = session.directory
        def status():
            value = {**session.status(), 'session_id':directory.name,
                     'lifetime':args.lifetime, 'controller':controller,
                     'review_retained':controller is None and session.mode == 'user'}
            write_json(directory/'status.json', value)
            return value

        def release(keep_open):
            nonlocal controller, closing
            if keep_open and launched and session.rpc.call('has_windows', {}):
                session.control('user')
                controller = None
                return status()
            closing = True
            return {'active':False, 'cleaned':True}

        def dispatch(request):
            nonlocal controller, launched, launch_time, closing
            method, params = request['method'], request.get('arguments', {})
            caller = request['controller']
            if method == 'status':
                return status()
            if method == 'attach':
                if controller and alive(controller) and controller != caller:
                    raise RuntimeError('Another agent controls this desktop')
                controller = caller
                session.last_used = time.monotonic()
                return status()
            # CLI user controls may stop or take over a retained session, but cannot
            # impersonate the attached agent. Same-UID clients are trusted, not sandboxed.
            if controller and controller != caller and alive(controller):
                raise RuntimeError('Another agent controls this desktop; ask it to hand control over')
            if method == 'stop':
                closing = True
                return {'active':False, 'cleaned':True}
            if method == 'finish':
                return release(params.get('keep_open', True))
            if method == 'disconnect':
                return release(args.lifetime == 'review')
            if method == 'control':
                result = session.control(params['mode'])
                if params['mode'] == 'agent':
                    controller = caller
                return status()
            if controller != caller:
                raise RuntimeError('Attach to this desktop before using it')
            if method == 'launch':
                value = session.launch(params['argv'])
                launched = True
                launch_time = time.monotonic()
                return value
            if method == 'schemas':
                return session.rpc.call('schemas', {})
            if method == 'driver':
                return session.call(params['name'], params['arguments'])
            raise ValueError('Unknown supervisor method')

        with socket.socket(socket.AF_UNIX) as server:
            server.bind(str(directory/'control.sock'))
            os.chmod(directory/'control.sock', 0o600)
            server.listen(8)
            print(json.dumps(status()), flush=True)
            while not closing:
                if session.p.poll() is not None or not directory.exists():
                    break
                now = time.monotonic()
                if controller and (not alive(controller) or
                                   now-session.last_used > IDLE_SECONDS):
                    release(args.lifetime == 'review')
                    if closing:
                        break
                if launched and now-launch_time > 3 and now-last_poll > 2:
                    # Leaving the last app closes this desktop, even after MCP detaches.
                    if not session.rpc.call('has_windows', {}):
                        break
                    last_poll = now
                if not select.select([server], [], [], .2)[0]:
                    continue
                connection, _ = server.accept()
                with connection:
                    connection.settimeout(5)
                    with connection.makefile('rb') as stream:
                        try:
                            line = stream.readline(1024*1024)
                            request = json.loads(line)
                            # Linux peer credentials bind the declared controller to the caller.
                            import struct
                            pid, uid, _ = struct.unpack('3i', connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
                            if uid != os.getuid() or request['controller'] != identity(pid):
                                raise ValueError('Controller identity mismatch')
                            value = dispatch(request)
                            if closing:
                                session.close()
                            response = {'result':value}
                        except Exception as exc:
                            response = {'error':str(exc)}
                        with contextlib.suppress(BrokenPipeError, ConnectionResetError):
                            connection.sendall((json.dumps(response)+'\n').encode())
    except Exception as exc:
        if session is None:
            print(json.dumps({'error':str(exc)}), flush=True)
        else:
            print(str(exc), file=sys.stderr)
    finally:
        if session:
            session.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    server = sub.add_parser('serve')
    server.add_argument('--output', required=True)
    server.add_argument('--driver', required=True)
    server.add_argument('--dependencies')
    server.add_argument('--lifetime', choices=['review','disposable'], required=True)
    server.add_argument('--controller', required=True)
    sub.add_parser('list')
    for name in ('status','stop','control'):
        command = sub.add_parser(name)
        command.add_argument('session_id')
        if name == 'control':
            command.add_argument('mode', choices=['user','agent'])
    args = parser.parse_args()
    if args.command == 'serve':
        supervise(args)
    elif args.command == 'list':
        print(json.dumps(sessions(), indent=2))
    else:
        print(json.dumps(Client(args.session_id).call(args.command,
                         {'mode':args.mode} if args.command == 'control' else {}), indent=2))


if __name__ == '__main__':
    main()
