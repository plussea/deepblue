from .forking import fork_session


class WorkflowFeatures:
    def fork(self, data):
        with self.index.lock:
            source = self.index.read(self.session_path(data.get('session_id', '')), self.cwd)
            identity = fork_session(source, self.home, self.cwd, data.get('message_count'))
            return {'session_id': identity}

    def queue_message(self, data):
        with self.lock:
            job_id = data.get('job_id')
            if not isinstance(job_id, str):
                raise ValueError('需要任务 ID。')
            job = self.jobs.get(job_id)
            if not job or job['finished']:
                raise ValueError('需要当前正在执行的任务。')
            settings = dict(self.settings)
            settings['verify'] = data.get('verify', '')
            if not isinstance(settings['verify'], str) or len(settings['verify']) > 4000:
                raise ValueError('无效验收命令。')
            if settings['verify'] and settings['permission_mode'] != 'trusted':
                raise ValueError('受限模式禁止命令验收。')
            identity = self.inbox.add(data.get('kind'), job_id, job['session_id'], data.get('prompt'), settings, data.get('request_id'))
            self.queue_keys.setdefault(identity, self.api_key())
            return {'id': identity}

    def dispatch_followup(self, job_id=None, identity=None):
        with self.lock:
            if self.job and not self.job['finished']:
                if identity:
                    raise ValueError('请等待当前任务结束。')
                return
            item = self.inbox.claim('follow-up', job_id, identity)
            if not item:
                if job_id:
                    self.inbox.release_job(job_id)
                return None
            try:
                result = self.start({'request_id': item['id'], 'session_id': item['session_id'],
                                     'action': 'run', 'prompt': item['prompt'], 'verify': item['settings'].get('verify', '')},
                                    frozen_settings=item['settings'], frozen_key=self.queue_keys.pop(item['id'], self.key_for_settings(item['settings'])))
                self.inbox.finish(item['id'], 'dispatched')
                self.inbox.release_job(item['job_id'], result['job_id'])
                return result
            except BaseException:
                self.inbox.finish(item['id'], 'unknown')
                self.inbox.release_job(item['job_id'])
                raise
