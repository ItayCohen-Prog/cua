#!/usr/bin/env python3
"""Task-owned rootful Xwayland desktops for Omarchy. Python 3.11+, stdio MCP."""
import argparse
import contextlib
import ctypes
import fcntl
import json
import os
from pathlib import Path
import secrets
import select
import shutil
import signal
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
STATE = Path(os.environ.get('XDG_RUNTIME_DIR', f'/run/user/{os.getuid()}')) / 'cua-background'
IDLE_SECONDS = 900
ALLOWED = {
    'list_apps', 'list_windows', 'get_window_state', 'get_desktop_state',
    'get_accessibility_tree', 'verify_state', 'bring_to_front', 'set_window_frame',
    'click', 'double_click', 'right_click', 'drag', 'mouse_button_down',
    'mouse_drag', 'mouse_button_up', 'type_text', 'press_key', 'hotkey',
    'set_value', 'scroll', 'clipboard_read', 'clipboard_write', 'get_screen_size',
    'get_cursor_position', 'move_cursor', 'zoom', 'invoke_menu',
}


def run(args, **kwargs):
    return subprocess.check_output(args, text=True, timeout=10, **kwargs).strip()


def hypr(what):
    return json.loads(run(['hyprctl', '-j', what]))


def process_stamp(pid):
    try:
        # comm may itself contain spaces or parentheses.
        fields = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()
        return None if fields[0] == 'Z' else fields[19]
    except (OSError, IndexError):
        return None


def alive(record):
    return process_stamp(record['pid']) == record['stamp']


def identity(pid):
    return {'pid': pid, 'stamp': process_stamp(pid)}


@contextlib.contextmanager
def locked():
    STATE.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (STATE / 'lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def write_json(path, value):
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(value))
    temp.replace(path)


def terminate(record):
    if not record or not record.get('stamp') or not alive(record):
        return
    # Only the process group created for this session. Recheck before escalation.
    with contextlib.suppress(ProcessLookupError):
        os.killpg(record['pid'], signal.SIGTERM)
    deadline = time.monotonic() + 3
    while alive(record) and time.monotonic() < deadline:
        time.sleep(.05)
    if alive(record):
        with contextlib.suppress(ProcessLookupError):
            os.killpg(record['pid'], signal.SIGKILL)


def reap(directory):
    """Only directories with our lease and verified ownership are eligible."""
    lease = directory / 'lease.json'
    if not lease.exists():
        return
    data = json.loads(lease.read_text())
    terminate(data.get('worker'))
    shutil.rmtree(directory)


def reserve():
    with locked():
        leases = []
        for directory in STATE.glob('session-*'):
            if not (directory/'lease.json').exists():
                # A kill between mkdir and the atomic lease write can leave an empty directory.
                remnants = list(directory.iterdir())
                if all(p.name == 'lease.tmp' and p.is_file() for p in remnants):
                    for remnant in remnants:
                        remnant.unlink()
                    directory.rmdir()
                    continue
                raise RuntimeError(f'Incomplete nonempty lease requires inspection: {directory}')
            data = json.loads((directory / 'lease.json').read_text())
            if not alive(data['owner']):
                reap(directory)
            else:
                leases.append(data)
        occupied = {w['id'] for w in hypr('workspaces') if w['windows'] > 0}
        occupied.update(m['activeWorkspace']['id'] for m in hypr('monitors'))
        occupied.update(l['workspace'] for l in leases)
        workspace = next(n for n in range(1, 10000) if n not in occupied)
        displays = {l['display'] for l in leases}
        display = next(n for n in range(62001, 63000) if n not in displays
                       and not Path(f'/tmp/.X11-unix/X{n}').exists())
        directory = STATE / ('session-' + secrets.token_hex(12))
        directory.mkdir(mode=0o700)
        data = {'owner': identity(os.getpid()), 'workspace': workspace,
                'display': display, 'worker': None}
        write_json(directory / 'lease.json', data)
        return directory, data


