"""Local settings and durable, ordered subtitle sessions. No secrets in SQLite."""
import csv
import hashlib
import io
import json
import math
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from theme import DEFAULT_COLORS,normalize_colors,normalize_theme
from typography import default_typography,normalize_typography
from audio_gain import DEFAULT_AGC,normalize_agc
from domain_bias import default_domain_bias,normalize_domain_bias
from desktop_caption_config import default_desktop_captions,normalize_desktop_captions
from audio_sources import normalize_audio_source
from caption_visibility import caption_visibility
from progressive_captions import preview_limit,preview_interval
from segmentation import confirmation_wait

from app_paths import DATA
SERVICE = 'ParakeetNPUSubtitles'


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds')


def text_fingerprint(text):
    return hashlib.sha256(str(text or '').encode('utf-8')).hexdigest()


def batch_fingerprint(texts):
    """Stable hash over one batch's ordered source cues (never over translations)."""
    digest = hashlib.sha256()
    for text in texts:
        digest.update(text_fingerprint(text).encode('ascii'))
        digest.update(b'\0')
    return digest.hexdigest()


def final_delivery_enabled(value=True):
    return value if type(value) is bool else True


def final_delivery_wait(value=4.):
    return float(value) if type(value) in (int,float) and math.isfinite(value) and .5<=value<=60 else 4.


def final_delivery_words(value=35):
    return value if type(value) is int and 1<=value<=500 else 35


def default_settings():
    return {'version': 3, 'main_layout_version':2, 'selected_profile': 'deepseek', 'translate': True,
        'show_source':True,'show_translation':True,'show_pending_source':False,'progressive_translation':True,'progressive_translation_limit':2,'progressive_translation_interval':1.,
        'close_to_tray':True,'minimize_to_tray':True,
        'asr_model':'parakeet','source_language':'auto','target_language':'简体中文','agc':dict(DEFAULT_AGC),'feature_backend':'numpy','domain_bias':default_domain_bias(),'desktop_captions':default_desktop_captions(),
        'english_size': 24, 'chinese_size': 22, 'opacity': 100, 'topmost': False,
        'quiet': True, 'interval': 1.5, 'segmentation': 'balanced', 'confirmation_wait':3.,
        'final_delivery_enabled':True,'final_delivery_wait':4.,'final_delivery_words':35,'colors':dict(DEFAULT_COLORS),
        'theme':normalize_theme(), 'saved_themes':[], 'typography':default_typography(), 'microphone_name': '', 'audio_source':normalize_audio_source(), 'profiles': [
            {'id': 'deepseek', 'name': 'DeepSeek', 'protocol': 'openai',
             'base_url': 'https://api.deepseek.com', 'model': 'deepseek-flash',
             'auth': 'auto', 'api_version': '2023-06-01', 'workspace': '', 'reasoning': 'auto',
             'extra': {'thinking': {'type': 'disabled'}, 'max_tokens': 1200}},
            {'id': 'openai', 'name': 'OpenAI', 'protocol': 'openai',
             'base_url': 'https://api.openai.com/v1', 'model': '', 'auth': 'auto',
             'api_version': '2023-06-01', 'workspace': '', 'reasoning': 'auto', 'extra': {}},
            {'id': 'anthropic', 'name': 'Anthropic', 'protocol': 'anthropic',
             'base_url': 'https://api.anthropic.com', 'model': '', 'auth': 'auto',
             'api_version': '2023-06-01', 'workspace': '', 'reasoning': 'auto', 'extra': {}},
        ]}


