import copy,uuid
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (QWidget,QVBoxLayout,QHBoxLayout,QFormLayout,QLabel,QComboBox,QLineEdit,
    QPlainTextEdit,QPushButton,QCheckBox,QDoubleSpinBox,QTableWidget,QTableWidgetItem,QHeaderView)
from domain_bias import normalize_domain_bias,model_bias


def validate_context(value,model_id):
    bias=model_bias(value,model_id)
    if bias['context']:
        from model_manager import ensure_model
        from qwen_tokenizer import load_tokenizer,checked_context
        folder=ensure_model(model_id,only={'vocab.json','merges.txt','tokenizer_config.json'})
        checked_context(load_tokenizer(str(folder)),bias['context'])


class DomainEditor(QWidget):
    def __init__(self,value=None,parent=None):
        super().__init__(parent);self.current=None;self.loading=False
        root=QVBoxLayout(self);root.setContentsMargins(20,22,20,20);root.setSpacing(12)
        note=QLabel('保存与切换不同专业的背景和术语。仅所选领域生效，各模型分别启用；保存后下次开始收音时应用。')
        note.setWordWrap(True);note.setObjectName('muted');root.addWidget(note)
        row=QHBoxLayout();self.selector=QComboBox();self.selector.currentIndexChanged.connect(self.switch)
        row.addWidget(self.selector,1)
        for title,method in [('＋ 新领域',self.add),('复制',self.duplicate),('删除',self.remove)]:
            button=QPushButton(title);button.clicked.connect(method);row.addWidget(button)
            if title=='删除':self.remove_button=button
        root.addLayout(row)
        form=QFormLayout();self.form=form;form.setVerticalSpacing(10);root.addLayout(form)
        self.name=QLineEdit();self.name.setMaxLength(80);form.addRow('领域名称',self.name)
        self.background=QPlainTextEdit();self.background.setFixedHeight(70)
        self.background.setPlaceholderText('简短背景，例如：暗物质直接探测与液氙实验。')
        form.addRow('背景',self.background)
        self.terms=QPlainTextEdit();self.terms.setMinimumHeight(150);self.terms.setMaximumHeight(230)
        self.terms.setPlaceholderText('每行一个完整术语或名称，例如：\nSchwarzschild\nKaluza-Klein\nXENONnT')
        form.addRow('术语 / 完整词组',self.terms)
        self.counter=QLabel();self.counter.setObjectName('muted');self.counter.setWordWrap(True);form.addRow(self.counter)
        self.translation_enabled=QCheckBox('翻译使用当前领域首选译名（与转录增强独立）')
        form.addRow(self.translation_enabled)
        self.preferred_names=QTableWidget(0,3)
        self.preferred_names.setHorizontalHeaderLabels(['目标语言','原文术语','首选译名'])
        self.preferred_names.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.preferred_names.setMinimumHeight(140)
        form.addRow('双语首选译名',self.preferred_names)
        preferred_note=QLabel('目标语言须与翻译设置一致，例如“简体中文”。仅原文中出现的术语生效；已有转录词组保持不变。')
        preferred_note.setWordWrap(True);preferred_note.setObjectName('muted');form.addRow(preferred_note)
        preferred_buttons=QHBoxLayout()
        for title,method in [('添加译名',self.add_preferred_name),('删除选中译名',self.remove_preferred_name)]:
            button=QPushButton(title);button.clicked.connect(method);preferred_buttons.addWidget(button)
        form.addRow(preferred_buttons)
        self.flags={}
        for key,label in [('parakeet','Parakeet v2：英文词组加权（实验）'),('parakeet_v3','Parakeet v3：多语言词组加权（实验）'),('qwen','Qwen：背景与术语提示'),('mega','Mega：背景与术语提示（单独对照效果）')]:
            field=QCheckBox(label);self.flags[key]=field;form.addRow(field)
        self.strength=QDoubleSpinBox();self.strength.setRange(0,2);self.strength.setSingleStep(.1);self.strength.setDecimals(1)
        form.addRow('Parakeet 加权强度',self.strength)
        caveat=QLabel('建议每次只放当前报告相关的 10–20 个术语。中英文分别建方案，并在“收音设置”选择实际转录语言。Qwen/Mega 的背景与词表最多 160 token；提示可能造成误报、漏识别或拼写变化，Mega 不保证优于 Qwen。')
        caveat.setWordWrap(True);caveat.setObjectName('muted');root.addWidget(caveat);self.caveat=caveat;self.model_id='parakeet'
        root.addStretch()
        self.timer=QTimer(self);self.timer.setSingleShot(True);self.timer.setInterval(250);self.timer.timeout.connect(self.update_counter)
        self.terms.textChanged.connect(self.schedule_counter);self.background.textChanged.connect(self.schedule_counter)
        self.set_values(value)

    def set_model(self,model_id):
        self.model_id=model_id
        key={'parakeet':'parakeet','parakeet-v3':'parakeet_v3','qwen3-asr-1.7b':'qwen','mega-asr':'mega'}.get(model_id)
        parakeet=model_id in ('parakeet','parakeet-v3')
        for name,field in self.flags.items():self.form.setRowVisible(field,name==key)
        self.form.setRowVisible(self.background,not parakeet)
        self.form.setRowVisible(self.strength,parakeet)
        self.caveat.setText('建议每次只放当前报告相关的 10–20 个术语。提示可能造成误报、漏识别或拼写变化。' + ('' if parakeet else ' Qwen/Mega 的背景与词表最多 160 token。') + (' Mega 效果需对照，不能保证优于 Qwen。' if model_id=='mega-asr' else ''))
        self.update_counter()

    def schedule_counter(self):
        if not self.loading:self.timer.start()

    def add_preferred_name(self,*_):
        row=self.preferred_names.rowCount();self.preferred_names.insertRow(row)
        for column in range(3):self.preferred_names.setItem(row,column,QTableWidgetItem(''))
        self.preferred_names.setCurrentCell(row,0)

    def remove_preferred_name(self,*_):
        rows={item.row() for item in self.preferred_names.selectedItems()}
        for row in sorted(rows,reverse=True):self.preferred_names.removeRow(row)

    def stash(self):
        if self.current is None or self.loading:return
        p=next(p for p in self.config['profiles'] if p['id']==self.current)
        p.update(name=self.name.text().strip(),background=self.background.toPlainText().strip(),
            terms=[s.strip() for s in self.terms.toPlainText().splitlines() if s.strip()])
        # Keep all rows until values() validates, including incomplete edits.
        p['_preferred_rows']=[[self.preferred_names.item(row,col).text().strip()
            if self.preferred_names.item(row,col) else '' for col in range(3)]
            for row in range(self.preferred_names.rowCount())]
        index=self.selector.findData(self.current)
        if index>=0:self.selector.setItemText(index,p['name'] or '未命名领域')

    def switch(self,*_):
        if self.loading:return
        self.stash();self.current=self.selector.currentData()
        if self.current is None:return
        p=next(p for p in self.config['profiles'] if p['id']==self.current)
        self.loading=True;self.name.setText(p['name']);self.background.setPlainText(p['background']);self.terms.setPlainText('\n'.join(p['terms']))
        rows=p.get('_preferred_rows',[[language,source,target] for language,mapping in p.get('preferred_names',{}).items() for source,target in mapping.items()])
        self.preferred_names.setRowCount(len(rows))
        for row,values in enumerate(rows):
            for col,text in enumerate(values):self.preferred_names.setItem(row,col,QTableWidgetItem(text))
        self.loading=False;self.remove_button.setEnabled(len(self.config['profiles'])>1);self.update_counter()

    def set_values(self,value):
        self.loading=True;self.current=None;self.config=normalize_domain_bias(value)
        self.selector.clear()
        for p in self.config['profiles']:self.selector.addItem(p['name'],p['id'])
        self.selector.setCurrentIndex(self.selector.findData(self.config['active']))
        for key,field in self.flags.items():field.setChecked(self.config[key])
        self.translation_enabled.setChecked(self.config['translation_enabled'])
        self.strength.setValue(self.config['strength']);self.loading=False;self.switch()

    def add(self,*_):
        self.stash()
        if len(self.config['profiles'])>=32:self.counter.setText('最多保存 32 个领域');return
        names={p['name'] for p in self.config['profiles']};name='自定义领域';i=2
        while name in names:name='自定义领域 '+str(i);i+=1
        p={'id':uuid.uuid4().hex,'name':name,'background':'','terms':[]}
        self.config['profiles'].append(p);self.selector.addItem(name,p['id']);self.selector.setCurrentIndex(self.selector.count()-1)

    def duplicate(self,*_):
        self.stash()
        if len(self.config['profiles'])>=32:self.counter.setText('最多保存 32 个领域');return
        p=copy.deepcopy(next(p for p in self.config['profiles'] if p['id']==self.current));p['id']=uuid.uuid4().hex
        names={q['name'] for q in self.config['profiles']};base=p['name'][:65]+' · 副本';p['name']=base;i=2
        while p['name'] in names:p['name']=base+' '+str(i);i+=1
        self.config['profiles'].append(p);self.selector.addItem(p['name'],p['id']);self.selector.setCurrentIndex(self.selector.count()-1)

    def remove(self,*_):
        if len(self.config['profiles'])<=1:return
        ident=self.current;index=self.selector.currentIndex();self.current=None
        self.config['profiles']=[p for p in self.config['profiles'] if p['id']!=ident]
        self.selector.removeItem(index)
        if self.current is None:self.switch()

    def values(self,model_id=None):
        self.stash();value=copy.deepcopy(self.config);value['active']=self.current
        value.update({key:field.isChecked() for key,field in self.flags.items()});value['strength']=self.strength.value()
        value['translation_enabled']=self.translation_enabled.isChecked()
        for profile in value['profiles']:
            if '_preferred_rows' not in profile:continue
            mapping={};seen=set()
            for language,source,target in profile.pop('_preferred_rows'):
                if not any((language,source,target)):continue
                if not all((language,source,target)):raise ValueError('首选译名每行须填写目标语言、原文术语与译名')
                key=(language.casefold(),source.casefold())
                if key in seen:raise ValueError('同一目标语言存在重复原文术语')
                seen.add(key);mapping.setdefault(language,{})[source]=target
            profile['preferred_names']=mapping
        value=normalize_domain_bias(value,strict=True)
        if model_id:validate_context(value,model_id)
        return value

    def update_counter(self):
        terms=[s.strip() for s in self.terms.toPlainText().splitlines() if s.strip()]
        self.counter.setText(f'{len(terms)} 个词组 · 最多 64 个。' + ('' if self.model_id in ('parakeet','parakeet-v3') else '启用的提示会在保存时后台检查 160 token 上限。'))
