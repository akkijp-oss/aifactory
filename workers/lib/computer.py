"""Lease-scoped desktop sessions over the existing pull queue."""
import base64
import fcntl
import hashlib
import json
import os
import pathlib
import struct
import sys
import uuid
from client import Client
from pull import Error, NAME
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[2]/'lib'))
import aifactory_paths as paths


def validate(action):
    if not isinstance(action,dict):raise Error('action must be an object')
    allowed={'action','x','y','button','count','text','keys','amount'}
    if set(action)-allowed:raise Error('unknown computer argument')
    kind=action.get('action')
    if kind not in ('screenshot','move','click','type','key','scroll'):raise Error('unsupported computer action')
    if kind in ('move','click'):
        if any(type(action.get(k)) is not int or not 0<=action[k]<=16384 for k in ('x','y')):raise Error('x/y must be screenshot pixel coordinates')
        if action.get('button','left') not in ('left','right'):raise Error('invalid button')
        if type(action.get('count',1)) is not int or action.get('count',1) not in (1,2):raise Error('invalid click count')
    if kind=='type' and (not isinstance(action.get('text'),str) or len(action['text'].encode())>8192):raise Error('text must be at most 8192 bytes')
    if kind=='key' and (not isinstance(action.get('keys'),list) or not 1<=len(action['keys'])<=4 or any(not isinstance(k,str) or not 1<=len(k)<=12 for k in action['keys'])):raise Error('provide 1 to 4 keys')
    if kind=='scroll' and (type(action.get('amount')) is not int or action['amount']==0 or not -20<=action['amount']<=20):raise Error('scroll amount must be -20..20 excluding zero')
    return action


class Desktop:
    def __init__(self, root=None, db=None):
        self.root=pathlib.Path(root or paths.WORKSPACE/'computer')
        self.db=db or os.environ.get('AIFACTORY_WORKER_DB') or paths.WORKSPACE/'workers'/'queue.sqlite3'
    def directory(self,session):
        if not isinstance(session,str) or not NAME.fullmatch(session) or not session.startswith('desktop-'):raise Error('invalid desktop session')
        p=self.root/session
        if p.is_symlink():raise Error('invalid desktop directory')
        return p
    def save(self,p,state):
        tmp=p/'state.tmp';tmp.write_text(json.dumps(state,ensure_ascii=False,indent=2));tmp.chmod(0o600);os.replace(tmp,p/'state.json')
    def open(self,worker):
        if not NAME.fullmatch(worker):raise Error('invalid worker')
        session='desktop-'+uuid.uuid4().hex
        p=self.directory(session);p.mkdir(parents=True,mode=0o700)
        c=Client(self.db,worker,session,p)
        w=next((w for w in c.store.workers() if w['id']==worker),{})
        system=w.get('info',{}).get('os')
        if system not in ('windows','darwin','linux'):raise Error('requires a Windows, macOS or Linux worker')
        state={'session':session,'worker':worker,'os':system,'status':'preparing'};self.save(p,state)
        try:
            c.store.acquire(worker,session)
            state['lease_acquired']=True;self.save(p,state)
            _,r=c.execute('guest-prepare')
            if r.returncode:raise Error('desktop prepare failed; session retained')
            state['status']='ready';self.save(p,state)
            return state
        except Exception:
            state['status']='error';self.save(p,state);raise Error('desktop session failed; inspect '+session)
    def load(self,session):
        p=self.directory(session)
        if not (p/'state.json').is_file():raise Error('desktop session not found')
        state=json.loads((p/'state.json').read_text())
        if state.get('session')!=session or state.get('status')=='closed':raise Error('desktop session unavailable')
        return p,state,Client(self.db,state['worker'],session,p)
    def action(self,session,action):
        validate(action);p,state,c=self.load(session)
        with (p/'action.lock').open('a') as lock:
            try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:raise Error('desktop session busy',409)
            if state['status']!='ready':raise Error('desktop session is not ready')
            if state['os']=='windows':
                command=r"[Console]::In.ReadToEnd() | & 'C:\ProgramData\AIFactoryWorker\bin\aifactory-computer.exe'; exit $LASTEXITCODE"
            elif state['os']=='linux':command='exec /usr/local/lib/aifactory-computer/aifactory-computer'
            else:command='exec "$HOME/.local/lib/aifactory-computer/aifactory-computer"'
            op,r=c.execute('guest-exec',{'command':command,'timeout':45},stdin=json.dumps(action,ensure_ascii=True))
            try:out=json.loads(r.stdout)
            except ValueError:raise Error('invalid desktop response; see operation '+op)
            if r.returncode or not out.get('ok'):raise Error(out.get('error','desktop action failed'))
            if out.get('image'):
                try:image=base64.b64decode(out['image'],validate=True)
                except ValueError:raise Error('invalid screenshot encoding')
                if len(image)>8*1024*1024 or image[:8]!=b'\x89PNG\r\n\x1a\n' or len(image)<24:raise Error('invalid screenshot')
                width,height=struct.unpack('>II',image[16:24])
                if not 0<width<=4096 or not 0<height<=4096 or (width,height)!=(out.get('width'),out.get('height')):raise Error('screenshot dimensions disagree')
                file=p/(op+'.png');file.write_bytes(image);file.chmod(0o600)
                out.update(path=str(file),sha256=hashlib.sha256(image).hexdigest())
            with (p/'actions.jsonl').open('a') as log:
                log.write(json.dumps({'operation':op,'action':action['action'],'sha256':out.get('sha256'),'ok':True})+'\n')
            return out
    def close(self,session):
        p,state,c=self.load(session)
        with (p/'action.lock').open('a') as lock:
            try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:raise Error('desktop session busy',409)
            op,r=c.execute('guest-release')
            if r.returncode:raise Error('desktop release failed; session retained')
            c.store.release_lease(state['worker'],session,op)
            state['status']='closed';self.save(p,state);return state