def load_settings(directory=DATA):
    defaults = default_settings()
    saved={}
    path = Path(directory) / 'settings.json'
    if path.exists():
        saved = json.loads(path.read_text('utf-8'))
        defaults.update({k: v for k, v in saved.items() if k in defaults})
    defaults['colors']=normalize_colors(defaults.get('colors'))
    defaults['theme']=normalize_theme(defaults.get('theme'))
    defaults['typography']=normalize_typography(defaults.get('typography'))
    if saved and saved.get('main_layout_version',1)<2:
        defaults['typography']['preview_height']=min(40,defaults['typography']['preview_height'])
        defaults['main_layout_version']=2
    defaults['agc']=normalize_agc(defaults.get('agc'))
    defaults['domain_bias']=normalize_domain_bias(defaults.get('domain_bias'))
    defaults['desktop_captions']=normalize_desktop_captions(defaults.get('desktop_captions'))
    defaults['audio_source']=normalize_audio_source(saved.get('audio_source'),legacy=defaults.get('microphone_name',''))
    defaults['show_source'],defaults['show_translation']=caption_visibility(defaults)
    if type(defaults['show_pending_source']) is not bool:defaults['show_pending_source']=False
    for key in ('close_to_tray','minimize_to_tray','progressive_translation'):
        if type(defaults[key]) is not bool:defaults[key]=True
    defaults['progressive_translation_limit']=preview_limit(defaults.get('progressive_translation_limit'))
    defaults['progressive_translation_interval']=preview_interval(defaults.get('progressive_translation_interval'))
    defaults['confirmation_wait']=confirmation_wait(defaults.get('confirmation_wait'))
    defaults['final_delivery_enabled']=final_delivery_enabled(defaults.get('final_delivery_enabled'))
    defaults['final_delivery_wait']=final_delivery_wait(defaults.get('final_delivery_wait'))
    defaults['final_delivery_words']=final_delivery_words(defaults.get('final_delivery_words'))
    reasoning_modes=('auto','off','low','medium','high','max')
    for profile in defaults.get('profiles',[]):
        if not isinstance(profile,dict):continue
        if profile.get('reasoning') not in reasoning_modes:profile['reasoning']='auto'
    return defaults


def save_settings(settings, directory=DATA):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    # Keys are not accepted by the settings schema; they live in Credential Manager.
    clean = {k: settings.get(k,value) for k,value in default_settings().items()}
    clean['progressive_translation_limit']=preview_limit(settings.get('progressive_translation_limit'))
    clean['progressive_translation_interval']=preview_interval(settings.get('progressive_translation_interval'))
    clean['confirmation_wait']=confirmation_wait(settings.get('confirmation_wait'))
    clean['final_delivery_enabled']=final_delivery_enabled(settings.get('final_delivery_enabled'))
    clean['final_delivery_wait']=final_delivery_wait(settings.get('final_delivery_wait'))
    clean['final_delivery_words']=final_delivery_words(settings.get('final_delivery_words'))
    clean['colors']=normalize_colors(settings.get('colors'))
    clean['theme']=normalize_theme(settings.get('theme'))
    clean['typography']=normalize_typography(settings.get('typography'))
    clean['agc']=normalize_agc(settings.get('agc'))
    clean['domain_bias']=normalize_domain_bias(settings.get('domain_bias'))
    clean['desktop_captions']=normalize_desktop_captions(settings.get('desktop_captions'))
    clean['audio_source']=normalize_audio_source(settings.get('audio_source'),legacy=settings.get('microphone_name',''))
    allowed = {'id','name','protocol','base_url','model','auth','api_version','workspace','reasoning','extra'}
    clean['profiles'] = [{k:v for k,v in p.items() if k in allowed} for p in clean['profiles']]
    for profile in clean['profiles']:
        if profile.get('reasoning') not in ('auto','off','low','medium','high','max'):
            profile['reasoning']='auto'
    temp = directory / 'settings.json.tmp'
    temp.write_text(json.dumps(clean, ensure_ascii=False, indent=2), 'utf-8')
    temp.replace(directory / 'settings.json')


