#!/usr/bin/env python3
"""Live tests for observation gating, durable review, reconnect, and reclamation."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import background as bg
from supervisor import Client, sessions


def wait_until(predicate, timeout=12):
    end=time.monotonic()+timeout
    while time.monotonic()<end:
        if predicate():return
        time.sleep(.1)
    raise AssertionError('Timed out')


def checked(result):
    assert not result.get('isError'), result
    return result


def main():
    driver='/home/bram/.local/share/cua-background/cua-driver'
    deps='/home/bram/.local/share/cua-background/dependencies'
    before={s['session_id'] for s in sessions()}
    host_before={k:bg.hypr(k) for k in ('activewindow','cursorpos','monitors')}
    with tempfile.TemporaryDirectory(prefix='cua-review-',dir=Path.cwd()) as tmp:
        output=Path(tmp)
        fixture=output/'fixture'
        flags=subprocess.check_output(['pkg-config','--cflags','--libs','Qt5Widgets'],text=True).split()
        subprocess.run(['g++',str(ROOT/'tests/qt_fixture.cpp'),'-o',str(fixture),*flags],check=True)
        # Exercise the real Session under a visible-workspace snapshot. No host
        # workspace is switched: the observation branch sees the live owned window.
        s=bg.Session(tmp,driver,deps)
        try:
            s.launch([str(fixture),str(output/'direct.json'),'Observation fixture'])
            time.sleep(.5)
            windows=checked(s.call('list_windows',{}))['structuredContent']['windows']
            target={k:windows[0][k] for k in ('pid','window_id')}
            devices=s.rpc.call('input_mode',{'agent':True})
            assert all(not d['enabled'] for d in devices if d['name'].startswith('xwayland-'))
            assert all(d['enabled'] for d in devices if 'XTEST' in d['name'])
            real_hypr=bg.hypr
            with patch.object(bg,'hypr',side_effect=lambda name:[{'activeWorkspace':{'id':s.lease['workspace']}}] if name=='monitors' else real_hypr(name)):
                checked(s.call('get_window_state',target))
                checked(s.call('click',{**target,'x':300,'y':45}))
            wait_until(lambda:json.loads((output/'direct.json').read_text())['clicks']==1)
            s.control('user')
            devices=s.rpc.call('input_mode',{'agent':False})
            assert all(d['enabled'] for d in devices if d['name'].startswith('xwayland-'))
            checked(s.call('get_window_state',target))
            try:s.call('click',{**target,'x':300,'y':45})
            except RuntimeError as e:assert 'User has control' in str(e)
            else:raise AssertionError('Agent input allowed during review')
            s.control('agent')
            checked(s.call('click',{**target,'x':300,'y':45}))
            wait_until(lambda:json.loads((output/'direct.json').read_text())['clicks']==2)
        finally:s.close()

        cmd=[sys.executable,str(ROOT/'background.py'),'mcp','--driver',driver,'--dependency-root',deps]
        # Both EOF and unexpected client death preserve an open app for review.
        for ending in ('eof','kill','finish'):
            p=subprocess.Popen(cmd,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL)
            rpc=bg.RPC(p)
            def tool(name,args={}):return checked(rpc.call('tools/call',{'name':name,'arguments':args}))
            session_id=None
            try:
                rpc.call('initialize',{})
                started=json.loads(tool('desktop_start',{'output_dir':tmp})['content'][0]['text'])
                session_id=started['session_id'];client=Client(session_id)
                tool('desktop_launch',{'argv':[str(fixture),str(output/(ending+'.json')),'Review '+ending]})
                time.sleep(.4)
                if ending=='finish':tool('desktop_finish')
                if ending=='kill':p.kill()
                else:p.stdin.close()
                p.wait(timeout=10)
                wait_until(lambda:client.call('status')['review_retained'])
                assert any(x['session_id']==session_id for x in sessions())
                client.call('attach')
                windows=checked(client.call('driver',{'name':'list_windows','arguments':{}}))['structuredContent']['windows']
                target={k:windows[0][k] for k in ('pid','window_id')}
                try:client.call('driver',{'name':'click','arguments':{**target,'x':300,'y':45}})
                except RuntimeError as e:assert 'User has control' in str(e)
                else:raise AssertionError('Reattach silently granted input')
                client.call('control',{'mode':'agent'})
                checked(client.call('driver',{'name':'get_window_state','arguments':target}))
                checked(client.call('driver',{'name':'click','arguments':{**target,'x':300,'y':45}}))
                wait_until(lambda:json.loads((output/(ending+'.json')).read_text())['clicks']==1)
                if ending=='finish':
                    # Close the actual last app, then verify automatic reclamation.
                    checked(client.call('driver',{'name':'hotkey','arguments':{**target,'keys':['alt','F4']}}))
                    wait_until(lambda:not client.directory.exists())
                else:
                    client.call('stop')
                    assert not client.directory.exists()
            finally:
                if p.poll() is None:p.terminate();p.wait(timeout=10)
                if session_id and Client(session_id).exists():Client(session_id).call('stop')
                if not p.stdin.closed:p.stdin.close()
                p.stdout.close()
        assert {s['session_id'] for s in sessions()}==before
    host_after={k:bg.hypr(k) for k in ('activewindow','cursorpos','monitors')}
    assert host_before['activewindow'].get('address')==host_after['activewindow'].get('address'),'Host focus changed; investigate user activity versus controller'
    assert [m['activeWorkspace'] for m in host_before['monitors']]==[m['activeWorkspace'] for m in host_after['monitors']],'Host visible workspaces changed'
    print(json.dumps({'passed':True,'tests':['private physical input disabled; XTEST enabled','visible-workspace snapshot permits real Qt click','user handoff restores private physical input','read-only review; mutations rejected','agent resume','EOF retains open app','SIGKILL client retains open app','finish retains open app','reconnect preserves control mode','last-app-close cleanup','explicit stop cleanup','host focus and visible workspaces unchanged'],'host_cursor_unchanged':host_before['cursorpos']==host_after['cursorpos']}))


if __name__=='__main__':main()
