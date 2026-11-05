"""Incremental, event-driven caption view with persistent follow intent.

The view never infers a reader's intent from scrollbar values.  Layout changes
and programmatic scrolls may move the range at any time; only wheel, keyboard
navigation and scrollbar actions pause following.  While following, every
range update is answered by pinning the current maximum, so a transient range
reported immediately after an edit cannot strand the view above the last row.
"""
from PySide6.QtCore import QPoint,Qt,QTimer,Signal
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import QTextBrowser

from caption_visibility import caption_visibility,visible_caption_pair
from typography import (CaptionFormatter,apply_head_padding,apply_tail_padding,
    clear_tail_padding,normalize_typography,render_caption_rows)

SIGNATURE_FIELDS=('en','zh','start_ms','uncertain','timing','provisional')
NAV_KEYS={Qt.Key.Key_PageUp,Qt.Key.Key_PageDown,Qt.Key.Key_Home,Qt.Key.Key_End,
    Qt.Key.Key_Up,Qt.Key.Key_Down,Qt.Key.Key_Space}


def row_identity(row):
    """Stable logical identity: draft request ids rotate, sentence ids do not."""
    return str(row.get('logical_id') or row.get('id'))


def row_signature(row):
    return tuple(row.get(k) for k in SIGNATURE_FIELDS)


