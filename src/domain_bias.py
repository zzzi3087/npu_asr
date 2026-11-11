"""Portable local domain profiles; model-specific bias is always opt-in."""
import math,re,uuid

def default_domain_bias():
    return {'active':'physics-en','translation_enabled':False,'parakeet':False,'parakeet_v3':False,'qwen':False,'mega':False,'strength':1.2,'profiles':[
        {'id':'physics-en','name':'物理学 · 英语','background':'Particle physics, theoretical physics and astrophysics.',
         'terms':['Schwarzschild','Feynman','Kaluza-Klein','axions','weakly interacting massive particles','neutrino oscillation','quantum field theory','quantum chromodynamics','Higgs boson','Lorentz symmetry','AdS/CFT','renormalization']},
        {'id':'physics-zh','name':'物理学 · 中文','background':'高能物理、理论物理与天体物理。',
         'terms':['施瓦西度规','费曼图','卡鲁扎-克莱因','轴子','弱相互作用大质量粒子','中微子振荡','量子场论','量子色动力学','希格斯玻色子','洛伦兹对称性','重整化']},
        {'id':'darkmatter-en','name':'暗物质与天体粒子 · 英语','background':'Dark matter searches and astroparticle physics.',
         'terms':['axion','WIMP','axion-like particle','sterile neutrino','direct detection','indirect detection','spin-independent cross section','XENONnT','LUX-ZEPLIN','PandaX','IceCube','cosmic microwave background']},
        {'id':'darkmatter-zh','name':'暗物质与天体粒子 · 中文','background':'暗物质搜索与天体粒子物理。',
         'terms':['暗物质','轴子','轴子样粒子','惰性中微子','弱相互作用大质量粒子','直接探测','间接探测','自旋无关散射截面','液氙','宇宙微波背景','宇宙线','引力透镜']},
        {'id':'astro-en','name':'天体物理与宇宙学 · 英语','background':'Astrophysics and cosmology.',
         'terms':['Hubble parameter','Friedmann equations','FLRW metric','cosmological constant','redshift','Schwarzschild','Kerr metric','accretion disk','gravitational lensing','gravitational waves','Lambda-CDM','Planck mission']},
        {'id':'astro-zh','name':'天体物理与宇宙学 · 中文','background':'天体物理与宇宙学。',
         'terms':['哈勃参数','弗里德曼方程','宇宙学常数','红移','施瓦西度规','克尔度规','吸积盘','引力透镜','引力波','宇宙微波背景','星系团','普朗克卫星']}
    ]}