class History:
    def __init__(self, path=None, recover=False):
        path = Path(path) if path else DATA / 'history.sqlite3'
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = str(path)
        self.db = sqlite3.connect(path, timeout=5)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA foreign_keys=ON')
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY, title TEXT NOT NULL, created_at TEXT NOT NULL,
                ended_at TEXT, provider TEXT NOT NULL, model TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS segments (
                id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id),
                seq INTEGER NOT NULL, created_at TEXT NOT NULL,
                start_ms INTEGER NOT NULL, end_ms INTEGER NOT NULL,
                en TEXT NOT NULL, zh TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL, error TEXT NOT NULL DEFAULT '', uncertain INTEGER NOT NULL DEFAULT 0,
                provider TEXT NOT NULL DEFAULT '', model TEXT NOT NULL DEFAULT '',
                UNIQUE(session_id, seq));
            CREATE INDEX IF NOT EXISTS segment_session ON segments(session_id, seq);
            CREATE INDEX IF NOT EXISTS segment_pending ON segments(session_id,status,seq);
            CREATE INDEX IF NOT EXISTS session_date ON sessions(created_at DESC);
            CREATE TABLE IF NOT EXISTS file_sessions (
                session_id TEXT PRIMARY KEY REFERENCES sessions(id), created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS batch_runs (
                session_id TEXT PRIMARY KEY REFERENCES sessions(id),
                run_id TEXT NOT NULL, source_revision TEXT NOT NULL,
                status TEXT NOT NULL, target_language TEXT NOT NULL,
                provider TEXT NOT NULL, model TEXT NOT NULL,
                reasoning TEXT NOT NULL DEFAULT 'auto', batch_count INTEGER NOT NULL DEFAULT 0,
                error TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS translation_batches (
                session_id TEXT NOT NULL REFERENCES sessions(id), batch_id TEXT NOT NULL,
                run_id TEXT NOT NULL, ordinal INTEGER NOT NULL, items TEXT NOT NULL,
                source_hash TEXT NOT NULL, status TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0, claim TEXT NOT NULL DEFAULT '',
                error TEXT NOT NULL DEFAULT '', flags TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                PRIMARY KEY(session_id, batch_id));
            CREATE INDEX IF NOT EXISTS batch_session ON translation_batches(session_id,run_id,ordinal);
        ''')
        # Older builds saved file sessions without a marker; route their explicit
        # retries through the batch path too.
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO file_sessions (session_id,created_at) "
                "SELECT id,created_at FROM sessions WHERE title LIKE '文件 · %'")
        columns={r[1] for r in self.db.execute('PRAGMA table_info(segments)')}
        for name in ('provider','model'):
            if name not in columns:self.db.execute(f"ALTER TABLE segments ADD COLUMN {name} TEXT NOT NULL DEFAULT ''")
        for name,default in [('asr_model','parakeet'),('source_language','English'),('target_language','简体中文'),('timing','word')]:
            if name not in columns:self.db.execute(f"ALTER TABLE segments ADD COLUMN {name} TEXT NOT NULL DEFAULT '{default}'")
        # Interrupted work remains visible and can be retried explicitly.
        if recover:
            with self.db:
                self.db.execute("UPDATE segments SET status='interrupted', error='上次退出时翻译未完成，可重试' WHERE status IN ('queued','pending')")
                self.db.execute('UPDATE sessions SET ended_at=? WHERE ended_at IS NULL', (utc_now(),))

    def create_session(self, title, profile, session_id=None, kind='realtime'):
        sid = session_id or uuid.uuid4().hex
        with self.db:
            self.db.execute('INSERT INTO sessions (id,title,created_at,ended_at,provider,model) VALUES (?,?,?,?,?,?)',
                (sid, title, utc_now(), None, profile.get('name',''), profile.get('model','')))
            if kind == 'file':
                self.db.execute('INSERT INTO file_sessions (session_id,created_at) VALUES (?,?)',
                    (sid, utc_now()))
        return sid

    def finish(self, sid):
        if sid:
            with self.db:
                self.db.execute('UPDATE sessions SET ended_at=? WHERE id=?', (utc_now(), sid))

    def rename(self, sid, title):
        with self.db:
            self.db.execute('UPDATE sessions SET title=? WHERE id=?', (title, sid))

    def delete_session(self,sid):
        with self.db:
            self.db.execute('DELETE FROM translation_batches WHERE session_id=?',(sid,))
            self.db.execute('DELETE FROM batch_runs WHERE session_id=?',(sid,))
            self.db.execute('DELETE FROM file_sessions WHERE session_id=?',(sid,))
            self.db.execute('DELETE FROM segments WHERE session_id=?',(sid,))
            self.db.execute('DELETE FROM sessions WHERE id=?',(sid,))

    def add(self, sid, en, start_ms, end_ms, status='queued', uncertain=False,metadata=None):
        ident = uuid.uuid4().hex
        with self.db:
            seq = self.db.execute('SELECT COALESCE(MAX(seq),0)+1 FROM segments WHERE session_id=?', (sid,)).fetchone()[0]
            self.db.execute('INSERT INTO segments (id,session_id,seq,created_at,start_ms,end_ms,en,zh,status,error,uncertain) VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                (ident,sid,seq,utc_now(),max(0,int(start_ms)),max(int(start_ms)+200,int(end_ms)),en,'',status,'',int(uncertain)))
            if metadata:
                self.db.execute('UPDATE segments SET asr_model=?,source_language=?,target_language=?,timing=? WHERE id=?',
                    (metadata.get('asr_model','parakeet'),metadata.get('source_language','English'),metadata.get('target_language','简体中文'),
                     'approximate' if metadata.get('approximate_timestamps') else 'word',ident))
        return ident

    def update(self, ident, status, zh='', error=''):
        with self.db:
            self.db.execute('UPDATE segments SET status=?,zh=?,error=? WHERE id=?', (status,zh,error,ident))

    def translation_source(self,ident,profile):
        with self.db:
            self.db.execute('UPDATE segments SET provider=?,model=?,target_language=? WHERE id=?',
                (profile['name'],profile['model'],profile.get('target_language','简体中文'),ident))

    def segment(self, ident):
        row = self.db.execute('SELECT * FROM segments WHERE id=?',(ident,)).fetchone()
        return dict(row) if row else None

    def rows(self, sid, limit=None):
        sql = 'SELECT * FROM segments WHERE session_id=? ORDER BY seq'
        args = [sid]
        if limit:
            sql = 'SELECT * FROM (SELECT * FROM segments WHERE session_id=? ORDER BY seq DESC LIMIT ?) ORDER BY seq'
            args.append(limit)
        return [dict(row) for row in self.db.execute(sql,args)]

    def rows_around(self, sid, seq, before=60, after=60):
        """A bounded cue window for opening an old search result."""
        seq = int(seq)
        earlier = [dict(row) for row in self.db.execute(
            'SELECT * FROM segments WHERE session_id=? AND seq<=? ORDER BY seq DESC LIMIT ?',
            (sid, seq, max(0, int(before)) + 1))]
        later = [dict(row) for row in self.db.execute(
            'SELECT * FROM segments WHERE session_id=? AND seq>? ORDER BY seq LIMIT ?',
            (sid, seq, max(0, int(after))))]
        return list(reversed(earlier)) + later

    def rows_before(self, sid, seq, limit=100):
        """Older cues for the paused-view “show earlier” action."""
        return [dict(row) for row in self.db.execute(
            'SELECT * FROM segments WHERE session_id=? AND seq<? ORDER BY seq DESC LIMIT ?',
            (sid, int(seq), max(1, int(limit))))][::-1]

    def rows_by_ids(self, sid, idents):
        """Refresh only rows currently visible in a paused reading snapshot."""
        idents=[ident for ident in dict.fromkeys(idents) if ident]
        if not idents:return []
        rows=[]
        for start in range(0,len(idents),800):
            chunk=idents[start:start+800]
            marks=','.join('?'*len(chunk))
            # id is globally unique; keep session_id out of SQL so SQLite uses
            # the primary-key lookup instead of scanning segment_session.
            rows.extend(dict(row) for row in self.db.execute(
                f'SELECT * FROM segments WHERE id IN ({marks})',chunk))
        rows=[row for row in rows if row['session_id']==sid]
        rows.sort(key=lambda row:row['seq'])
        return rows

    def context(self, ident):
        row = self.segment(ident)
        if not row:
            return []
        data = self.db.execute('SELECT en FROM segments WHERE session_id=? AND seq<? ORDER BY seq DESC LIMIT 2',
                               (row['session_id'],row['seq'])).fetchall()
        return [r['en'] for r in reversed(data)]

    def session(self, sid):
        row = self.db.execute('SELECT * FROM sessions WHERE id=?',(sid,)).fetchone()
        if not row:return None
        result = dict(row)
        result['kind'] = 'file' if self.db.execute(
            'SELECT 1 FROM file_sessions WHERE session_id=?',(sid,)).fetchone() else 'realtime'
        return result

    def all_sessions(self):
        """Every saved session with its cue count, newest first."""
        return [dict(r) for r in self.db.execute('''
            SELECT s.*, (SELECT COUNT(*) FROM segments WHERE session_id=s.id) AS count
            FROM sessions s ORDER BY s.created_at DESC''')]

    def sessions(self, query='', limit=100):
        pattern = '%'+query.replace('\\','\\\\').replace('%','\\%').replace('_','\\_')+'%'
        return [dict(r) for r in self.db.execute('''
            SELECT s.*, (SELECT COUNT(*) FROM segments WHERE session_id=s.id) AS count
            FROM sessions s WHERE s.title LIKE ? ESCAPE '\\' OR EXISTS
                (SELECT 1 FROM segments g WHERE g.session_id=s.id AND (g.en LIKE ? ESCAPE '\\' OR g.zh LIKE ? ESCAPE '\\'))
            ORDER BY s.created_at DESC LIMIT ?''', (pattern,pattern,pattern,limit))]

    def retryable(self, sid):
        return [dict(r) for r in self.db.execute("SELECT * FROM segments WHERE session_id=? AND status IN ('failed','disabled','interrupted') ORDER BY seq",(sid,))]

    def queued(self, sid,limit=None):
        sql="SELECT * FROM segments WHERE session_id=? AND status='queued' ORDER BY seq"
        args=[sid]
        if limit is not None:sql+=' LIMIT ?';args.append(max(1,int(limit)))
        return [dict(r) for r in self.db.execute(sql,args)]

    def fail_queued(self,sid,error):
        with self.db:self.db.execute("UPDATE segments SET status='failed',error=? WHERE session_id=? AND status='queued'",(error,sid))

    def translation_revision(self, sid):
        """Hash of the raw transcript identity/text; timestamps remain local and unchanged."""
        digest = hashlib.sha256()
        for row in self.db.execute(
                'SELECT id,seq,start_ms,end_ms,en FROM segments WHERE session_id=? ORDER BY seq', (sid,)):
            for value in (row['id'], row['seq'], row['start_ms'], row['end_ms'], row['en']):
                digest.update(str(value).encode('utf-8')); digest.update(b'\0')
        return digest.hexdigest()

    def batch_run(self, sid):
        row = self.db.execute('SELECT * FROM batch_runs WHERE session_id=?', (sid,)).fetchone()
        if not row:return None
        result = dict(row)
        result['counts'] = self.batch_progress(sid, result['run_id'])
        return result

    def create_batch_run(self, sid, revision, profile, target_language, reasoning, batches):
        """Atomically replace the durable plan for one file session."""
        run_id = uuid.uuid4().hex; now = utc_now()
        with self.db:
            self.db.execute('DELETE FROM translation_batches WHERE session_id=?', (sid,))
            self.db.execute('''INSERT INTO batch_runs
                (session_id,run_id,source_revision,status,target_language,provider,model,reasoning,batch_count,error,created_at,updated_at)
                VALUES (?,?,?,'translating',?,?,?,?,?,'',?,?)
                ON CONFLICT(session_id) DO UPDATE SET run_id=excluded.run_id,source_revision=excluded.source_revision,
                status='translating',target_language=excluded.target_language,provider=excluded.provider,model=excluded.model,
                reasoning=excluded.reasoning,batch_count=excluded.batch_count,error='',created_at=excluded.created_at,updated_at=excluded.updated_at''',
                (sid, run_id, revision, target_language, profile.get('name',''), profile.get('model',''),
                 str(reasoning or 'auto'), len(batches), now, now))
            for ordinal, batch in enumerate(batches):
                self.db.execute('''INSERT INTO translation_batches
                    (session_id,batch_id,run_id,ordinal,items,source_hash,status,attempts,claim,error,flags,created_at,updated_at)
                    VALUES (?,?,?,?,?,?,'pending',0,'','','[]',?,?)''',
                    (sid, batch['batch_id'], run_id, ordinal,
                     json.dumps(batch['items'], ensure_ascii=False, separators=(',', ':')),
                     batch['source_hash'], now, now))
        return run_id

    def set_batch_run_provider(self, sid, run_id, profile, reasoning):
        with self.db:
            self.db.execute('UPDATE batch_runs SET provider=?,model=?,reasoning=?,updated_at=? WHERE session_id=? AND run_id=?',
                (profile.get('name',''), profile.get('model',''), str(reasoning or 'auto'), utc_now(), sid, run_id))

    def batches_for_run(self, sid, run_id):
        return [dict(row) for row in self.db.execute(
            'SELECT * FROM translation_batches WHERE session_id=? AND run_id=? ORDER BY ordinal', (sid, run_id))]

    def batch_progress(self, sid, run_id=None):
        sql = '''SELECT COUNT(*) AS total,
            SUM(CASE WHEN status='done' THEN 1 ELSE 0 END) AS done,
            SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) AS failed,
            SUM(CASE WHEN status='cancelled' THEN 1 ELSE 0 END) AS cancelled,
            SUM(CASE WHEN status IN ('pending','translating') THEN 1 ELSE 0 END) AS pending
            FROM translation_batches WHERE session_id=?'''
        args = [sid]
        if run_id is not None:
            sql += ' AND run_id=?'; args.append(run_id)
        row = self.db.execute(sql, args).fetchone()
        return {key:int(row[key] or 0) for key in ('total','done','failed','cancelled','pending')}

    def pending_batches(self, sid, run_id, limit=-1):
        return [dict(row) for row in self.db.execute(
            "SELECT * FROM translation_batches WHERE session_id=? AND run_id=? AND status='pending' ORDER BY ordinal LIMIT ?",
            (sid, run_id, limit))]

    def release_batch_claim(self, sid, run_id, batch_id, claim):
        with self.db:
            self.db.execute("UPDATE translation_batches SET status='pending',claim='',updated_at=? "
                "WHERE session_id=? AND batch_id=? AND run_id=? AND claim=? AND status='translating'",
                (utc_now(), sid, batch_id, run_id, claim))

    def claim_batch(self, sid, run_id, batch_id):
        """Return a claim token only for the current run and an unclaimed batch."""
        with self.db:
            run = self.db.execute('SELECT * FROM batch_runs WHERE session_id=?', (sid,)).fetchone()
            if not run or run['run_id'] != run_id or run['status'] not in ('translating','interrupted','preparing'):
                return None
            batch = self.db.execute(
                'SELECT * FROM translation_batches WHERE session_id=? AND batch_id=? AND run_id=?',
                (sid, batch_id, run_id)).fetchone()
            if not batch or batch['status'] not in ('pending','interrupted'):
                return None
            claim = uuid.uuid4().hex; now = utc_now()
            self.db.execute("UPDATE translation_batches SET status='translating',claim=?,attempts=attempts+1,error='',updated_at=? WHERE session_id=? AND batch_id=?",
                (claim, now, sid, batch_id))
            result = dict(batch); result.update(status='translating', claim=claim, run=dict(run))
            return result

    def _finish_run_if_terminal(self, sid, run_id):
        row = self.db.execute('''SELECT SUM(CASE WHEN status NOT IN ('done','failed','cancelled') THEN 1 ELSE 0 END) AS open,
            SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) AS failed,
            SUM(CASE WHEN status='done' THEN 1 ELSE 0 END) AS done
            FROM translation_batches WHERE session_id=? AND run_id=?''', (sid, run_id)).fetchone()
        if not row or int(row['open'] or 0):return False
        status = 'completed' if int(row['done'] or 0) and not int(row['failed'] or 0) else ('failed' if int(row['failed'] or 0) else 'cancelled')
        error = '' if status == 'completed' else ('部分批次翻译失败，可在历史记录中重试' if status == 'failed' else '批量翻译已取消')
        with self.db:
            self.db.execute('UPDATE batch_runs SET status=?,error=?,updated_at=? WHERE session_id=? AND run_id=?',
                (status, error, utc_now(), sid, run_id))
        return True

    def apply_batch_translations(self, sid, run_id, batch_id, claim, revision, translations, flags=None):
        """Atomic guarded write. Stale claims, revisions or source text never overwrite rows."""
        items = None
        with self.db:
            run = self.db.execute('SELECT * FROM batch_runs WHERE session_id=?', (sid,)).fetchone()
            batch = self.db.execute(
                'SELECT * FROM translation_batches WHERE session_id=? AND batch_id=? AND run_id=?',
                (sid, batch_id, run_id)).fetchone()
            if not run or run['run_id'] != run_id or run['status'] != 'translating':
                return {'ok':False,'reason':'stale-run'}
            if not batch or batch['status'] != 'translating' or batch['claim'] != claim:
                return {'ok':False,'reason':'stale-claim'}
            if run['source_revision'] != revision:
                return {'ok':False,'reason':'revision-changed'}
            payload = json.loads(batch['items'])
            items = payload.get('target',[]) if isinstance(payload,dict) else payload
            sources = []
            for item in items:
                row = self.db.execute('SELECT en,session_id FROM segments WHERE id=?', (item['id'],)).fetchone()
                if not row or row['session_id'] != sid or text_fingerprint(row['en']) != item.get('hash'):
                    return {'ok':False,'reason':'source-changed'}
                sources.append(row['en'])
            if batch['source_hash'] != batch_fingerprint(sources):
                return {'ok':False,'reason':'source-changed'}
            if set(translations) != {item['id'] for item in items}:
                return {'ok':False,'reason':'coverage'}
            now = utc_now()
            for item in items:
                text = str(translations[item['id']]).strip()
                if not text:return {'ok':False,'reason':'empty'}
                self.db.execute('''UPDATE segments SET zh=?,status='done',error='',provider=?,model=?,target_language=?
                    WHERE id=? AND session_id=?''',
                    (text, run['provider'], run['model'], run['target_language'], item['id'], sid))
            self.db.execute("UPDATE translation_batches SET status='done',claim='',error='',flags=?,updated_at=? WHERE session_id=? AND batch_id=?",
                (json.dumps(flags or [], ensure_ascii=False), now, sid, batch_id))
        finished = self._finish_run_if_terminal(sid, run_id)
        return {'ok':True,'run_finished':finished,'counts':self.batch_progress(sid, run_id)}

    def fail_batch(self, sid, run_id, batch_id, claim, error):
        with self.db:
            run = self.db.execute('SELECT * FROM batch_runs WHERE session_id=?', (sid,)).fetchone()
            batch = self.db.execute(
                'SELECT * FROM translation_batches WHERE session_id=? AND batch_id=? AND run_id=?',
                (sid, batch_id, run_id)).fetchone()
            if not run or run['run_id'] != run_id or run['status'] != 'translating' or not batch:
                return {'ok':False,'reason':'stale'}
            if batch['status'] == 'done' or batch['claim'] != claim:
                return {'ok':False,'reason':'stale-claim'}
            if run['status'] == 'cancelled':
                return {'ok':False,'reason':'cancelled'}
            now = utc_now()
            payload = json.loads(batch['items'])
            items = payload.get('target',[]) if isinstance(payload,dict) else payload
            self.db.execute("UPDATE translation_batches SET status='failed',error=?,updated_at=? WHERE session_id=? AND batch_id=?",
                (str(error)[:800], now, sid, batch_id))
            for item in items:
                self.db.execute("UPDATE segments SET status='failed',error=? WHERE id=? AND session_id=? AND (zh='' OR status!='done')",
                    (str(error)[:800], item['id'], sid))
        finished = self._finish_run_if_terminal(sid, run_id)
        return {'ok':True,'run_finished':finished,'counts':self.batch_progress(sid, run_id)}

    def interrupt_batch_run(self, sid, run_id=None):
        with self.db:
            run = self.db.execute('SELECT * FROM batch_runs WHERE session_id=?', (sid,)).fetchone()
            if not run or (run_id and run['run_id'] != run_id) or run['status'] in ('completed','cancelled'):return None
            self.db.execute("UPDATE batch_runs SET status='interrupted',updated_at=? WHERE session_id=?", (utc_now(), sid))
            self.db.execute("UPDATE translation_batches SET status='pending',claim='',updated_at=? WHERE session_id=? AND run_id=? AND status='translating'",
                (utc_now(), sid, run['run_id']))
            return run['run_id']

    def resume_batch_run(self, sid, run_id=None):
        run = self.db.execute('SELECT * FROM batch_runs WHERE session_id=?', (sid,)).fetchone()
        if not run or (run_id and run['run_id'] != run_id) or run['status'] in ('completed','cancelled'):return []
        if run['status'] not in ('translating','interrupted','preparing'):return []
        with self.db:
            self.db.execute("UPDATE batch_runs SET status='translating',error='',updated_at=? WHERE session_id=?", (utc_now(), sid))
            self.db.execute("UPDATE translation_batches SET status='pending',claim='',updated_at=? WHERE session_id=? AND run_id=? AND status='translating'",
                (utc_now(), sid, run['run_id']))
        return [dict(row) for row in self.db.execute(
            "SELECT * FROM translation_batches WHERE session_id=? AND run_id=? AND status='pending' ORDER BY ordinal",
            (sid, run['run_id']))]

    def retry_batch_run(self, sid):
        run = self.db.execute('SELECT * FROM batch_runs WHERE session_id=?', (sid,)).fetchone()
        if not run:return None
        with self.db:
            self.db.execute("UPDATE batch_runs SET status='translating',error='',updated_at=? WHERE session_id=?", (utc_now(), sid))
            self.db.execute("UPDATE translation_batches SET status='pending',claim='',error='',updated_at=? WHERE session_id=? AND run_id=? AND status IN ('failed','cancelled','interrupted')",
                (utc_now(), sid, run['run_id']))
            self.db.execute("UPDATE segments SET status='queued',error='' WHERE session_id=? AND zh='' AND status IN ('failed','interrupted','disabled')", (sid,))
        return run['run_id']

    def cancel_batch_run(self, sid):
        run = self.db.execute('SELECT * FROM batch_runs WHERE session_id=?', (sid,)).fetchone()
        if not run or run['status'] in ('completed','cancelled'):return None
        with self.db:
            self.db.execute("UPDATE batch_runs SET status='cancelled',error='批量翻译已取消，可在历史记录中重试',updated_at=? WHERE session_id=?", (utc_now(), sid))
            self.db.execute("UPDATE translation_batches SET status='cancelled',claim='',updated_at=? WHERE session_id=? AND run_id=? AND status IN ('pending','translating')",
                (utc_now(), sid, run['run_id']))
            self.db.execute("UPDATE segments SET status='interrupted',error='翻译已取消，可在历史记录中重试' WHERE session_id=? AND zh='' AND status IN ('queued','pending','failed','disabled')", (sid,))
        return run['run_id']

    def disable_file_translation(self, sid):
        """Keep the plan resumable but show untranslated rows as disabled."""
        with self.db:
            self.db.execute("UPDATE batch_runs SET status='interrupted',updated_at=? WHERE session_id=? AND status!='completed'",
                (utc_now(), sid))
            self.db.execute("UPDATE translation_batches SET status='pending',claim='',updated_at=? WHERE session_id=? AND status='translating'",
                (utc_now(), sid))
            self.db.execute("UPDATE segments SET status='disabled',error='' WHERE session_id=? AND zh='' AND status IN ('queued','pending','interrupted','failed')", (sid,))

    def fail_file_translation(self, sid, error):
        error = str(error)[:800]
        with self.db:
            self.db.execute("UPDATE segments SET status='failed',error=? WHERE session_id=? AND zh='' AND status IN ('queued','pending','interrupted','disabled')", (error, sid))
            self.db.execute("UPDATE batch_runs SET status='failed',error=?,updated_at=? WHERE session_id=?", (error, utc_now(), sid))

    def finish_batch_run_if_terminal(self, sid, run_id):
        return self._finish_run_if_terminal(sid, run_id)

    def apply_review_translation(self, sid, ident, text, flags, *, expected_source, expected_translation, expected_run_id):
        text = str(text).strip()
        if not text: return False
        with self.db:
            run = self.batch_run(sid); row = self.segment(ident)
            if not run or run['run_id'] != expected_run_id or run['status'] not in ('completed','failed'): return False
            if not row or row['session_id'] != sid or row['en'] != expected_source or row['zh'] != expected_translation: return False
            self.db.execute("UPDATE segments SET zh=?,status='done',error='' WHERE id=? AND session_id=?", (text, ident, sid))
            self.replace_review_flags(sid, ident, flags)
        return True

    def review_cues(self, sid):
        """Current suspect cues, grouped by cue and ordered by source timestamp."""
        grouped = {}
        for batch in self.db.execute("SELECT flags FROM translation_batches WHERE session_id=? AND status='done'", (sid,)):
            try: flags = json.loads(batch['flags'])
            except (ValueError, TypeError): continue
            if not isinstance(flags, list): continue
            for flag in flags:
                if not isinstance(flag, dict): continue
                ident = flag.get('id')
                row = self.segment(ident)
                if not row or row['session_id'] != sid: continue
                if ident not in grouped: grouped[ident] = dict(row, flags=[])
                grouped[ident]['flags'].append(flag)
        return sorted(grouped.values(), key=lambda row: (row['start_ms'], row['seq']))

    def replace_review_flags(self, sid, ident, flags):
        """Replace only one cue's flags without disturbing neighbouring reviews."""
        with self.db:
            batches = self.db.execute('SELECT batch_id,flags FROM translation_batches WHERE session_id=?', (sid,)).fetchall()
            destination = None
            for batch in batches:
                try: old = json.loads(batch['flags'])
                except (ValueError, TypeError): old = []
                if not isinstance(old, list): old = []
                retained = [flag for flag in old if not isinstance(flag, dict) or flag.get('id') != ident]
                if len(retained) != len(old):
                    destination = batch['batch_id']
                    self.db.execute('UPDATE translation_batches SET flags=? WHERE session_id=? AND batch_id=?',
                        (json.dumps(retained, ensure_ascii=False), sid, batch['batch_id']))
            if flags and destination:
                row = self.db.execute('SELECT flags FROM translation_batches WHERE session_id=? AND batch_id=?', (sid, destination)).fetchone()
                self.db.execute('UPDATE translation_batches SET flags=? WHERE session_id=? AND batch_id=?',
                    (json.dumps(json.loads(row['flags']) + flags, ensure_ascii=False), sid, destination))

    def batch_flag_count(self, sid, run_id=None):
        sql = "SELECT flags FROM translation_batches WHERE session_id=? AND status='done'"
        args = [sid]
        if run_id is not None:
            sql += ' AND run_id=?'; args.append(run_id)
        count = 0
        for row in self.db.execute(sql, args):
            try:
                value = json.loads(row['flags'])
                count += len(value) if isinstance(value, list) else 0
            except (ValueError, TypeError):
                continue
        return count

    def resumable_batch_runs(self):
        return [dict(row) for row in self.db.execute('''
            SELECT r.* FROM batch_runs r JOIN file_sessions f ON f.session_id=r.session_id
            WHERE r.status IN ('translating','interrupted','preparing')
            ORDER BY r.updated_at''')]

    def close(self):
        self.db.close()


def cue_time(ms, separator=','):
    ms = max(0,int(ms))
    h, r = divmod(ms,3600000)
    m, r = divmod(r,60000)
    s, milli = divmod(r,1000)
    return f'{h:02d}:{m:02d}:{s:02d}{separator}{milli:03d}'


def export_session(history, sid, path):
    """Legacy single-session entry point; full formatting lives in subtitle_export."""
    from subtitle_export import export_single_file
    return export_single_file(history, sid, path)