def guardian(directory, owner):
    # pidfds track original processes, not later processes reusing their PIDs.
    descriptors = []
    try:
        try:
            owner_fd = os.pidfd_open(owner['pid'])
            descriptors.append(owner_fd)
        except ProcessLookupError:
            owner_fd = None
        while owner_fd is not None and alive(owner):
            if select.select([owner_fd], [], [], .1)[0]:
                break
            lease = directory / 'lease.json'
            if not lease.exists():
                return
            worker = json.loads(lease.read_text()).get('worker')
            if not worker:
                continue
            if not alive(worker):
                break
            try:
                worker_fd = os.pidfd_open(worker['pid'])
            except ProcessLookupError:
                break
            descriptors.append(worker_fd)
            select.select(descriptors, [], [])
            break
    finally:
        for fd in descriptors:
            os.close(fd)
    with locked():
        reap(directory)


class RPC:
    """One request at a time, bounded reads, no replay after uncertain delivery."""
    def __init__(self, process):
        self.p, self.counter, self.buffer = process, 0, b''

    def call(self, method, params, timeout=60):
        self.counter += 1
        message = {'jsonrpc': '2.0', 'id': self.counter, 'method': method, 'params': params}
        self.p.stdin.write((json.dumps(message) + '\n').encode())
        self.p.stdin.flush()
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if b'\n' not in self.buffer:
                if not select.select([self.p.stdout], [], [], max(0, end-time.monotonic()))[0]:
                    break
                data = os.read(self.p.stdout.fileno(), 65536)
                if not data:
                    raise RuntimeError('Private desktop closed; inspect the session log')
                self.buffer += data
                if len(self.buffer) > 100 * 1024 * 1024:
                    raise RuntimeError('MCP response exceeded 100 MiB')
                continue
            line, self.buffer = self.buffer.split(b'\n', 1)
            response = json.loads(line)
            if response.get('id') != self.counter:
                continue
            if 'error' in response:
                raise RuntimeError(response['error']['message'])
            return response['result']
        raise TimeoutError('Request timed out. It was not replayed; observe before retrying.')


