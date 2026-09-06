#!/usr/bin/env python3
"""Install or remove the user-local Omarchy background desktop integration."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import tomllib
import urllib.request

HERE=Path(__file__).resolve().parent
DEST=Path.home()/'.local/share/cua-background'
CODEX=Path(os.environ.get('CODEX_HOME',Path.home()/'.codex'))
CONFIG=Path.home()/'.config/hypr/hyprland.lua'
SKILL=CODEX/'skills/background-desktop'
BEGIN='-- BEGIN CUA BACKGROUND'
END='-- END CUA BACKGROUND'
PACKAGES={'openbox':'3.6.1-14','startup-notification':'0.12-9','imlib2':'1.12.6-2'}


def command(args):
    return subprocess.run(args,check=True,text=True,capture_output=True).stdout.strip()


def servers():
    return tomllib.loads((CODEX/'config.toml').read_text()).get('mcp_servers',{})


def register(name,record):
    args=['codex','mcp','add',name]
    for key,value in record.get('env',{}).items():args+=['--env',f'{key}={value}']
    command(args+['--',record['command'],*record.get('args',[])])


def remove_block(text):
    if BEGIN not in text:return text
    before,tail=text.split(BEGIN,1)
    _,after=tail.split(END,1)
    return before.rstrip()+after


def reload():
    command(['hyprctl','reload'])
    errors=command(['hyprctl','configerrors'])
    if errors:raise RuntimeError(errors)


def private_openbox(root):
    # Check the current Omarchy installer catalog before acquiring dependencies.
    catalog=command(['omarchy','install','--help'])
    if any('openbox' in line.lower() for line in catalog.splitlines()):
        raise RuntimeError('Omarchy now lists Openbox; use its dedicated installer first')
    if os.uname().machine!='x86_64':raise RuntimeError('Private Openbox bundle is Arch x86_64 only')
    with tempfile.TemporaryDirectory(prefix='cua-dependencies-') as tmp:
        for name,version in PACKAGES.items():
            filename=f'{name}-{version}-x86_64.pkg.tar.zst'
            url=f'https://archive.archlinux.org/packages/{name[0]}/{name}/{filename}'
            package=Path(tmp)/filename
            for suffix in ('','.sig'):
                urllib.request.urlretrieve(url+suffix,str(package)+suffix)
            command(['gpgv','--keyring','/etc/pacman.d/gnupg/pubring.gpg',str(package)+'.sig',str(package)])
            command(['tar','--zstd','-xf',str(package),'-C',str(root)])
        # Package metadata is not needed at runtime; licenses remain under usr/share/licenses.
        for name in ('.PKGINFO','.BUILDINFO','.MTREE','.INSTALL'):
            (root/name).unlink(missing_ok=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--driver',type=Path)
    parser.add_argument('--private-openbox',action='store_true',help='Use signature-verified Arch packages privately, without changing system packages')
    parser.add_argument('--uninstall',action='store_true')
    args=parser.parse_args()
    state=Path(os.environ.get('XDG_RUNTIME_DIR',f'/run/user/{os.getuid()}'))/'cua-background'
    if list(state.glob('session-*')):raise RuntimeError('Stop active background desktops before changing the installation')
    manifest=DEST/'installation.json'
    if args.uninstall:
        record=json.loads(manifest.read_text())
        current=servers().get('background-desktop')
        if current and current!=record['mcp']:raise RuntimeError('MCP settings changed since install; preserve them and uninstall manually')
        original=CONFIG.read_text()
        try:
            CONFIG.write_text(remove_block(original))
            reload()
            if current:command(['codex','mcp','remove','background-desktop'])
            if record.get('previous_cua') and 'cua-driver' not in servers():register('cua-driver',record['previous_cua'])
        except BaseException:
            CONFIG.write_text(original);reload();raise
        if SKILL.is_symlink() and SKILL.resolve()==DEST/'skill/background-desktop':SKILL.unlink()
        shutil.rmtree(DEST)
        print('Removed the integration. Task output directories were preserved.')
        return
    if not args.driver or not args.driver.is_file():raise RuntimeError('--driver must point to the driver built from this fork')
    for binary in ('bwrap','Xwayland','xauth','xprop','dbus-daemon','hyprctl','codex'):
        if not shutil.which(binary):raise RuntimeError(f'Missing prerequisite: {binary}')
    reuse_dependencies = (DEST/'dependencies/usr').is_dir() and (DEST/'installation.json').exists()
    use_private = args.private_openbox or reuse_dependencies
    if not use_private and not shutil.which('openbox'):
        raise RuntimeError('Install Openbox using Omarchy package management, or pass --private-openbox')
    if SKILL.exists() and not (SKILL.is_symlink() and SKILL.resolve()==DEST/'skill/background-desktop'):
        raise RuntimeError('A different background-desktop skill exists; refusing to overwrite it')
    if DEST.exists() and not manifest.exists():
        raise RuntimeError('Destination already exists without an installation manifest')
    prior=json.loads(manifest.read_text()) if manifest.exists() else {}
    original=CONFIG.read_text()
    original_mcp=(CODEX/'config.toml').read_bytes()
    saved=None
    DEST.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='cua-install-',dir=DEST.parent) as tmp:
        stage=Path(tmp)/'new';stage.mkdir()
        for name in ('background.py','supervisor.py','input_gate.py','private_pointer.py','hyprland.lua','openbox.xml'):
            shutil.copy2(HERE/name,stage/name)
        shutil.copytree(HERE/'skill',stage/'skill')
        shutil.copy2(args.driver,stage/'cua-driver')
        if args.private_openbox:
            (stage/'dependencies').mkdir()
            private_openbox(stage/'dependencies')
        elif reuse_dependencies:
            shutil.copytree(DEST/'dependencies',stage/'dependencies')
        mcp={'command':sys.executable,'args':[str(DEST/'background.py'),'mcp','--driver',str(DEST/'cua-driver')]}
        if use_private:mcp['args']+=['--dependency-root',str(DEST/'dependencies')]
        record={'mcp':mcp,'previous_cua':prior.get('previous_cua',servers().get('cua-driver')),
                'driver_sha256':hashlib.sha256((stage/'cua-driver').read_bytes()).hexdigest()}
        (stage/'installation.json').write_text(json.dumps(record,indent=2));(stage/'installation.json').chmod(0o600)
        try:
            if DEST.exists():
                saved=Path(tmp)/'old';DEST.rename(saved)
            stage.rename(DEST)
            CONFIG.write_text(remove_block(original).rstrip()+'\n\n'+BEGIN+'\ndofile(os.getenv("HOME") .. "/.local/share/cua-background/hyprland.lua")\n'+END+'\n')
            reload()
            register('background-desktop',mcp)
            if 'cua-driver' in servers():command(['codex','mcp','remove','cua-driver'])
            SKILL.parent.mkdir(parents=True,exist_ok=True)
            if not SKILL.is_symlink():SKILL.symlink_to(DEST/'skill/background-desktop',target_is_directory=True)
        except BaseException:
            CONFIG.write_text(original)
            (CODEX/'config.toml').write_bytes(original_mcp)
            if DEST.exists():shutil.rmtree(DEST)
            if saved:saved.rename(DEST)
            reload();raise
    print(f'Installed at {DEST}. Start a new Codex task to load the MCP tools and skill.')


if __name__=='__main__':main()
