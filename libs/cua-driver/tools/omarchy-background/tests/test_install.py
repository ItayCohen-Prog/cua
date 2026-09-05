import contextlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import install

class Installation(unittest.TestCase):
    def test_install_uninstall_preserves_unrelated_config_and_restores_previous_cua(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);dest=root/'runtime';codex=root/'codex';codex.mkdir()
            config=root/'hyprland.lua';original='-- User-owned rules\no.window("user", {})\n';config.write_text(original)
            (codex/'config.toml').write_text('[mcp_servers.unrelated]\ncommand="user-tool"\n')
            driver=root/'driver';driver.write_text('fixture')
            prior={'command':'cua-driver','args':['mcp','--direct'],'env':{'CUA_DRIVER_RS_ENABLE_WAYLAND':'1'}}
            servers={'cua-driver':prior,'unrelated':{'command':'user-tool'}}
            def command(args):
                if args[:3]==['codex','mcp','remove']:servers.pop(args[3],None)
                return ''
            with contextlib.ExitStack() as stack:
                for name,value in [('DEST',dest),('CODEX',codex),('CONFIG',config),('SKILL',codex/'skills/background-desktop')]:
                    stack.enter_context(patch.object(install,name,value))
                stack.enter_context(patch.object(install,'servers',side_effect=lambda:servers))
                stack.enter_context(patch.object(install,'register',side_effect=lambda n,r:servers.update({n:r})))
                stack.enter_context(patch.object(install,'command',side_effect=command))
                stack.enter_context(patch.object(install,'reload'))
                stack.enter_context(patch.object(install.shutil,'which',return_value='/usr/bin/fixture'))
                stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
                with patch.object(sys,'argv',['install.py','--driver',str(driver)]):install.main()
                self.assertIn('background-desktop',servers);self.assertNotIn('cua-driver',servers)
                self.assertTrue(install.SKILL.is_symlink())
                with patch.object(sys,'argv',['install.py','--uninstall']):install.main()
                self.assertEqual(config.read_text(),original)
                self.assertEqual(servers['cua-driver'],prior)
                self.assertEqual(servers['unrelated'],{'command':'user-tool'})
                self.assertFalse(dest.exists());self.assertFalse(install.SKILL.is_symlink())

if __name__=='__main__':unittest.main()