class CaptionView(QTextBrowser):
    followChanged=Signal(bool)

    def __init__(self,parent=None):
        super().__init__(parent)
        self.setOpenExternalLinks(False)
        self._follow=True
        self._dragging=False
        self._pending=None
        self._rows=[]
        self._blocks=[]
        self._formatter=None
        self._style=None
        self._settings=None
        self._colors=None
        self._anchor=None
        self._anchor_pending=False
        self._rebase=False
        self._generation=0
        self._full_rebuilds=0
        self._incremental_updates=0
        self._row_replacements=0
        self._pins=0
        bar=self.verticalScrollBar()
        bar.rangeChanged.connect(self._on_range_changed)
        bar.sliderPressed.connect(self._on_slider_pressed)
        bar.sliderMoved.connect(self._on_slider_moved)
        bar.sliderReleased.connect(self._on_slider_released)
        bar.actionTriggered.connect(self._on_action_triggered)

    @property
    def following(self):return self._follow
    @property
    def full_rebuild_count(self):return self._full_rebuilds
    @property
    def incremental_update_count(self):return self._incremental_updates
    @property
    def row_replacement_count(self):return self._row_replacements
    @property
    def pin_count(self):return self._pins

    def visible_rows(self):return list(self._rows)
    def visible_row_ids(self):return [row_identity(row) for row in self._rows]

    def request_rebase(self):
        """Next set_captions() may replace the window (load older records)."""
        self._rebase=True

    def consume_rebase(self):
        value=self._rebase;self._rebase=False;return value

    def clear_state(self):
        """Drop cached rows/edits before a non-session placeholder is shown."""
        self._generation+=1
        self._pending=None
        self._rows=[]
        self._blocks=[]
        self._anchor=None
        self._anchor_pending=False
        self._rebase=False
        self._dragging=False
        self._follow=True

    def reading_anchor(self):
        return dict(self._anchor) if self._anchor else self._capture_anchor()

    def set_captions(self,rows,colors,settings,reset=False,rebase=False):
        rows=[dict(row) for row in rows]
        style=self._style_key(colors,settings)
        if self._dragging and not reset and not rebase:
            self._pending=(rows,colors,dict(settings),reset,rebase)
            return
        self._pending=None
        self._settings=settings;self._colors=colors
        force_full=reset or rebase or self._style!=style
        if force_full:
            self._rebase=False
            anchor=None if self._follow else self._capture_anchor()
            self._full_rebuild(rows,colors,settings)
            self._style=style
            if reset and not self._follow:
                self._follow=True
                self.followChanged.emit(True)
            if self._follow:
                self._anchor=None;self._anchor_pending=False
                self._pin_if_following()
            else:self._restore_after(anchor)
            return
        self._formatter=CaptionFormatter(colors,settings)
        if self._follow:
            self._anchor=None
            self._apply_diff(rows)
            self._pin_if_following()
        else:
            anchor=self._capture_anchor()
            self._apply_diff(self._merge_paused(rows))
            self._restore_after(anchor)

    @staticmethod
    def _style_key(colors,settings):
        t=normalize_typography(settings.get('typography'))
        return (tuple(sorted(colors.items())),tuple(sorted(t.items())),
            caption_visibility(settings),settings.get('english_size'),settings.get('chinese_size'))

    def _full_rebuild(self,rows,colors,settings):
        self.setUpdatesEnabled(False)
        try:
            self._formatter=CaptionFormatter(colors,settings)
            self._blocks=render_caption_rows(self,rows,colors,settings)
            self._rows=list(rows)
            self._full_rebuilds+=1
        finally:
            self.setUpdatesEnabled(True)

    def _merge_paused(self,new_rows):
        """Keep the reading snapshot; apply updates only to visible identities."""
        by_key={row_identity(row):row for row in new_rows}
        return [by_key.get(row_identity(row),row) for row in self._rows]

    def _apply_diff(self,new_rows):
        no_old=not self._rows;no_new=not new_rows
        if no_old and no_new:return
        if no_old or no_new:
            self._rebase_with(new_rows);return
        old_keys=[row_identity(row) for row in self._rows]
        new_keys=[row_identity(row) for row in new_rows]
        if len(set(old_keys))!=len(old_keys) or len(set(new_keys))!=len(new_keys):
            self._rebase_with(new_rows);return
        old_pos={key:index for index,key in enumerate(old_keys)}
        new_pos={key:index for index,key in enumerate(new_keys)}
        common=[key for key in old_keys if key in new_pos]
        if not common:
            self._rebase_with(new_rows);return
        old_common=[old_pos[key] for key in common]
        new_common=[new_pos[key] for key in common]
        if not all(a<b for a,b in zip(old_common,old_common[1:])) or not all(a<b for a,b in zip(new_common,new_common[1:])):
            self._rebase_with(new_rows);return
        first_old,last_old=old_common[0],old_common[-1]
        first_new,last_new=new_common[0],new_common[-1]
        if last_old-first_old+1!=len(common) or last_new-first_new+1!=len(common):
            self._rebase_with(new_rows);return
        if first_new:  # older rows prepended: a user-driven rebase
            self._rebase_with(new_rows);return
        changed=[(old_index,new_index) for old_index,new_index in zip(old_common,new_common)
            if row_signature(self._rows[old_index])!=row_signature(new_rows[new_index])]
        tail_remove=len(old_keys)-last_old-1
        tail_add=len(new_keys)-last_new-1
        if not changed and not tail_remove and not first_old and not tail_add:return
        if any(self._blocks[old_index][1]==0 for old_index,_ in changed):
            self._rebase_with(new_rows);return
        self.setUpdatesEnabled(False)
        try:
            if self._rows:
                clear_tail_padding(self.document(),self._formatter)
            for old_index,new_index in changed:
                self._replace_row(old_index,new_rows[new_index],first=(old_index==0))
            if changed and changed[0][0]==0:apply_head_padding(self.document(),self._formatter)
            if tail_remove:self._remove_tail(tail_remove)
            if first_old:self._remove_head(first_old)
            if tail_add:self._append_rows(new_rows[last_new+1:])
            if self._rows:apply_tail_padding(self.document(),self._formatter)
        finally:
            self.setUpdatesEnabled(True)
        self._incremental_updates+=1

    def _rebase_with(self,rows):
        anchor=None if self._follow else self._capture_anchor()
        self._full_rebuild(rows,self._colors,self._settings)
        if self._follow:self._pin_if_following()
        else:self._restore_after(anchor)

    def _row_has_blocks(self,row):
        source,translation=visible_caption_pair(row['en'],row['zh'],
            self._formatter.show_source,self._formatter.show_translation)
        return bool(source or translation)

    def _replace_row(self,index,row,first):
        first_block,old_count=self._blocks[index]
        self._remove_blocks(first_block,old_count)
        doc=self.document()
        if not self._row_has_blocks(row):
            new_count=0
        else:
            empty=doc.blockCount()==1 and not doc.firstBlock().text()
            if empty:
                cursor=QTextCursor(doc)
                new_count=self._formatter.insert_row(cursor,row,first=True)
            elif first_block>=doc.blockCount():
                cursor=QTextCursor(doc);cursor.movePosition(QTextCursor.MoveOperation.End)
                new_count=self._formatter.insert_row(cursor,row,first=first,new_block=True)
            else:
                cursor=QTextCursor(doc);cursor.setPosition(doc.findBlockByNumber(first_block).position())
                cursor.insertBlock();cursor.movePosition(QTextCursor.MoveOperation.PreviousBlock)
                new_count=self._formatter.insert_row(cursor,row,first=first)
        delta=new_count-old_count
        self._blocks[index]=(first_block,new_count)
        for position in range(index+1,len(self._blocks)):
            start,count=self._blocks[position];self._blocks[position]=(start+delta,count)
        self._rows[index]=row
        self._row_replacements+=1

    def _append_rows(self,rows):
        if not rows:return
        if not self._rows:
            self._rebase_with(rows);return
        doc=self.document()
        for row in rows:
            start=doc.blockCount()
            if not self._row_has_blocks(row):
                self._rows.append(row);self._blocks.append((start,0));continue
            cursor=QTextCursor(doc)
            if doc.blockCount()==1 and not doc.firstBlock().text():
                count=self._formatter.insert_row(cursor,row,first=True)
            else:
                cursor.movePosition(QTextCursor.MoveOperation.End)
                count=self._formatter.insert_row(cursor,row,first=False,new_block=True)
            self._rows.append(row);self._blocks.append((start,count))

    def _remove_head(self,count):
        if count<=0:return
        blocks=sum(item[1] for item in self._blocks[:count])
        if blocks:self._remove_blocks(0,blocks)
        del self._rows[:count];del self._blocks[:count]
        for position,(start,block_count) in enumerate(self._blocks):
            self._blocks[position]=(start-blocks,block_count)
        if self._rows:apply_head_padding(self.document(),self._formatter)

    def _remove_tail(self,count):
        if count<=0:return
        start_index=len(self._rows)-count
        first_block=self._blocks[start_index][0]
        blocks=sum(item[1] for item in self._blocks[start_index:])
        if blocks:self._remove_blocks(first_block,blocks)
        del self._rows[start_index:];del self._blocks[start_index:]

    def _remove_blocks(self,first,count):
        if count<=0:return
        doc=self.document();total=doc.blockCount()
        if first<=0 and count>=total:
            doc.clear();return
        if first+count<total:
            start=doc.findBlockByNumber(first).position();end=doc.findBlockByNumber(first+count).position()
            cursor=QTextCursor(doc);cursor.setPosition(start);cursor.setPosition(end,QTextCursor.MoveMode.KeepAnchor)
            cursor.removeSelectedText()
        else:
            start=doc.findBlockByNumber(first).position()-1
            cursor=QTextCursor(doc);cursor.setPosition(start)
            cursor.movePosition(QTextCursor.MoveOperation.End,QTextCursor.MoveMode.KeepAnchor)
            cursor.removeSelectedText()

    def _row_index_for_block(self,number):
        for index,(start,count) in enumerate(self._blocks):
            if count and start<=number<start+count:return index
        return None

    def _capture_anchor(self):
        if not self._rows:return None
        doc=self.document()
        if doc.blockCount()==0:return None
        block=self.cursorForPosition(QPoint(0,0)).block()
        if not block.isValid():return None
        number=block.blockNumber()
        index=self._row_index_for_block(number)
        if index is None:
            index=0
            for position,(start,count) in enumerate(self._blocks):
                if count and start<=number:index=position
        first,count=self._blocks[index]
        ordinal=max(0,min(number-first,count-1)) if count else 0
        top=doc.documentLayout().blockBoundingRect(block).top()
        return {'key':row_identity(self._rows[index]),'ordinal':ordinal,
            'offset':top-self.verticalScrollBar().value(),'index':index}

    def _restore_after(self,anchor):
        if self._follow:return
        self._anchor=anchor
        if anchor:self._schedule_anchor_restore()

    def _schedule_anchor_restore(self):
        if self._anchor_pending:return
        self._anchor_pending=True
        generation=self._generation
        QTimer.singleShot(0,lambda:self._restore_anchor(generation))

    def _restore_anchor(self,generation=None):
        if generation is not None and generation!=self._generation:return
        self._anchor_pending=False
        anchor=self._anchor
        if not anchor or self._follow or self._dragging or not self._rows:return
        index=next((position for position,row in enumerate(self._rows)
            if row_identity(row)==anchor['key']),None)
        if index is None:index=max(0,min(anchor.get('index',0),len(self._rows)-1))
        first,count=self._blocks[index]
        if count==0:
            for distance in range(1,len(self._rows)+1):
                found=False
                for candidate in (index-distance,index+distance):
                    if 0<=candidate<len(self._rows) and self._blocks[candidate][1]:
                        first,count=self._blocks[candidate];found=True;break
                if found:break
        if count==0:return
        number=first+min(anchor.get('ordinal',0),count-1)
        block=self.document().findBlockByNumber(number)
        if not block.isValid():return
        top=self.document().documentLayout().blockBoundingRect(block).top()
        value=int(round(top-anchor.get('offset',0)))
        bar=self.verticalScrollBar();bar.setValue(max(0,min(value,bar.maximum())))

    def _pause_following(self):
        if self._follow:
            self._follow=False;self.followChanged.emit(False)

    def _bottom_reached(self):
        bar=self.verticalScrollBar()
        if bar.value()>=bar.maximum()-1:return True
        index=len(self._rows)-1
        while index>=0 and self._blocks[index][1]==0:index-=1
        if index<0:return True
        first,count=self._blocks[index]
        doc=self.document();layout=doc.documentLayout()
        top=layout.blockBoundingRect(doc.findBlockByNumber(first)).top()
        bottom=layout.blockBoundingRect(doc.findBlockByNumber(first+count-1)).bottom()
        visible_bottom=bar.value()+self.viewport().height()
        return top>=bar.value()-2 and bottom<=visible_bottom+2

    def _settle_user_intent(self,generation=None):
        if generation is not None and generation!=self._generation:return
        if self._dragging:return
        if self._bottom_reached():
            if not self._follow:
                self._follow=True;self._anchor=None
                self.followChanged.emit(True)
            self._pin_if_following()
        elif not self._follow:
            self._anchor=self._capture_anchor()

    def _user_scrolled(self):
        self._anchor=None
        self._pause_following()
        generation=self._generation
        QTimer.singleShot(0,lambda:self._settle_user_intent(generation))

    def _pin_if_following(self):
        if not self._follow or self._dragging:return
        bar=self.verticalScrollBar()
        if bar.value()!=bar.maximum():
            bar.setValue(bar.maximum())
        self._pins+=1

    def _on_range_changed(self,_minimum,_maximum):
        if self._dragging:return
        if self._follow:self._pin_if_following()
        elif self._anchor is not None:self._schedule_anchor_restore()

    def _on_slider_pressed(self):
        self._dragging=True;self._anchor=None;self._pause_following()

    def _on_slider_moved(self,_value):
        if not self._dragging:self._dragging=True
        self._pause_following()

    def _on_slider_released(self):
        self._dragging=False
        if self._pending:
            rows,colors,settings,reset,rebase=self._pending;self._pending=None
            self.set_captions(rows,colors,settings,reset=reset,rebase=rebase)
        self._settle_user_intent()

    def _on_action_triggered(self,_action):
        self._user_scrolled()

    def wheelEvent(self,event):
        super().wheelEvent(event)
        if event.angleDelta().y():self._user_scrolled()

    def keyPressEvent(self,event):
        super().keyPressEvent(event)
        if event.key()==Qt.Key.Key_End:
            # Read-only QTextBrowser does not consistently scroll on Ctrl+End.
            if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                self.verticalScrollBar().setValue(self.verticalScrollBar().maximum())
        elif event.key()==Qt.Key.Key_Home:
            if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                self.verticalScrollBar().setValue(0)
        if event.key() in NAV_KEYS:self._user_scrolled()

    def resizeEvent(self,event):
        super().resizeEvent(event)
        if self._follow:QTimer.singleShot(0,self._pin_if_following)
        elif self._anchor is not None:self._schedule_anchor_restore()