class Session:
    def __init__(self, output_dir, driver, dependency_root=None):
        self.directory = None
        self.p = self.guard = self.log = None
        self.address = None
        self.driver = str(Path(driver).resolve(strict=True))
        output = Path(output_dir)
        if not output.is_absolute() or not output.is_dir():
            raise ValueError('output_dir must be an existing absolute directory')
        output = output.resolve()
        if (output in (Path('/'), Path.home(), Path('/tmp'), STATE)
                or STATE.is_relative_to(output) or output.is_relative_to(STATE)):
            raise ValueError('Use a dedicated task output directory, not home, /tmp, / or runtime')
        self.output = output
        self.last_used = time.monotonic()
        try:
            self.directory, self.lease = reserve()
            self.guard = subprocess.Popen(
                [sys.executable, str(HERE/'background.py'), 'guardian',
                 str(self.directory), json.dumps(self.lease['owner'])],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, start_new_session=True)
            home = self.directory / 'home'
            home.mkdir()
            uid = os.getuid()
            runtime = f'/run/user/{uid}'
            host = Path(os.environ['XDG_RUNTIME_DIR']) / os.environ['WAYLAND_DISPLAY']
            if not host.is_socket():
                raise RuntimeError('Host Wayland socket unavailable')
            # Keep /tmp and runtime sockets private. Network remains available to apps.
            args = ['bwrap', '--die-with-parent', '--unshare-pid', '--ro-bind', '/', '/',
                    '--bind', str(self.directory), str(self.directory),
                    '--dev', '/dev', '--proc', '/proc',
                    '--tmpfs', '/tmp', '--tmpfs', runtime,
                    '--bind', str(self.directory), str(self.directory),
                    '--bind', str(output), str(output),
                    '--ro-bind', str(host), runtime+'/cua-host']
            if dependency_root:
                root = Path(dependency_root).resolve(strict=True)
                args += ['--overlay-src', '/usr', '--overlay-src', str(root/'usr'),
                         '--ro-overlay', '/usr']
            for device in sorted(Path('/dev/dri').glob('renderD*')) + [
                    Path('/dev/nvidia0'), Path('/dev/nvidiactl'), Path('/dev/nvidia-uvm')]:
                if device.exists():
                    args += ['--dev-bind', str(device), str(device)]
            env = dict(os.environ)
            for key in list(env):
                if key.startswith(('HYPRLAND_', 'DBUS_', 'XDG_', 'QT_', 'GDK_', 'GTK_')) or key in (
                        'DISPLAY', 'WAYLAND_DISPLAY', 'XAUTHORITY', 'NOTIFY_SOCKET',
                        'DESKTOP_SESSION', 'SESSION_MANAGER', 'LD_PRELOAD', 'LD_LIBRARY_PATH'):
                    env.pop(key, None)
            env.update(HOME=str(home), XDG_RUNTIME_DIR=runtime,
                       XDG_CONFIG_HOME=str(home/'.config'), XDG_CACHE_HOME=str(home/'.cache'),
                       XDG_DATA_HOME=str(home/'.local/share'), XDG_STATE_HOME=str(home/'.local/state'),
                       XDG_SESSION_TYPE='x11', XDG_CURRENT_DESKTOP='Openbox',
                       DISPLAY=f':{self.lease["display"]}', QT_QPA_PLATFORM='xcb',
                       GDK_BACKEND='x11', SDL_VIDEODRIVER='x11', MOZ_ENABLE_WAYLAND='0',
                       CUA_DRIVER_RS_ENABLE_WAYLAND='0', GTK_USE_PORTAL='0',
                       ATSPI_DBUS_IMPLEMENTATION='dbus-daemon')
            args += ['--', sys.executable, str(HERE/'background.py'),
                     'worker', str(self.directory), self.driver]
            self.log = (self.directory/'session.log').open('wb')
            self.p = subprocess.Popen(args, env=env, stdin=subprocess.PIPE,
                                      stdout=subprocess.PIPE, stderr=self.log, start_new_session=True)
            self.lease['worker'] = identity(self.p.pid)
            with locked():
                write_json(self.directory/'lease.json', self.lease)
            self.rpc = RPC(self.p)
            self.rpc.call('ready', {})
            title = f'Xwayland on :{self.lease["display"]}'
            end = time.monotonic()+10
            while time.monotonic() < end:
                windows = [w for w in hypr('clients') if w['initialTitle'] == title]
                if windows:
                    window = windows[0]
                    # PID namespaces report host credentials to the compositor.
                    if not self.owns_pid(window['pid']):
                        raise RuntimeError('Window title belongs to another process')
                    self.address = window['address']
                    self.check_properties()
                    run(['hyprctl', 'dispatch', 'hl.dsp.window.move({window="address:'+
                         self.address+'",workspace="'+str(self.lease['workspace'])+'",follow=false})'])
                    self.validate()
                    return
                time.sleep(.1)
            raise RuntimeError('Protected Xwayland window did not appear')
        except BaseException as exc:
            detail = ''
            if self.directory and (self.directory/'session.log').exists():
                detail = (self.directory/'session.log').read_text(errors='replace')[-2500:]
            self.close()
            if isinstance(exc, Exception):
                raise RuntimeError(f'{exc}\n{detail}') from exc
            raise

    def owns_pid(self, pid):
        for _ in range(32):
            if pid == self.p.pid:
                return True
            try:
                pid = int(Path(f'/proc/{pid}/stat').read_text().rsplit(')',1)[1].split()[1])
            except (OSError, ValueError, IndexError):
                return False
            if pid <= 1:
                return False
        return False

    def check_properties(self):
        for prop in ('no_focus', 'render_unfocused'):
            value = run(['hyprctl', 'getprop', 'address:'+self.address, prop])
            if value not in ('1', 'true'):
                raise RuntimeError(f'Protection missing: {prop}={value}; install the Hyprland rule')

    def validate(self):
        if self.p.poll() is not None:
            raise RuntimeError('Private desktop process exited')
        windows = [w for w in hypr('clients') if w['address'] == self.address]
        if len(windows) != 1 or not self.owns_pid(windows[0]['pid']):
            raise RuntimeError('Owned desktop window is gone')
        if windows[0]['workspace']['id'] != self.lease['workspace']:
            raise RuntimeError('Desktop moved out of its reserved workspace; stopping input')
        # If the user enters the workspace, pause instead of manipulating what they see.
        if any(m['activeWorkspace']['id'] == self.lease['workspace'] for m in hypr('monitors')):
            raise RuntimeError('Reserved workspace is now visible; input is paused')
        others = [w for w in hypr('clients') if w['workspace']['id'] == self.lease['workspace']
                  and w['address'] != self.address]
        if others:
            raise RuntimeError('Reserved workspace contains a user window; input is paused')
        self.check_properties()
        self.last_used = time.monotonic()

    def call(self, name, args):
        self.validate()
        if name not in ALLOWED:
            raise ValueError('Tool is outside the isolated desktop API')
        try:
            return self.rpc.call('driver', {'name': name, 'arguments': args})
        except (TimeoutError, BrokenPipeError):
            # Never continue a protocol stream after uncertain action delivery.
            self.close()
            raise

    def launch(self, argv):
        self.validate()
        return self.rpc.call('launch', {'argv': argv})

    def status(self):
        return {'active': self.p is not None and self.p.poll() is None,
                'workspace': self.lease['workspace'], 'display': self.lease['display'],
                'output_dir': str(self.output), 'idle_timeout_seconds': IDLE_SECONDS,
                'session_dir': str(self.directory), 'window': self.address}

    def close(self):
        if self.directory:
            with locked():
                reap(self.directory)
        if self.p:
            with contextlib.suppress(subprocess.TimeoutExpired):
                self.p.wait(timeout=5)
            for stream in (self.p.stdin, self.p.stdout):
                stream.close()
        if self.guard:
            self.guard.terminate()
            self.guard.wait(timeout=5)
        if self.log:
            self.log.close()
        self.directory = None


