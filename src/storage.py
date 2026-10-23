"""Local settings and durable, ordered subtitle sessions. No secrets in SQLite."""
import csv
import io
import json
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

from app_paths import DATA
SERVICE = 'ParakeetNPUSubtitles'


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds')


def default_settings():
    return {'version': 3, 'main_layout_version':2, 'selected_profile': 'deepseek', 'translate': True,
        'show_source':True,'show_translation':True,
        'close_to_tray':True,'minimize_to_tray':True,
        'asr_model':'parakeet','source_language':'auto','target_language':'简体中文','agc':dict(DEFAULT_AGC),'feature_backend':'numpy','domain_bias':default_domain_bias(),'desktop_captions':default_desktop_captions(),
        'english_size': 24, 'chinese_size': 22, 'opacity': 100, 'topmost': False,
        'quiet': True, 'interval': 1.5, 'segmentation': 'balanced', 'colors':dict(DEFAULT_COLORS),
        'theme':normalize_theme(), 'saved_themes':[], 'typography':default_typography(), 'microphone_name': '', 'audio_source':normalize_audio_source(), 'profiles': [
            {'id': 'deepseek', 'name': 'DeepSeek', 'protocol': 'openai',
             'base_url': 'https://api.deepseek.com', 'model': 'deepseek-flash',
             'auth': 'auto', 'api_version': '2023-06-01', 'workspace': '',
             'extra': {'thinking': {'type': 'disabled'}, 'max_tokens': 1200}},
            {'id': 'openai', 'name': 'OpenAI', 'protocol': 'openai',
             'base_url': 'https://api.openai.com/v1', 'model': '', 'auth': 'auto',
             'api_version': '2023-06-01', 'workspace': '', 'extra': {}},
            {'id': 'anthropic', 'name': 'Anthropic', 'protocol': 'anthropic',
             'base_url': 'https://api.anthropic.com', 'model': '', 'auth': 'auto',
             'api_version': '2023-06-01', 'workspace': '', 'extra': {}},
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
    for key in ('close_to_tray','minimize_to_tray'):
        if type(defaults[key]) is not bool:defaults[key]=True
    return defaults


def save_settings(settings, directory=DATA):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    # Keys are not accepted by the settings schema; they live in Credential Manager.
    clean = {k: settings.get(k,value) for k,value in default_settings().items()}
    clean['colors']=normalize_colors(settings.get('colors'))
    clean['theme']=normalize_theme(settings.get('theme'))
    clean['typography']=normalize_typography(settings.get('typography'))
    clean['agc']=normalize_agc(settings.get('agc'))
    clean['domain_bias']=normalize_domain_bias(settings.get('domain_bias'))
    clean['desktop_captions']=normalize_desktop_captions(settings.get('desktop_captions'))
    clean['audio_source']=normalize_audio_source(settings.get('audio_source'),legacy=settings.get('microphone_name',''))
    allowed = {'id','name','protocol','base_url','model','auth','api_version','workspace','extra'}
    clean['profiles'] = [{k:v for k,v in p.items() if k in allowed} for p in clean['profiles']]
    temp = directory / 'settings.json.tmp'
    temp.write_text(json.dumps(clean, ensure_ascii=False, indent=2), 'utf-8')
    temp.replace(directory / 'settings.json')


class History:
    def __init__(self, path=None, recover=False):
        path = Path(path) if path else DATA / 'history.sqlite3'
        path.parent.mkdir(parents=True, exist_ok=True)
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
        ''')
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

    def create_session(self, title, profile, session_id=None):
        sid = session_id or uuid.uuid4().hex
        with self.db:
            self.db.execute('INSERT INTO sessions VALUES (?,?,?,?,?,?)',
                (sid, title, utc_now(), None, profile.get('name',''), profile.get('model','')))
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

    def context(self, ident):
        row = self.segment(ident)
        if not row:
            return []
        data = self.db.execute('SELECT en FROM segments WHERE session_id=? AND seq<? ORDER BY seq DESC LIMIT 2',
                               (row['session_id'],row['seq'])).fetchall()
        return [r['en'] for r in reversed(data)]

    def session(self, sid):
        row = self.db.execute('SELECT * FROM sessions WHERE id=?',(sid,)).fetchone()
        return dict(row) if row else None

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

    def close(self):
        self.db.close()


def cue_time(ms, separator=','):
    ms = max(0,int(ms))
    h, r = divmod(ms,3600000)
    m, r = divmod(r,60000)
    s, milli = divmod(r,1000)
    return f'{h:02d}:{m:02d}:{s:02d}{separator}{milli:03d}'


def export_session(history, sid, path):
    path = Path(path)
    session, rows = history.session(sid), history.rows(sid)
    if not session:
        raise ValueError('请选择一条历史记录')
    suffix = path.suffix.lower()
    if suffix == '.json':
        path.write_text(json.dumps({'version':1,'session':session,'segments':rows}, ensure_ascii=False,indent=2), 'utf-8')
    elif suffix == '.csv':
        def excel_text(value):
            value = str(value)
            return "'"+value if value.lstrip().startswith(('=','+','-','@')) else value
        with path.open('w',encoding='utf-8-sig',newline='') as file:
            writer = csv.writer(file)
            writer.writerow(['序号','开始毫秒','结束毫秒','英文','中文','状态','边界待核'])
            for r in rows:
                writer.writerow([r['seq'],r['start_ms'],r['end_ms'],excel_text(r['en']),excel_text(r['zh']),r['status'],bool(r['uncertain'])])
    elif suffix in ('.srt','.vtt'):
        chunks = ['WEBVTT\n'] if suffix == '.vtt' else []
        for index,row in enumerate(rows,1):
            end = max(row['start_ms']+200,row['end_ms'])
            if index < len(rows) and rows[index]['start_ms'] > row['start_ms']:
                end = min(end,rows[index]['start_ms'])
            content = row['en'] + ('\n'+row['zh'] if row['zh'] else '')
            sep = '.' if suffix == '.vtt' else ','
            chunks.append(f'{index}\n{cue_time(row["start_ms"],sep)} --> {cue_time(end,sep)}\n{content}\n')
        path.write_text('\n'.join(chunks), 'utf-8-sig' if suffix == '.srt' else 'utf-8')
    elif suffix in ('.txt','.md'):
        blocks = [session['title'], session['created_at'], '']
        for row in rows:
            blocks.append(f'[{cue_time(row["start_ms"])}]'+(' [边界待核]' if row['uncertain'] else ''))
            blocks.append(row['en'])
            if row['zh']:
                blocks.append(row['zh'])
            blocks.append('')
        path.write_text('\n'.join(blocks), 'utf-8')
    else:
        raise ValueError('请选择 TXT、SRT、VTT、CSV 或 JSON 格式')
    return len(rows)
