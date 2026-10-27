"""Synthetic cross-language boundaries; never includes private user recordings."""
import sys,json,time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from text_rules import sentence_spans,complete_text,rule_language,terminal
from segmentation import SentenceAssembler,TextAssembler
from text_agreement import TextAgreement

cases=[
 ('German','Die Messung zeigt, dass die Probe stabil ist. Ich prüfe danach den zweiten Sensor.',2),
 ('German','Wir sehen, dass beide Sensoren bereit sind. In diesem Versuch messen wir die Spannung.',2),
 ('German','Die Kontrolle endet. Danach folgt der nächste Versuch.',2),
 ('German','Hier endet der Versuch. Messungen beginnen morgen.',2),
 ('English','This is what the result is. We repeat the experiment.',2),
 ('English','Which detector were you working with? We used the small detector.',2),
 ('English','The result is ready. In this trial, we measure again.',2),
 ('English','The detector is stable. the next measurement begins.',2),
 ('English','We visited CERN. Scientists explained the measurements.',2),
 ('French','Le résultat est prêt. ensuite nous vérifions le détecteur.',2),
 ('French','La mesure est terminée. Nous continuons demain.',2),
 ('Spanish','La medición ha terminado. ahora comprobamos el detector.',2),
 ('Italian','La misura è terminata. poi controlliamo il risultato.',2),
 ('Portuguese','A medição terminou. depois verificamos o resultado.',2),
 ('Dutch','We weten dat het resultaat betrouwbaar is. Daarna meten we opnieuw.',2),
 ('Swedish','Mätningen är klar. sedan fortsätter vi med försöket.',2),
 ('Russian','Измерение завершено. затем мы проверяем результат.',2),
 ('Ukrainian','Вимірювання завершено. потім ми перевіряємо результат.',2),
 ('Greek','Είναι έτοιμο; Συνεχίζουμε το πείραμα.',2),
 ('Arabic','هل انتهى القياس؟ نتحقق من النتيجة.',2),
 ('Hindi','मापन पूरा हो गया। अब परिणाम की जाँच करें।',2),
 ('Chinese','第一轮测量结束。接下来检查探测器。然后开始第二轮实验。',3),
 ('Japanese','測定が終わりました。次の実験を始めます。',2),
 ('Korean','측정이 끝났습니다. 다음 실험을 시작합니다.',2),
]
negative=[
 ('English','The measured energy is approximately. One hundred GeV.',1),
 ('English','We visit the National. Research Laboratory. They agree.',2),
 ('English','We measured the response. With care.',1),
 ('English','The mass is 125. GeV. We check Eq. (2).',2),
 ('English','Dr. Green checked the sensor. We continue.',2),
 ('German','Die Probe ist. Noch nicht bereit.',1),
 ('German','Wir verwenden z.B. einen Sensor. Danach messen wir erneut.',2),
 ('French','La valeur est. Encore inconnue.',1),
 ('Russian','Мы проверяем рис. 2. Затем продолжаем.',2),
 ('Chinese','这是因为。探测器还需要校准。',1),
]
checked=0
for language,text,expected in cases+negative:
 for chosen in (language,'auto'):
  spans=list(sentence_spans(text,chosen));parts=[text[a:b].strip() for a,b in spans]
  assert len(parts)==expected,(language,chosen,rule_language(text),parts)
  assert ''.join(text[a:b] for a,b in spans)==text,(text,spans)
  a=TextAssembler();events=a.update(text,0,12,now=13,final=True,language=chosen)
  assert [e['text'] for e in events]==parts,(language,chosen,events,parts)
  assert all(e['audio_start_ms']<e['audio_end_ms'] for e in events)
  if language not in ('Chinese','Japanese'):
   words=[(w,i*.16) for i,w in enumerate(text.split())]
   s=SentenceAssembler(language=chosen);events=s.update(words,words,len(words)*.16+.8,final=True)
   assert len(events)==expected,(language,chosen,events)
   # A second identical hypothesis releases established earlier sentences
   # during speech. The last sentence waits for a real endpoint as before.
   s=SentenceAssembler(language=chosen);assert not s.update(words,words,len(words)*.16)
   events=s.update([],words,len(words)*.16+.1)
   assert len(events)==expected-1,(language,chosen,events)
  agreement=TextAgreement();assert not agreement.update(text,language=chosen)[0]
  agreed=agreement.update(text,language=chosen)[0]
  if expected>1:assert agreed and not agreed.endswith(parts[-1]),(language,chosen,agreed)
  checked+=1

# A window-edge sentence remains pending and joins a genuine continuation;
# independent lowercase sentences no longer lose the preceding full stop.
for language,left,right in [
 ('German','Wir wissen, dass der Sensor bereit ist.','danach prüfen wir die Daten.'),
 ('French','La mesure est terminée.','ensuite nous contrôlons le résultat.'),
 ('Chinese','测量结束。','接下来分析结果。')]:
 a=TextAssembler();assert not a.update(left,0,12,now=12,language=language,window_limit=True)
 events=a.update(right,12,16,now=17,language=language,final=True)
 assert len(events)==2 and events[0]['text']==left,(language,events)

for language,text in [('English','The measured energy is'),('German','Die Probe ist'),('French','La mesure est'),('Chinese','这是因为')]:
 assert not complete_text(text,language)
 assert not complete_text(text,'auto'),(language,rule_language(text))

# Warm-cache work stays bounded; no model or language-detection service.
t=time.perf_counter()
for _ in range(10000):
 for language,text,_ in cases:complete_text(text,language);rule_language(text)
print(json.dumps({'synthetic_language_cases':checked,'window_continuation':True,
 'no_private_fixture':True,'warm_cached_checks_ms':round((time.perf_counter()-t)*1000,2)}))