def worker(directory, driver):
    """All Cua and application processes run exclusively inside the private display."""
    for forbidden in ('/dev/uinput', '/dev/input'):
        if Path(forbidden).exists():
            raise RuntimeError('Input isolation failed: '+forbidden)
    if os.environ['DISPLAY'].removeprefix(':') != str(json.loads((directory/'lease.json').read_text())['display']):
        raise RuntimeError('Private display mismatch')
    Path('/tmp/.X11-unix').mkdir(mode=0o1777, exist_ok=True)
    auth = directory/'Xauthority'
    auth.touch(mode=0o600)
    run(['xauth', '-f', str(auth), 'add', os.environ['DISPLAY'], '.', secrets.token_hex(16)])
    os.environ['XAUTHORITY'] = str(auth)
    os.environ.pop('WAYLAND_DISPLAY', None)
    children = []
    pointer = None
    try:
        xserver = subprocess.Popen(['Xwayland', os.environ['DISPLAY'], '-geometry', '1800x1000',
                                    '-nolisten', 'tcp', '-noreset', '-nokeymap', '-auth', str(auth)],
                                   env={**os.environ, 'WAYLAND_DISPLAY': 'cua-host'},
                                   stdin=subprocess.DEVNULL, stdout=sys.stderr)
        children.append(xserver)
        for _ in range(100):
            if xserver.poll() is not None:
                raise RuntimeError('Xwayland failed to start')
            try:
                run(['xprop', '-root', '_NET_SUPPORTING_WM_CHECK'], stderr=subprocess.DEVNULL)
                break
            except subprocess.CalledProcessError:
                time.sleep(.1)
        else:
            raise RuntimeError('Private X server unavailable')
        wm = subprocess.Popen(['openbox', '--config-file', str(HERE/'openbox.xml')],
                              stdin=subprocess.DEVNULL, stdout=sys.stderr)
        children.append(wm)
        for _ in range(100):
            if 'window id' in run(['xprop', '-root', '_NET_SUPPORTING_WM_CHECK']):
                break
            if wm.poll() is not None:
                raise RuntimeError('Openbox failed to start')
            time.sleep(.1)
        else:
            raise RuntimeError('Private window manager unavailable')
        # Initialize only this display's XTEST pointer before the first click.
        xlib = ctypes.CDLL('libX11.so.6')
        xtst = ctypes.CDLL('libXtst.so.6')
        xlib.XOpenDisplay.restype = ctypes.c_void_p
        xlib.XSync.argtypes = [ctypes.c_void_p, ctypes.c_int]
        xlib.XCloseDisplay.argtypes = [ctypes.c_void_p]
        xtst.XTestFakeMotionEvent.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_ulong]
        display = xlib.XOpenDisplay(None)
        if not display:
            raise RuntimeError('Private XTEST initialization failed')
        xtst.XTestFakeMotionEvent(display, -1, 900, 500, 0)
        xlib.XSync(display, 0)
        xlib.XCloseDisplay(display)
        # Start the bus after DISPLAY/XAUTHORITY are set. Its activated services
        # must inherit stderr, never the worker's JSON protocol stdout.
        read_fd, write_fd = os.pipe()
        try:
            bus = subprocess.Popen(['dbus-daemon', '--session', '--nofork', '--nopidfile',
                                    f'--print-address={write_fd}'], pass_fds=[write_fd],
                                   stdin=subprocess.DEVNULL, stdout=sys.stderr, stderr=sys.stderr)
            children.append(bus)
            os.close(write_fd)
            write_fd = None
            if not select.select([read_fd], [], [], 5)[0]:
                raise RuntimeError('Private D-Bus did not start')
            address = os.read(read_fd, 4096).decode().strip()
            if not address.startswith('unix:'):
                raise RuntimeError('Private D-Bus returned an invalid address')
            os.environ['DBUS_SESSION_BUS_ADDRESS'] = address
        finally:
            os.close(read_fd)
            if write_fd is not None:
                os.close(write_fd)
        p = subprocess.Popen([driver, 'mcp', '--direct'], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=sys.stderr)
        children.append(p)
        rpc = RPC(p)
        rpc.call('initialize', {'protocolVersion':'2025-06-18','capabilities':{},
                               'clientInfo':{'name':'omarchy-background','version':'0.1.0'}})
        p.stdin.write(b'{"jsonrpc":"2.0","method":"notifications/initialized"}\n')
        p.stdin.flush()
        schemas = {t['name']: t for t in rpc.call('tools/list', {})['tools'] if t['name'] in ALLOWED}
        from private_pointer import Pointer, NAMES, schemas as pointer_schemas
        pointer = Pointer(rpc)
        schemas.update(pointer_schemas())
        def dispatch(method, params):
            if method == 'ready':
                return {'ready': True}
            if method == 'schemas':
                return list(schemas.values())
            if method == 'launch':
                argv = params['argv']
                if not isinstance(argv, list) or not argv or not all(isinstance(a,str) and a for a in argv):
                    raise ValueError('argv must be a nonempty string array; no shell expansion')
                child = subprocess.Popen(argv, cwd=os.environ['HOME'], stdin=subprocess.DEVNULL, stdout=sys.stderr, stderr=sys.stderr)
                children.append(child)
                time.sleep(.15)
                if child.poll() is not None and child.returncode != 0:
                    raise RuntimeError(f'Application exited with code {child.returncode}')
                return {'pid':child.pid, 'display':os.environ['DISPLAY']}
            if method == 'driver':
                name, args = params['name'], dict(params['arguments'])
                if name not in schemas:
                    raise ValueError('Unsupported desktop tool')
                if name in NAMES:
                    return pointer.call(name, args)
                if pointer.held and name not in ('get_window_state', 'get_desktop_state', 'list_windows', 'get_cursor_position'):
                    raise ValueError('Release the held pointer before another input operation')
                props = schemas[name]['inputSchema'].get('properties', {})
                if 'delivery_mode' in props:
                    args['delivery_mode'] = 'foreground'
                result = rpc.call('tools/call', {'name':name,'arguments':args})
                pointer.observe(result)
                return result
            raise ValueError('Unknown worker method')
        serve(dispatch)
    finally:
        if pointer:
            with contextlib.suppress(Exception):
                pointer.close()
        for child in reversed(children):
            if child.poll() is None:
                child.terminate()
        # bwrap's PID namespace kills all remaining descendants when this exits.


