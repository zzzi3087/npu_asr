"""Persistent settings pages with compact, searchable category navigation."""
from PySide6.QtCore import Qt,Signal
from PySide6.QtWidgets import (QWidget,QVBoxLayout,QHBoxLayout,QLineEdit,QListWidget,QListWidgetItem,
    QStackedWidget,QLabel,QSizePolicy,QToolButton)


PAGE_INFO={
    '收音设置':('','音频来源、模型、语言、分句与音量增益。','麦克风 桌面 系统音频 声音 播放设备 NPU Qwen Mega Parakeet 英语 多语言 分句 预览 更新间隔 省电 GPU 特征 音量 自动 手动 增益 轻声 电平 峰值'),
    '翻译服务':('声音与语言','选择目标语言与服务；密钥留空保留本机已有凭据。','API 地址 模型 密钥 OpenAI Anthropic 翻译 目标语言 云端'),
    '接口高级参数':('声音与语言','当前翻译服务的高级参数，通常可保持默认。','API JSON 认证 Bearer token Anthropic 工作区 版本'),
    '桌面字幕':('字幕与外观','独立透明字幕；预览后保存，取消恢复。','悬浮 透明 字体 颜色 字号 描边 位置 锁定 鼠标 间距'),
    '工作台字体':('字幕与外观','工作台窗口、字体与间距；与桌面字幕分别设置。','字号 窗口 透明度 置顶 原文 译文 字体 行高 间距 粗体'),
    '工作台配色':('字幕与外观','预设与自定义配色；保存后保留，取消撤销。','颜色 全局 配色 背景 文字 按钮 方案'),
    '领域词表':('词表与数据','选取当前专业背景与术语；保存后下次收音生效。','物理学 高能 理论 天体 暗物质 专有名词 词表 热词'),
    '存储与配置':('词表与数据','本机历史位置、配置导入与导出。','历史 保存 导入 导出 配置 备份 存储 文件夹'),
}


class SettingsPages(QWidget):
    currentChanged=Signal(int)
    def __init__(self,parent=None):
        super().__init__(parent);self.pages=[];self.order=[];self._ready=False
        row=QHBoxLayout(self);row.setContentsMargins(0,0,0,0);row.setSpacing(18)
        side=QWidget();side.setFixedWidth(184);column=QVBoxLayout(side);column.setContentsMargins(0,0,0,0)
        self.search=QLineEdit();self.search.setPlaceholderText('查找设置…');self.search.setClearButtonEnabled(True)
        self.search.setAccessibleName('搜索设置分类');column.addWidget(self.search)
        self.navigation=QListWidget();self.navigation.setObjectName('settingsNavigation')
        self.navigation.setStyleSheet('QListWidget::item { padding:6px 10px; margin:1px 0; }')
        self.navigation.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        column.addWidget(self.navigation,1)
        self.no_results=QLabel('未找到相关分类');self.no_results.setObjectName('muted');self.no_results.hide();column.addWidget(self.no_results)
        row.addWidget(side)
        content=QWidget();detail=QVBoxLayout(content);detail.setContentsMargins(0,0,0,0);detail.setSpacing(8)
        self.heading=QLabel();self.heading.setObjectName('sectionHeading');detail.addWidget(self.heading)
        self.description=QLabel();self.description.setObjectName('muted');self.description.setWordWrap(True);detail.addWidget(self.description)
        self.stack=QStackedWidget();self.stack.setSizePolicy(QSizePolicy.Policy.Expanding,QSizePolicy.Policy.Expanding)
        detail.addWidget(self.stack,1);row.addWidget(content,1)
        self.search.textChanged.connect(self.rebuild_navigation)
        self.navigation.currentItemChanged.connect(self._selected)
        self.stack.currentChanged.connect(self._changed)

    def addTab(self,widget,title):
        self.pages.append((widget,title));return self.stack.addWidget(widget)

    def finalize(self,titles):
        self.order=[next(i for i,(_,name) in enumerate(self.pages) if name==title) for title in titles]
        self._ready=True;self.rebuild_navigation();self._changed(self.currentIndex())

    def rebuild_navigation(self,*_):
        if not self._ready:return
        query=self.search.text().strip().casefold();selected=self.currentIndex();matches=0
        self.navigation.blockSignals(True);self.navigation.clear()
        for index in self.order:
            _,title=self.pages[index];category,description,keywords=PAGE_INFO[title]
            if query and query not in (title+' '+description+' '+keywords).casefold():continue
            item=QListWidgetItem(title);item.setData(Qt.ItemDataRole.UserRole,index);item.setToolTip(description)
            self.navigation.addItem(item);matches+=1
            if index==selected:self.navigation.setCurrentItem(item)
        self.navigation.blockSignals(False);self.no_results.setVisible(not matches)
        if matches and self.navigation.currentItem() is None:
            for n in range(self.navigation.count()):
                if self.navigation.item(n).data(Qt.ItemDataRole.UserRole) is not None:
                    self.navigation.setCurrentRow(n);break

    def _selected(self,item,*_):
        if item and item.data(Qt.ItemDataRole.UserRole) is not None:self.setCurrentIndex(item.data(Qt.ItemDataRole.UserRole))

    def _changed(self,index):
        if index<0 or index>=len(self.pages):return
        title=self.pages[index][1];self.heading.setText(title);self.description.setText(PAGE_INFO.get(title,('',title,''))[1])
        self.navigation.blockSignals(True)
        for n in range(self.navigation.count()):
            item=self.navigation.item(n)
            if item.data(Qt.ItemDataRole.UserRole)==index:self.navigation.setCurrentItem(item);break
        self.navigation.blockSignals(False);self.currentChanged.emit(index)

    def setCurrentIndex(self,index):self.stack.setCurrentIndex(index)
    def currentIndex(self):return self.stack.currentIndex()
    def count(self):return self.stack.count()
    def widget(self,index):return self.stack.widget(index)
    def currentWidget(self):return self.stack.currentWidget()
    def setCurrentWidget(self,widget):
        for index,(root,_) in enumerate(self.pages):
            if root is widget or root.isAncestorOf(widget):self.setCurrentIndex(index);return


class Foldout(QWidget):
    """Instant collapse; keeps draft controls alive without animation or rebuilding."""
    def __init__(self,title,content,parent=None):
        super().__init__(parent);layout=QVBoxLayout(self);layout.setContentsMargins(0,0,0,0)
        button=QToolButton();button.setText(title);button.setCheckable(True)
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon);button.setArrowType(Qt.ArrowType.RightArrow)
        layout.addWidget(button);layout.addWidget(content);content.hide()
        button.toggled.connect(content.setVisible)
        button.toggled.connect(lambda opened:button.setArrowType(Qt.ArrowType.DownArrow if opened else Qt.ArrowType.RightArrow))
