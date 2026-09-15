"""Workflow on a standalone systemd Linux worker; no hypervisor lifecycle."""
import fcntl
import os
import pathlib
import uuid
from macos import acquire_lease, backend as pull_backend, Client


def backend(Run):
    class LinuxRun(pull_backend(Run)):
        backend_label = 'Linux workspace'
        # code step の対応表は macos と同じものをそのまま継承する（command() が POSIX で gh も同じように入っている）。
        # automerge も kit/steps/pr-automerge.sh を guest の中で走らせる macos の実装で動く（チケット 386）。
        # ここで CODE_STEPS を上書きすると macos に足した step が linux で落ちるので、意図して定義しない
        # PATH の決め方（guest_path_prelude）も同じ理由で継承する。列挙を backend ごとに書くと、
        # 片方だけ直した道具が「もう片方の backend では存在しない」ことになる（#549 の go）。
        # 差し替えるのは保険の固定列挙だけ（ADR-0076）。この worker は
        # /bin/bash --noprofile --norc -c で起動するので、ゲスト側の PATH は前置きの /etc/profile で入る
        PATH_FALLBACK = '/usr/local/bin:/usr/bin:/bin:$HOME/.local/bin:$HOME/.cargo/bin'

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.project = dict(self.project)
            self.root = str(pathlib.PurePosixPath(self.project['app_dir']).parent)
            self.state['backend'] = 'linux-pull'
            self.paths(self.state.get('lease', 'dry-run'))

        def paths(self, lease):
            self.project['app_dir'] = f'{self.root}/{lease}/app'
            self.work = f'{self.root}/{lease}/work/{self.task}'
            self.env_file = self.work + '/runtime.env'

        def take(self):
            if self.dry:
                return
            self.record_needed_keys()
            import aifactory_paths as paths
            # --wait で take を呼び直せるよう、lock と lease id と Client は 1 回だけ作る（チケット 373）
            if self.run_lock is None:
                self.run_lock = open(self.run_dir / 'linux.lock', 'a')
                fcntl.flock(self.run_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if self.client is None:
                lease = self.state.get('lease') if self.resume else f'run-{self.task}-{uuid.uuid4().hex[:16]}'
                if not lease:
                    raise RuntimeError('Linux resume has no recorded lease')
                self.lease_id = lease
                self.paths(lease)
                self.client = Client(os.environ.get('AIFACTORY_WORKER_DB') or paths.WORKSPACE / 'workers/queue.sqlite3',
                                     self.project['worker'], lease, self.run_dir)
            lease = self.lease_id
            worker = next((w for w in self.client.store.workers() if w['id'] == self.project['worker']), {})
            info = worker.get('info', {})
            if not worker.get('online') or info.get('os') != 'linux' or not info.get('lifecycle'):
                raise RuntimeError('Linux worker is offline or not configured')
            if pathlib.PurePosixPath(info.get('work_root', '')) != pathlib.PurePosixPath(self.root):
                raise RuntimeError('project app_dir must be <worker work_root>/app')
            if self.resume:
                if (worker.get('lease') or {}).get('id') != lease:
                    raise RuntimeError('resume requires this run’s retained lease')
                self.resume_guest(lease)
                return
            acquire_lease(self, worker, lease)
            self.state['lease'] = lease
            self.save()
            self.set_current('prepare', 'code', 'prepare.log')
            _, result = self.client.execute('guest-prepare')
            if result.returncode:
                raise RuntimeError('Linux workspace preparation failed; lease retained')
            self.check_clock()   # 工程を始める前にゲストの時計を測る（491）
            self.setup_project()

    return LinuxRun