def serve(dispatch):
    for line in sys.stdin:
        request = json.loads(line)
        if 'id' not in request:
            continue
        try:
            response = {'result':dispatch(request['method'],request.get('params',{}))}
        except Exception as exc:
            response = {'error':{'code':-32000,'message':str(exc)}}
        print(json.dumps({'jsonrpc':'2.0','id':request['id'],**response}),flush=True)


def tool(name, description, properties=None, required=None):
    return {'name':name, 'description':description, 'inputSchema':{'type':'object',
            'properties':properties or {}, 'required':required or [], 'additionalProperties':False}}


TOOLS = [
    tool('desktop_start', 'Reserve an unused Hyprland workspace and start a private desktop for this task. Save deliverables in output_dir; profiles are temporary. No host focus or cursor input.',
         {'output_dir':{'type':'string','description':'Existing absolute task output directory'}}, ['output_dir']),
    tool('desktop_launch', 'Launch an installed app inside the task desktop. Never use a host launcher. argv is executable plus arguments, without shell expansion.',
         {'argv':{'type':'array','items':{'type':'string'},'minItems':1}}, ['argv']),
    tool('desktop_tools', 'Return Cua control/capture tool schemas for the private desktop.'),
    tool('desktop_call', 'Call a Cua tool inside the private desktop. Use desktop_tools to inspect arguments, then list_windows and get_window_state. Window coordinates are client-local. Input focus is confined to this desktop.',
         {'name':{'type':'string'},'arguments':{'type':'object'}}, ['name','arguments']),
    tool('desktop_status', 'Inspect this task desktop and its resource ownership.'),
    tool('desktop_stop', 'Close all task-owned apps, remove temporary profiles/screenshots/sockets and release the workspace. Save/export results to output_dir first. Idempotent.'),
]


