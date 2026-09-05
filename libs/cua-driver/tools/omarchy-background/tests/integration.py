#!/usr/bin/env python3
"""Opt-in live Hyprland test. Creates only owned private desktops and temp outputs."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from background import RPC, STATE, Session, hypr, process_stamp


def check(result):
    assert not result.get('isError'), result
    return result


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--driver',required=True)
    parser.add_argument('--dependency-root')
    args=parser.parse_args()
    samples=[]
    owned_windows=set()
    done=threading.Event()
    def monitor():
        while not done.is_set():
            samples.append({'focus':hypr('activewindow').get('address'),
                            'cursor':hypr('cursorpos'),
                            'visible':[m['activeWorkspace']['id'] for m in hypr('monitors')]})
            time.sleep(.04)
    thread=threading.Thread(target=monitor)
    thread.start()
    try:
        with tempfile.TemporaryDirectory(prefix='cua-integration-',dir=Path.cwd()) as tmp:
            output=Path(tmp)
            fixture=output/'qt-fixture'
            flags=subprocess.check_output(['pkg-config','--cflags','--libs','Qt5Widgets'],text=True).split()
            subprocess.run(['g++',str(Path(__file__).with_name('qt_fixture.cpp')),'-o',str(fixture),*flags],check=True)
            first=second=None
            try:
                first=Session(str(output),args.driver,args.dependency_root)
                second=Session(str(output),args.driver,args.dependency_root)
                assert first.lease['workspace'] != second.lease['workspace']
                assert first.lease['display'] != second.lease['display']
                owned_windows.update((first.address,second.address))
                paths=[first.directory,second.directory]
                workers=[first.lease['worker'],second.lease['worker']]
                second.launch([str(fixture),str(output/'other.json'),'Other task sentinel'])
                first.launch([str(fixture),str(output/'target.json'),'Target'])
                first.launch([str(fixture),str(output/'sentinel.json'),'Same task sentinel'])
                time.sleep(1)
                windows=check(first.call('list_windows',{}))['structuredContent']['windows']
                assert len(windows)==2, windows
                target=next(w for w in windows if w['title']=='Target')
                target={k:target[k] for k in ('pid','window_id')}
                check(first.call('bring_to_front',target))
                before=check(first.call('get_window_state',{**target,'screenshot_out_file':str(output/'before.png')}))
                actions=[('click',{'x':300,'y':45}),('click',{'x':300,'y':112}),
                         ('type_text',{'text':'Background works: שלום ✓'}),
                         ('press_key',{'key':'F5'}),('hotkey',{'keys':['ctrl','shift','k']}),
                         ('click',{'x':400,'y':380,'button':'right'}),
                         ('click',{'x':400,'y':380,'count':2}),
                         ('scroll',{'x':400,'y':380,'direction':'down','amount':3}),
                         ('drag',{'from_x':18,'from_y':167,'to_x':500,'to_y':167})]
                for name,params in actions:
                    check(first.call(name,{**target,**params}))
                    time.sleep(.15)
                state=json.loads((output/'target.json').read_text())
                expected={'clicks':1,'text':'Background works: שלום ✓','keys':1,'hotkeys':1,
                          'rights':1,'doubles':1,'wheel':-360,'slider':83}
                assert {k:state[k] for k in expected} == expected, state
                for path in ('sentinel.json','other.json'):
                    state=json.loads((output/path).read_text())
                    assert all(state[k]==( '' if k=='text' else 0) for k in expected),state
                check(first.call('get_window_state',{**target,'screenshot_out_file':str(output/'after.png')}))
                assert (output/'before.png').read_bytes() != (output/'after.png').read_bytes()
                desktop=check(first.call('get_desktop_state',{'screenshot_out_file':str(output/'desktop.png')}))
                size=desktop['structuredContent']
                assert size['screen_width']>=1800 and size['screen_height']>=1000,size
                check(first.call('clipboard_write',{'text':'private clipboard שלום'}))
                assert check(first.call('clipboard_read',{'include_text':True}))['structuredContent']['text']=='private clipboard שלום'
                assert check(second.call('clipboard_read',{'include_text':True}))['structuredContent']['text']!='private clipboard שלום'
                check(first.call('move_cursor',{**target,'x':400,'y':380}))
                check(first.call('mouse_button_down',{**target,'x':500,'y':167}))
                check(first.call('mouse_drag',{'x':250,'y':167,'duration_ms':300}))
                check(first.call('mouse_button_up',{}))
                time.sleep(.2)
                assert 35<=json.loads((output/'target.json').read_text())['slider']<=45
                # Failed application launch reports the error, leaves the session usable.
                try:first.launch(['/does/not/exist/cua-test'])
                except RuntimeError:pass
                else:raise AssertionError('Invalid launch succeeded')
                check(first.call('list_windows',{}))
            finally:
                if second:second.close();second.close()
                if first:first.close();first.close()
            assert all(not p.exists() for p in paths)
            assert all(process_stamp(r['pid']) != r['stamp'] for r in workers)
            assert (output/'after.png').exists(), 'Cleanup removed a deliverable'
            # Real MCP disconnect and SIGKILL exercise the guardian, not a mock.
            for death in ('eof','kill'):
                cmd=[sys.executable,str(Path(__file__).resolve().parents[1]/'background.py'),
                     'mcp','--driver',args.driver]
                if args.dependency_root:cmd+=['--dependency-root',args.dependency_root]
                p=subprocess.Popen(cmd,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
                rpc=RPC(p)
                rpc.call('initialize',{})
                check(rpc.call('tools/call',{'name':'desktop_start','arguments':{'output_dir':str(output)}}))
                response=rpc.call('tools/call',{'name':'desktop_status','arguments':{}})
                status=json.loads(response['content'][0]['text'])
                owned_windows.add(status['window'])
                directory=Path(status['session_dir'])
                if death=='kill':p.kill()
                else:p.stdin.close()
                p.wait(timeout=10)
                end=time.monotonic()+10
                while directory.exists() and time.monotonic()<end:time.sleep(.1)
                assert not directory.exists(), f'{death} did not reclaim resources'
                p.stdout.close();p.stderr.close()
    finally:
        done.set();thread.join()
    focus=set(s['focus'] for s in samples)
    visible=set(tuple(s['visible']) for s in samples)
    assert not focus.intersection(owned_windows), 'An agent desktop took host focus'
    print(json.dumps({'passed':True,'host_samples':len(samples),'agent_window_focus_events':0,
                      'distinct_user_focus_targets':len(focus),
                      'distinct_visible_workspace_sets':len(visible),
                      'cursor_positions':len(set(tuple(s['cursor'].values()) for s in samples)),
                      'checks':['two concurrent desktops','separate displays/workspaces',
                                'window activation','nine input actions','Hebrew Unicode',
                                'same-session and cross-session input isolation','fresh capture',
                                'desktop capture','private clipboard','hover','persistent press/move/release','failed launch','idempotent stop',
                                'output preservation','EOF cleanup','SIGKILL guardian cleanup']}))

if __name__=='__main__':main()
