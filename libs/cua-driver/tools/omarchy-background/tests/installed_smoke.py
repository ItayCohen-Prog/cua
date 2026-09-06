#!/usr/bin/env python3
"""Check the installed MCP, /tmp output mounts, and worker-crash reclamation."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from background import RPC

record=json.loads((Path.home()/'.local/share/cua-background/installation.json').read_text())
command=[record['mcp']['command'],*record['mcp']['args']]
with tempfile.TemporaryDirectory(prefix='cua-installed-') as tmp:
    output=Path(tmp)
    fixture=output/'qt-fixture'
    flags=subprocess.check_output(['pkg-config','--cflags','--libs','Qt5Widgets'],text=True).split()
    subprocess.run(['g++',str(Path(__file__).with_name('qt_fixture.cpp')),'-o',str(fixture),*flags],check=True)
    with (output/'mcp.log').open('wb') as log:
        p=subprocess.Popen(command,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=log)
        rpc=RPC(p)
        def call(name,args=None):
            result=rpc.call('tools/call',{'name':name,'arguments':args or {}})
            assert not result.get('isError'),result
            return result
        def data(result):return json.loads(result['content'][0]['text'])
        try:
            rpc.call('initialize',{})
            assert len(rpc.call('tools/list',{})['tools'])>=10
            first=data(call('desktop_start',{'output_dir':str(output)}))
            call('desktop_launch',{'argv':[str(fixture),str(output/'state.json'),'Installed MCP fixture']})
            time.sleep(.5)
            windows=call('desktop_call',{'name':'list_windows','arguments':{}})['structuredContent']['windows']
            target={k:windows[0][k] for k in ('pid','window_id')}
            call('desktop_call',{'name':'get_window_state','arguments':{**target,'screenshot_out_file':str(output/'proof.png')}})
            call('desktop_call',{'name':'click','arguments':{**target,'x':300,'y':45}})
            time.sleep(.1)
            assert json.loads((output/'state.json').read_text())['clicks']==1
            call('desktop_stop')
            assert not Path(first['session_dir']).exists()
            assert (output/'proof.png').stat().st_size>100
            second=data(call('desktop_start',{'output_dir':str(output)}))
            directory=Path(second['session_dir'])
            lease=json.loads((directory/'lease.json').read_text())
            os.kill(lease['worker']['pid'],signal.SIGKILL)
            end=time.monotonic()+10
            while directory.exists() and time.monotonic()<end:time.sleep(.1)
            assert not directory.exists(),'Worker crash did not reclaim the lease'
            assert data(call('desktop_status'))=={'active':False}
            call('desktop_start',{'output_dir':str(output)})
            call('desktop_stop')
        finally:
            p.stdin.close();p.wait(timeout=10);p.stdout.close()
print(json.dumps({'installed_mcp':True,'tmp_output_mount':True,'qt_click':True,
                  'output_preserved_after_stop':True,'worker_crash_cleanup':True,'same_mcp_restart':True}))
