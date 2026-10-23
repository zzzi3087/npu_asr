"""Portable, versioned configuration. Never reads or exports Credential Manager."""
import copy,json,math,re,uuid
from pathlib import Path
from urllib.parse import urlsplit,urlunsplit,parse_qsl,urlencode
from storage import default_settings
from theme import DEFAULT_COLORS,normalize_colors
from typography import normalize_typography
from languages import LANGUAGES
from audio_gain import DEFAULT_AGC,normalize_agc
from domain_bias import normalize_domain_bias
from desktop_caption_config import normalize_desktop_captions
from audio_sources import normalize_audio_source

FORMAT='parakeet-npu-settings'
SECRET_NAMES={'apikey','authorization','accesstoken','secret','token','password','credential','credentials','clientsecret','refreshtoken'}

def is_secret(key):return re.sub('[^a-z0-9]','',str(key).lower()) in SECRET_NAMES

def without_secrets(value):
    if isinstance(value,dict):return {key:without_secrets(item) for key,item in value.items() if not is_secret(key)}
    if isinstance(value,list):return [without_secrets(item) for item in value]
    return value

def clean_url(value):
    try:p=urlsplit(value)
    except ValueError:raise ValueError('API 地址格式无效') from None
    if p.username or p.password:raise ValueError('API 地址不能包含用户名或密码')
    query=urlencode([(key,val) for key,val in parse_qsl(p.query,keep_blank_values=True) if not is_secret(key)])
    return urlunsplit((p.scheme,p.netloc,p.path,query,p.fragment))

def checked_colors(value):
    if not isinstance(value,dict):raise ValueError('配色配置必须是对象')
    for key,color in value.items():
        if key in DEFAULT_COLORS and (not isinstance(color,str) or not re.fullmatch(r'#[0-9a-fA-F]{6}',color)):
            raise ValueError('颜色须为 #RRGGBB：'+key)
    return normalize_colors(value)

def validate_configuration(value):
    if not isinstance(value,dict):raise ValueError('配置必须是 JSON 对象')
    result=default_settings()
    for key in result:
        if key in value:result[key]=copy.deepcopy(value[key])
    for key,lo,hi in [('english_size',12,72),('chinese_size',12,72),('opacity',40,100),('interval',1,2)]:
        number=result[key]
        if type(number) not in (int,float) or not math.isfinite(number) or not lo<=number<=hi:
            raise ValueError('数值超出范围：'+key)
        if key!='interval':result[key]=int(number)
    for key in ('translate','topmost','quiet','show_source','show_translation'):
        if type(result[key]) is not bool:raise ValueError('布尔参数无效：'+key)
    if result['interval'] not in (1.,1.5,2.):raise ValueError('预览间隔须为 1、1.5 或 2 秒')
    if result['segmentation'] not in ('balanced','sentence','fast'):raise ValueError('不支持的分句策略')
    result['typography']=normalize_typography(result['typography'],strict=True)
    if result['asr_model'] not in ('parakeet','qwen3-asr-1.7b','mega-asr'):raise ValueError('不支持的转录模型')
    if result['feature_backend'] not in ('numpy','gpu'):raise ValueError('不支持的音频特征计算设备')
    if result['source_language'] not in {item[0] for item in LANGUAGES}:raise ValueError('不支持的转录语言')
    if not isinstance(result['target_language'],str) or not 1<=len(result['target_language'])<=80:raise ValueError('翻译目标语言无效')
    if not isinstance(result['agc'],dict):raise ValueError('增益参数必须是对象')
    result['agc']=normalize_agc(result['agc'])
    result['domain_bias']=normalize_domain_bias(result['domain_bias'],strict=True)
    result['desktop_captions']=normalize_desktop_captions(result['desktop_captions'],strict=True)
    result['audio_source']=normalize_audio_source(value.get('audio_source'),strict=True,legacy=result.get('microphone_name',''))
    result['colors']=checked_colors(result['colors'])
    themes=result['saved_themes']
    if not isinstance(themes,list) or len(themes)>100:raise ValueError('自定义配色方案数量无效')
    result['saved_themes']=[];theme_ids=set();names=set()
    for theme in themes:
        if not isinstance(theme,dict) or not isinstance(theme.get('name'),str) or not 1<=len(theme['name'].strip())<=80:
            raise ValueError('自定义配色名称无效')
        name=theme['name'].strip()
        if name in names:raise ValueError('存在同名自定义配色')
        ident=theme.get('id') or uuid.uuid4().hex
        if not isinstance(ident,str) or len(ident)>100 or ident in theme_ids:raise ValueError('自定义配色 ID 无效')
        result['saved_themes'].append({'id':ident,'name':name,'colors':checked_colors(theme.get('colors',{}))})
        theme_ids.add(ident);names.add(name)
    profiles=result['profiles']
    if not isinstance(profiles,list) or not 1<=len(profiles)<=100:raise ValueError('翻译服务配置数量无效')
    result['profiles']=[];ids=set()
    for profile in profiles:
        if not isinstance(profile,dict):raise ValueError('翻译服务配置无效')
        p=without_secrets({key:copy.deepcopy(val) for key,val in profile.items()
            if key in {'id','name','protocol','base_url','model','auth','api_version','workspace','extra'}})
        for key,default in [('id',uuid.uuid4().hex),('name','自定义服务'),('protocol','openai'),('base_url',''),
            ('model',''),('auth','auto'),('api_version','2023-06-01'),('workspace','')]:
            p.setdefault(key,default)
            if not isinstance(p[key],str) or len(p[key])>2048:raise ValueError('翻译服务字段无效：'+key)
        if not p['id'] or p['id'] in ids:raise ValueError('翻译服务 ID 重复或为空')
        if p['protocol'] not in ('openai','anthropic') or p['auth'] not in ('auto','bearer','x-api-key','api-key','none'):
            raise ValueError('不支持的接口或认证格式')
        p['base_url']=clean_url(p['base_url'])
        p.setdefault('extra',{})
        if not isinstance(p['extra'],dict):raise ValueError('服务额外参数必须是对象')
        result['profiles'].append(p);ids.add(p['id'])
    if result['selected_profile'] not in ids:raise ValueError('所选翻译服务不存在')
    if not isinstance(result['microphone_name'],str) or len(result['microphone_name'])>1000:raise ValueError('麦克风名称无效')
    result['version']=3
    result['main_layout_version']=2
    return result

def export_configuration(settings,path):
    clean=validate_configuration(settings)
    document={'format':FORMAT,'version':1,'settings':clean}
    target=Path(path);temp=target.with_name(target.name+'.tmp')
    temp.write_text(json.dumps(document,ensure_ascii=False,indent=2,allow_nan=False),'utf-8')
    temp.replace(target)

def import_configuration(path):
    path=Path(path)
    if path.stat().st_size>2*1024*1024:raise ValueError('配置文件超过 2 MB')
    try:doc=json.loads(path.read_text('utf-8-sig'))
    except (ValueError,UnicodeError):raise ValueError('无法解析 JSON 配置文件') from None
    if not isinstance(doc,dict):raise ValueError('配置必须是 JSON 对象')
    if 'format' in doc:
        if doc['format']!=FORMAT or doc.get('version')!=1:raise ValueError('不支持的配置格式或版本')
        doc=doc.get('settings')
    elif not any(key in doc for key in ('profiles','colors','typography','english_size')):
        raise ValueError('文件中没有可识别的工作台配置')
    return validate_configuration(doc)