def mcp(driver, dependency_root):
    session = None
    def dispatch(method, params):
        nonlocal session
        if session and (session.directory is None or session.p.poll() is not None):
            session.close()
            session = None
        if method == 'initialize':
            return {'protocolVersion':'2025-06-18','capabilities':{'tools':{}},
                    'serverInfo':{'name':'omarchy-background','version':'0.1.0'}}
        if method == 'ping':
            return {}
        if method == 'tools/list':
            return {'tools':TOOLS}
        if method != 'tools/call':
            raise ValueError('Unsupported MCP method')
        name, args = params['name'], params.get('arguments',{})
        try:
            if name == 'desktop_start':
                if session:
                    raise RuntimeError('This task already owns a desktop; stop it before starting another')
                session = Session(args['output_dir'],driver,dependency_root)
                value = session.status()
            elif name == 'desktop_stop':
                if session:
                    session.close()
                    session = None
                value = {'active':False,'cleaned':True}
            elif name == 'desktop_status':
                value = session.status() if session else {'active':False}
            else:
                if not session:
                    raise RuntimeError('Call desktop_start first')
                if name == 'desktop_launch':
                    value = session.launch(args['argv'])
                elif name == 'desktop_tools':
                    value = session.rpc.call('schemas',{})
                elif name == 'desktop_call':
                    return session.call(args['name'],args['arguments'])
                else:
                    raise ValueError('Unknown desktop tool')
            return {'content':[{'type':'text','text':json.dumps(value)}]}
        except Exception as exc:
            return {'isError':True,'content':[{'type':'text','text':str(exc)}]}
    def stopped(_sig,_frame):
        raise SystemExit(0)
    signal.signal(signal.SIGTERM,stopped)
    signal.signal(signal.SIGINT,stopped)
    try:
        # Binary reads avoid TextIO buffering hiding a second queued MCP request.
        buffer = b''
        while True:
            wait = max(0, IDLE_SECONDS-(time.monotonic()-session.last_used)) if session else None
            if not select.select([sys.stdin],[],[],wait)[0]:
                session.close()
                session = None
                continue
            data = os.read(sys.stdin.fileno(),65536)
            if not data:
                break
            buffer += data
            while b'\n' in buffer:
                line,buffer = buffer.split(b'\n',1)
                request = json.loads(line)
                if 'id' not in request:
                    continue
                try:
                    result = {'result':dispatch(request['method'],request.get('params',{}))}
                except Exception as exc:
                    result = {'error':{'code':-32000,'message':str(exc)}}
                print(json.dumps({'jsonrpc':'2.0','id':request['id'],**result}),flush=True)
    finally:
        if session:
            session.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='mode',required=True)
    main = sub.add_parser('mcp')
    main.add_argument('--driver',required=True)
    main.add_argument('--dependency-root')
    internal = sub.add_parser('worker')
    internal.add_argument('directory',type=Path)
    internal.add_argument('driver')
    guard = sub.add_parser('guardian')
    guard.add_argument('directory',type=Path)
    guard.add_argument('owner')
    args = parser.parse_args()
    if args.mode == 'mcp':
        mcp(args.driver,args.dependency_root)
    elif args.mode == 'worker':
        worker(args.directory,args.driver)
    else:
        guardian(args.directory,json.loads(args.owner))
