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
TARGETS=[('简体中文','简体中文'),('繁體中文','繁體中文')]+[(name,label) for name,label,_ in LANGUAGES if name not in ('auto','Chinese')]
def label(language):return next((text for name,text,_ in LANGUAGES if name==language),language)
