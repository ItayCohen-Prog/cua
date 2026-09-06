import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import background as bg

class Lifecycle(unittest.TestCase):
    def test_allocation_excludes_occupied_visible_and_reserved_workspaces(self):
        def hypr(name):
            return {'workspaces':[{'id':1,'windows':3},{'id':4,'windows':0}],
                    'monitors':[{'activeWorkspace':{'id':2}},{'activeWorkspace':{'id':3}}]}[name]
        with tempfile.TemporaryDirectory() as tmp, patch.object(bg,'STATE',Path(tmp)), patch.object(bg,'hypr',hypr):
            one,a=bg.reserve();two,b=bg.reserve()
            self.assertEqual((a['workspace'],b['workspace']),(4,5))
            self.assertNotEqual(a['display'],b['display'])
            bg.reap(one);bg.reap(two)
            self.assertEqual([p.name for p in Path(tmp).iterdir()],['lock'])

    def test_stale_and_interrupted_allocation_are_reclaimed(self):
        def hypr(name):return []
        with tempfile.TemporaryDirectory() as tmp, patch.object(bg,'STATE',Path(tmp)), patch.object(bg,'hypr',hypr):
            empty=Path(tmp)/'session-interrupted';empty.mkdir()
            (empty/'lease.tmp').write_text('{unfinished')
            stale=Path(tmp)/'session-stale';stale.mkdir()
            bg.write_json(stale/'lease.json',{'owner':{'pid':999999999,'stamp':'old'},'worker':None,'workspace':1,'display':62001})
            path,data=bg.reserve()
            self.assertFalse(empty.exists());self.assertFalse(stale.exists())
            self.assertEqual(data['workspace'],1)
            bg.reap(path)

    def test_reused_pid_is_never_signalled(self):
        with patch.object(bg,'process_stamp',return_value='new'), patch.object(bg.os,'killpg') as kill:
            bg.terminate({'pid':1234,'stamp':'old'})
            kill.assert_not_called()

    def test_observing_or_sharing_workspace_does_not_stop_private_input(self):
        s=object.__new__(bg.Session)
        s.address='owned';s.lease={'workspace':4};s.mode='agent'
        class Process:
            def poll(self):return None
        s.p=Process();s.owns_pid=lambda _:True;s.check_properties=lambda:None
        own={'address':'owned','pid':1,'workspace':{'id':4}}
        user={'address':'user','pid':2,'workspace':{'id':4}}
        with patch.object(bg,'hypr',side_effect=lambda name:[own,user] if name=='clients' else []):
            s.validate()
        with patch.object(bg,'hypr',side_effect=lambda name:[own] if name=='clients' else [{'activeWorkspace':{'id':4}}]):
            s.validate()

if __name__=='__main__':unittest.main()
