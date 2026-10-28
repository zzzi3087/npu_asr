"""Speech language IDs accepted by Qwen3-ASR; translation uses explicit names."""
LANGUAGES=[('auto','自动识别','Automatic'),('Chinese','中文普通话','Chinese'),('English','英语','English'),
    ('Cantonese','粤语','Cantonese'),('Japanese','日语','Japanese'),('Korean','韩语','Korean'),
    ('German','德语','German'),('French','法语','French'),('Spanish','西班牙语','Spanish'),
    ('Portuguese','葡萄牙语','Portuguese'),('Italian','意大利语','Italian'),('Russian','俄语','Russian'),
    ('Arabic','阿拉伯语','Arabic'),('Indonesian','印尼语','Indonesian'),('Thai','泰语','Thai'),
    ('Vietnamese','越南语','Vietnamese'),('Turkish','土耳其语','Turkish'),('Hindi','印地语','Hindi'),
    ('Malay','马来语','Malay'),('Dutch','荷兰语','Dutch'),('Swedish','瑞典语','Swedish'),
    ('Danish','丹麦语','Danish'),('Finnish','芬兰语','Finnish'),('Polish','波兰语','Polish'),
    ('Czech','捷克语','Czech'),('Filipino','菲律宾语','Filipino'),('Persian','波斯语','Persian'),
    ('Greek','希腊语','Greek'),('Hungarian','匈牙利语','Hungarian'),('Macedonian','马其顿语','Macedonian'),
    ('Romanian','罗马尼亚语','Romanian')]
QWEN_LANGUAGES=frozenset(name for name,_,_ in LANGUAGES)
LANGUAGES += [('Bulgarian','保加利亚语','Bulgarian'),('Croatian','克罗地亚语','Croatian'),
    ('Estonian','爱沙尼亚语','Estonian'),('Latvian','拉脱维亚语','Latvian'),('Lithuanian','立陶宛语','Lithuanian'),
    ('Maltese','马耳他语','Maltese'),('Slovak','斯洛伐克语','Slovak'),('Slovenian','斯洛文尼亚语','Slovenian'),
    ('Ukrainian','乌克兰语','Ukrainian')]
PARAKEET_V3_LANGUAGES=frozenset('Bulgarian Croatian Czech Danish Dutch English Estonian Finnish French German Greek Hungarian Italian Latvian Lithuanian Maltese Polish Portuguese Romanian Slovak Slovenian Spanish Swedish Russian Ukrainian'.split())
ASR_MODELS=(('Parakeet v2 · 英语 · NPU','parakeet'),('Parakeet v3 · 25 种欧洲语言 · NPU','parakeet-v3'),
    ('Qwen3-ASR 1.7B · 多语言 · NPU','qwen3-asr-1.7b'),('Mega-ASR · 抗噪 · NPU','mega-asr'))
PARAKEET_MODELS=frozenset(('parakeet','parakeet-v3'))

def supported_language(model,language):
    if model=='parakeet':return language in ('auto','English')
    if model=='parakeet-v3':return language=='auto' or language in PARAKEET_V3_LANGUAGES
    return model in ('qwen3-asr-1.7b','mega-asr') and language in QWEN_LANGUAGES

def source_languages(model):
    return [item for item in LANGUAGES if supported_language(model,item[0])
        and not (model=='parakeet' and item[0]=='auto')]

def source_language_for_model(model,language):
    if model=='parakeet':return 'English'
    return language if supported_language(model,language) else 'auto'

TARGETS=[('简体中文','简体中文'),('繁體中文','繁體中文')]+[(name,label) for name,label,_ in LANGUAGES if name not in ('auto','Chinese')]
def label(language):return next((text for name,text,_ in LANGUAGES if name==language),language)
