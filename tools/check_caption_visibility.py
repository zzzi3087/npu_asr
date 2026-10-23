"""Isolated visibility and API-outage UI checks with a local fixture service."""
import copy,json,os,sys,threading,time
from pathlib import Path
from tempfile import TemporaryDirectory
from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler

temporary=TemporaryDirectory(prefix='captions-visibility-');folder=Path(temporary.name)
os.environ['QT_QPA_PLATFORM']='offscreen';os.environ['PARAKEET_DATA_DIR']=str(folder/'data')
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PySide6.QtCore import QTimer,QEventLoop,QPoint
from PySide6.QtWidgets import QApplication
from storage import default_settings,save_settings,load_settings
from config_io import export_configuration,import_configuration,validate_configuration
from caption_visibility import visible_caption_pair
import app

healthy=False;request_count=0
class Handler(BaseHTTPRequestHandler):
    def log_message(self,*_):pass
    def do_POST(self):
        global request_count
        request_count+=1;self.rfile.read(int(self.headers['Content-Length']))
        body=json.dumps({'choices':[{'message':{'content':'暗物质散射。'}}]} if healthy else {'error':{'message':'fixture unavailable'}}).encode()
        self.send_response(200 if healthy else 401);self.send_header('Content-Type','application/json')
        self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)

server=ThreadingHTTPServer(('127.0.0.1',0),Handler);threading.Thread(target=server.serve_forever,daemon=True).start()
qt=QApplication([])
def settle(ms=40):
    loop=QEventLoop();QTimer.singleShot(ms,loop.quit);loop.exec()
def until(check):
    deadline=time.monotonic()+5
    while not check() and time.monotonic()<deadline:settle(20)
    assert check(),'fixture response did not arrive'

s=default_settings();s['translate']=True;s['desktop_captions']['enabled']=True
profile=dict(s['profiles'][0],id='fixture',name='Local fixture',base_url=f'http://127.0.0.1:{server.server_port}/v1',model='fixture',auth='none')
s['profiles']=[profile];s['selected_profile']='fixture';save_settings(s)
app.get_key=lambda _profile:''
w=app.Window(start_workers=False);w.show();settle()
try:
    sid=w.history.create_session('Physics fixture',profile);w.current_session=w.view_session=sid
    first=w.history.add(sid,'Axions are dark matter candidates.',0,1000,status='done')
    w.history.update(first,'done','轴子是暗物质候选者。')
    failed=w.history.add(sid,'Dark matter scattering.',1000,2000,status='queued')
    w.render_now();settle()
    assert not any(text in w.browser.toPlainText() for text in ('等待翻译','翻译中','未启用','不可用'))
    w.pump(sid);until(lambda:w.history.segment(failed)['status']=='failed');settle(180)
    assert request_count==1 and 'Dark matter scattering.' in w.browser.toPlainText()
    assert 'HTTP 401' not in w.browser.toPlainText() and '密钥无效' not in w.browser.toPlainText()
    assert w.translation_warning.isVisible() and 'HTTP 401' in w.translation_warning.toolTip()
    w.clear_notice();assert w.translation_warning.isVisible()
    assert w.translation_warning.mapTo(w,QPoint()).x()>w.width()/2
    assert w.footer.height()<=24
    # One global pair of controls drives both displays, without changing storage.
    w.hide_source.setChecked(True);settle()
    text=w.browser.toPlainText();assert 'Axions are' not in text and '轴子是' in text
    assert 'Dark matter scattering.' in text  # missing translation falls back
    assert w.desktop._key[0]==(('', '轴子是暗物质候选者。'),('Dark matter scattering.',''))
    assert load_settings()['show_source'] is False
    w.hide_translation.setChecked(True);settle()
    assert not w.browser.toPlainText().strip() and not w.desktop.isVisible()
    w.hide_source.setChecked(False);settle()
    assert 'Axions are' in w.browser.toPlainText() and '轴子是' not in w.browser.toPlainText()
    assert all(not pair[1] for pair in w.desktop._key[0])
    w.hide_source.setChecked(True);w.hide_translation.setChecked(False);settle()
    healthy=True;w.retry_current();until(lambda:w.history.segment(failed)['status']=='done');settle(180)
    assert request_count==2 and not w.translation_warning.isVisible()
    assert '暗物质散射。' in w.browser.toPlainText() and 'Dark matter scattering.' not in w.browser.toPlainText()
    assert w.desktop._key[0][-1]==('', '暗物质散射。')
    assert w.history.segment(first)['en']=='Axions are dark matter candidates.'
    # Raw overlay drawing contains no branding or live-preview labels.
    w.hide_source.setChecked(False);settle()
    drawn=[];original=w.desktop._text_path
    def capture(text,*args,**kwargs):drawn.append(text);return original(text,*args,**kwargs)
    w.desktop._text_path=capture;w.capturing=True;w.last_partial='A neutrino oscillates.'
    w.refresh_desktop();settle()
    assert 'A neutrino oscillates.' in drawn
    assert not any(text.startswith(('实时字幕','实时预览')) for text in drawn)
    assert w.desktop.windowTitle()=='桌面字幕'
    before=w.desktop.rebuild_count
    for _ in range(100):w.refresh_desktop()
    settle();assert w.desktop.rebuild_count==before
    w.capturing=False
    for width in (1220,860):
        w.resize(width,830 if width==1220 else 620);settle()
        assert w.width()==width
        for control in (w.hide_source,w.hide_translation):
            position=control.mapTo(w,QPoint());assert position.x()>=0 and position.x()+control.width()<=w.width()
    config=folder/'config.json';export_configuration(w.settings,config)
    imported=import_configuration(config);assert (imported['show_source'],imported['show_translation'])==(True,True)
    invalid=copy.deepcopy(w.settings);invalid['show_source']='false'
    try:validate_configuration(invalid);raise AssertionError('non-boolean option accepted')
    except ValueError:pass
    # Stored old API errors remain in diagnostics, never in caption text.
    w.history.update(failed,'failed',error='Translation service unavailable')
    w.set_translation_status(None);w.render_now();settle()
    assert w.translation_warning.isVisible() and 'unavailable' not in w.browser.toPlainText()
    w.settings['translate']=False;w.render_now();assert not w.translation_warning.isVisible()
    print(json.dumps(dict(shared_visibility_controls=True,all_four_visibility_modes=True,
        missing_translation_falls_back_to_source=True,api_outage_footer_only=True,api_recovery_clears_warning=True,
        persistent_choices_and_config_roundtrip=True,original_history_preserved=True,overlay_has_no_prefix=True,
        small_window_fits=True,unchanged_captions_skip_redraw=True,fixture_http_requests=request_count),indent=2))
finally:
    w.close();settle(400);server.shutdown();server.server_close()