def normalize_domain_bias(value=None,strict=False):
    try:
        if value is None:return default_domain_bias()
        if not isinstance(value,dict):raise ValueError('领域词表配置必须是对象')
        result=default_domain_bias()
        for key in ('parakeet','parakeet_v3','qwen','mega','translation_enabled'):
            v=value.get(key,False)
            if type(v) is not bool:raise ValueError('领域增强开关必须是布尔值')
            result[key]=v
        strength=value.get('strength',1.2)
        if type(strength) not in (int,float) or not math.isfinite(strength) or not 0<=strength<=2:raise ValueError('词组加权强度须为 0–2')
        result['strength']=float(strength)
        profiles=value.get('profiles',result['profiles'])
        if not isinstance(profiles,list) or not 1<=len(profiles)<=32:raise ValueError('领域方案数量须为 1–32')
        result['profiles']=[];ids=set();names=set()
        for profile in profiles:
            if not isinstance(profile,dict):raise ValueError('领域方案格式无效')
            ident=profile.get('id') or uuid.uuid4().hex;name=profile.get('name','')
            if not isinstance(ident,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',ident) or ident in ids:raise ValueError('领域方案 ID 无效或重复')
            if not isinstance(name,str) or not 1<=len(name.strip())<=80 or name.strip().casefold() in names:raise ValueError('领域名称为空、重复或过长')
            background=profile.get('background','');terms=profile.get('terms',[])
            if not isinstance(background,str) or len(background)>600:raise ValueError('领域背景须在 600 字符以内')
            if not isinstance(terms,list) or len(terms)>64:raise ValueError('每个领域最多 64 个词组')
            clean=[];seen=set()
            for term in terms:
                if not isinstance(term,str) or not 1<=len(term.strip())<=160 or '\n' in term or '\r' in term:raise ValueError('术语须为非空单行文字，最长 160 字符')
                term=term.strip()
                if term not in seen:clean.append(term);seen.add(term)
            if len(background)+sum(map(len,clean))>4096:raise ValueError('领域背景与词表总长度过长')
            if any(tag in background+'\n'+'\n'.join(clean) for tag in ('<|im_start|>','<|im_end|>','<|audio','<asr_text>','<|endoftext|>')):
                raise ValueError('领域词表不能包含模型控制标记')
            preferred=profile.get('preferred_names',{})
            if not isinstance(preferred,dict) or len(preferred)>32:raise ValueError('首选译名须按目标语言分组，最多 32 种语言')
            preferred_clean={};entry_count=0;preferred_size=0
            for language,mapping in preferred.items():
                if not isinstance(language,str) or not 1<=len(language.strip())<=80 or '\n' in language or '\r' in language:raise ValueError('首选译名目标语言无效')
                language=language.strip()
                if language.casefold() in {key.casefold() for key in preferred_clean} or not isinstance(mapping,dict):raise ValueError('首选译名语言重复或格式无效')
                target={};seen_sources=set()
                for source,preferred_name in mapping.items():
                    for item in (source,preferred_name):
                        if not isinstance(item,str) or not 1<=len(item.strip())<=160 or '\n' in item or '\r' in item:raise ValueError('原文与首选译名须为非空单行文字，最长 160 字符')
                        if any(tag in item for tag in ('<|im_start|>','<|im_end|>','<|audio','<asr_text>','<|endoftext|>')):raise ValueError('首选译名不能包含模型控制标记')
                    source=source.strip();preferred_name=preferred_name.strip()
                    if source.casefold() in seen_sources:raise ValueError('同一目标语言存在重复原文术语')
                    seen_sources.add(source.casefold());target[source]=preferred_name
                    entry_count+=1;preferred_size+=len(source)+len(preferred_name)
                preferred_clean[language]=target
            if entry_count>256 or preferred_size>16384:raise ValueError('首选译名最多 256 对，总长度最多 16384 字符')
            result['profiles'].append({'id':ident,'name':name.strip(),'background':background.strip(),'terms':clean,'preferred_names':preferred_clean})
            ids.add(ident);names.add(name.strip().casefold())
        result['active']=value.get('active',result['profiles'][0]['id'])
        if result['active'] not in ids:raise ValueError('所选领域不存在')
        return result
    except (ValueError,TypeError):
        if strict:raise
        return default_domain_bias()

def active_domain(value):
    config=normalize_domain_bias(value,strict=True)
    return config,next(p for p in config['profiles'] if p['id']==config['active'])

def build_context(profile):
    parts=[]
    if profile['background']:parts.append('Topic: '+profile['background'])
    if profile['terms']:parts.append('Vocabulary: '+', '.join(profile['terms'])+'.')
    return '\n'.join(parts)

def model_bias(value,model_id):
    config,profile=active_domain(value)
    key={'parakeet':'parakeet','parakeet-v3':'parakeet_v3','qwen3-asr-1.7b':'qwen','mega-asr':'mega'}.get(model_id)
    if not key or not config[key]:return {'context':'','terms':[],'strength':0.}
    if model_id=='parakeet':
        terms=[t for t in profile['terms'] if all(ord(c)<128 for c in t)]
        if not terms:raise ValueError('Parakeet 需要英文词组，请切换到英文领域方案')
        return {'context':'','terms':terms,'strength':config['strength']}
    if model_id=='parakeet-v3':return {'context':'','terms':profile['terms'],'strength':config['strength']}
    return {'context':build_context(profile),'terms':[],'strength':0.}


def domain_translation_glossary(value, text, target_language, limit=24, max_chars=1200):
    """Explicit preferred names for the active, translation-enabled domain only.

    Target language labels are matched exactly (ignoring case/outer whitespace).
    ASR vocabulary and previously generated translations never imply a mapping.
    """
    try:
        config,profile=active_domain(value)
    except (ValueError,TypeError,KeyError):
        return []
    if not config['translation_enabled'] or not isinstance(text,str) or not isinstance(target_language,str):return []
    target_key=target_language.strip().casefold()
    mapping=next((names for language,names in profile.get('preferred_names',{}).items()
                  if language.casefold()==target_key),{})
    found=[];used=0
    for source,preferred in mapping.items():
        pattern=re.escape(source)
        if source[0].isascii() and source[0].isalnum():pattern=r'(?<![A-Za-z0-9_])'+pattern
        if source[-1].isascii() and source[-1].isalnum():pattern+=r'(?![A-Za-z0-9_])'
        if not re.search(pattern,text,re.I):continue
        cost=len(source)+len(preferred)
        if len(found)>=max(0,limit):break
        if used+cost>max_chars:continue
        found.append({'source':source,'preferred':preferred});used+=cost
    return found
