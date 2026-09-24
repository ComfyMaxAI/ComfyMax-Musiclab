"""Two waveform views sharing selection and an audio-clock playhead."""
import math
import numpy as np
from PySide6.QtCore import Qt, Signal, QRectF, QPointF
from PySide6.QtGui import QColor, QPainter, QPen, QPolygonF, QPainterPath
from PySide6.QtWidgets import QWidget, QToolTip
from .project import playable


def timestamp(value):
    if not isinstance(value, (int, float)) or not math.isfinite(value):
        return 'Invalid'
    millis = round(value * 1000)
    return f'{millis//60000:02d}:{(millis//1000)%60:02d}.{millis%1000:03d}'


class Waveform(QWidget):
    seek = Signal(float)
    audition = Signal(int)
    rangeSelected = Signal(float, float)
    viewChanged = Signal(float, float)
    cutSelected = Signal(int)
    cutMoved = Signal(int, int)
    sceneSelected = Signal(int)

    def __init__(self, overview=False):
        super().__init__()
        self.overview = overview
        self.setMinimumHeight(80 if overview else 210)
        self.setMouseTracking(True)
        self.setAccessibleName('Whole-song overview' if overview else 'Detailed audio timeline')
        self.doc = None
        self.peaks = None
        self.start, self.span, self.position = 0., 30., 0.
        self.selected = -1
        self.context = None
        self.press = None
        self.drag_time = None
        self.cut_drag = None
        self.selected_cut = None
        self.scene_index = -1
        self.split_position = None

    def scene_lane(self):
        return bool(self.doc and (self.doc.data.get('scenes_initialized') or self.doc.data.get('chunk_boundaries')))

    @property
    def duration(self):
        return self.doc.duration if self.doc else 1.

    def visible_window(self):
        return (0., self.duration) if self.overview else (self.start, self.span)

    def x(self, t):
        a, span = self.visible_window()
        return 8 + (t-a)/span * max(1, self.width()-16)

    def t(self, x):
        a, span = self.visible_window()
        return max(0., min(self.duration, a+(x-8)/max(1, self.width()-16)*span))

    def set_view(self, start, span):
        self.span = min(self.duration, max(min(1., self.duration), span))
        self.start = max(0., min(start, self.duration-self.span))
        self.update()

    def zoom(self, factor):
        center = self.start+self.span/2
        span = min(self.duration, max(1., self.span*factor))
        self.viewChanged.emit(max(0., min(center-span/2, self.duration-span)), span)

    def draw_cached_samples(self, painter, center, amplitude):
        a,span=self.visible_window(); width=max(1,self.width()-16)
        key=(a,span,width,center,amplitude)
        if getattr(self,'_sample_path_key',None)!=key or getattr(self,'_sample_path_source',None) is not self.peaks:
            low,high,step=self.peaks; path=QPainterPath()
            # Reduce only the visible, already-decoded peak bins in one NumPy
            # operation. Follow used to cause thousands of tiny reductions/tick.
            pixels=np.arange(width)
            first=np.maximum(0,((a+span*pixels/width)/step).astype(np.int64))
            last=np.minimum(len(low),np.maximum(first+1,((a+span*(pixels+1)/width)/step).astype(np.int64)))
            valid=first<len(low)
            if np.any(valid):
                unique,inverse=np.unique(first[valid],return_inverse=True)
                stop=int(last[valid][-1])
                lows=np.minimum.reduceat(low[:stop],unique)[inverse]
                highs=np.maximum.reduceat(high[:stop],unique)[inverse]
                for pixel,lo,hi in zip(pixels[valid],lows,highs):
                    path.moveTo(float(pixel+8),center-min(1,float(hi))*amplitude)
                    path.lineTo(float(pixel+8),center-max(-1,float(lo))*amplitude)
            self._sample_path=path; self._sample_path_key=key; self._sample_path_source=self.peaks
        painter.drawPath(self._sample_path)

    def paintEvent(self, event):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor('#fafbfc'))
        if not self.doc or self.peaks is None:
            p.setPen(QColor('#52606d'))
            p.drawText(self.rect(), Qt.AlignCenter, 'Import a Stage 1 analysis or open an editor project')
            return
        a, span = self.visible_window()
        bottom = self.height()-8 if self.overview else self.height()-(96 if self.scene_lane() else 66)
        top = 23
        center, amp = (top+bottom)/2, (bottom-top)/2*.92
        if self.context:
            left, right = self.context
            p.fillRect(QRectF(self.x(left),top,self.x(right)-self.x(left),bottom-top),QColor('#edf3fe'))
        if 0 <= self.selected < len(self.doc.phrases):
            phrase = self.doc.phrases[self.selected]
            if playable(phrase, self.duration):
                p.fillRect(QRectF(self.x(phrase['start']),top,self.x(phrase['end'])-self.x(phrase['start']),bottom-top),QColor('#d9e7fb'))
        low, high, step = self.peaks
        p.setPen(QPen(QColor('#216a78'), 1))
        width = max(1, self.width()-16)
        self.draw_cached_samples(p, center, amp)
        p.setPen(QColor('#596575'))
        target = span / max(2, width//110)
        tick = next((s for s in [.1,.2,.5,1,2,5,10,15,30,60,120,300,600,1800] if s>=target),3600)
        value = math.ceil(a/tick)*tick
        while value <= a+span:
            x = self.x(value)
            p.drawText(QRectF(x+3,0,105,20), Qt.AlignLeft|Qt.AlignVCenter, timestamp(value))
            value += tick
        if self.overview:
            p.setPen(QPen(QColor('#3b6db1'), 2))
            p.drawRect(QRectF(self.x(self.start),top,self.span/self.duration*width,bottom-top))
        else:
            for region in self.doc.analysis.get('regions', []):
                left, right = max(a,region['start']), min(a+span,region['end'])
                if right <= left:
                    continue
                rect = QRectF(self.x(left),bottom+7,max(1.,self.x(right)-self.x(left)),24)
                vocal = region['kind']=='vocal'
                p.fillRect(rect,QColor('#dceee8' if vocal else '#e7e7eb'))
                p.setPen(QColor('#304c45' if vocal else '#474751'))
                p.drawRect(rect)
                if rect.width()>65:
                    label = 'Vocal' if vocal else 'Instrumental'
                    if vocal and not any(playable(s,self.duration) and s['end']>left and s['start']<right for s in self.doc.phrases):
                        label = 'Vocal — no transcript'
                    p.drawText(rect.adjusted(4,0,-2,0),Qt.AlignVCenter,label)
            for i, phrase in enumerate(self.doc.phrases):
                if not playable(phrase,self.duration):
                    continue
                left,right=max(a,phrase['start']),min(a+span,phrase['end'])
                if right<=left:
                    continue
                rect=QRectF(self.x(left),bottom+35,max(2.,self.x(right)-self.x(left)),23)
                p.fillRect(rect,QColor('#426fa4' if i==self.selected else '#e4edf8'))
                p.setPen(QColor('white' if i==self.selected else '#29435f'))
                p.drawRect(rect)
                if rect.width()>26:
                    p.drawText(rect.adjusted(3,0,-1,0),Qt.AlignVCenter,f'{i+1}' + (' ?' if phrase.get('suspect') else ''))
            if self.scene_lane():
                rate=self.doc.data['timeline']['sample_rate']
                bounds=[0]+self.doc.data.get('chunk_boundaries',[])+[self.doc.data['timeline']['frames']]
                for i,(left,right) in enumerate(zip(bounds,bounds[1:])):
                    left,right=left/rate,right/rate
                    if right<a or left>a+span: continue
                    rect=QRectF(self.x(max(a,left)),bottom+64,max(1,self.x(min(a+span,right))-self.x(max(a,left))),24)
                    p.fillRect(rect,QColor('#f3d49b' if i==self.scene_index else '#fff0d5'))
                    p.setPen(QColor('#775016')); p.drawRect(rect)
                    if rect.width()>40: p.drawText(rect.adjusted(3,0,-1,0),Qt.AlignVCenter,f'Scene {i+1}')
                for cut in self.doc.data.get('chunk_boundaries',[]):
                    t=cut/rate
                    if a<=t<=a+span:
                        p.setPen(QPen(QColor('#ad5a00'),3 if cut==self.selected_cut else 1))
                        p.drawLine(QPointF(self.x(t),bottom+61),QPointF(self.x(t),self.height()-3))
                if self.cut_drag is not None and self.drag_time is not None:
                    p.setPen(QPen(QColor('#ad5a00'),2,Qt.DashLine))
                    p.drawLine(QPointF(self.x(self.drag_time),20),QPointF(self.x(self.drag_time),self.height()-3))
        if self.press is not None and self.drag_time is not None and not self.overview:
            left,right=sorted([self.press[1],self.drag_time])
            p.fillRect(QRectF(self.x(left),top,self.x(right)-self.x(left),bottom-top),QColor(60,105,180,50))
        if self.split_position is not None and a<=self.split_position<=a+span:
            p.setPen(QPen(QColor('#843bb0'),2,Qt.DashLine))
            x=self.x(self.split_position)
            p.drawLine(QPointF(x,20),QPointF(x,self.height()-3))
            p.drawText(QRectF(x+4,20,90,20),Qt.AlignLeft,'Phrase split')
        if a<=self.position<=a+span:
            p.setPen(QPen(QColor('#b02c35'), 2))
            x=self.x(self.position)
            p.drawLine(QPointF(x,20),QPointF(x,self.height()-3))

    def mousePressEvent(self,event):
        if self.doc and event.button()==Qt.LeftButton:
            self.press=(event.position().x(),self.t(event.position().x()),event.position().y())
            self.cut_drag=None
            if not self.overview and self.scene_lane() and event.position().y()>=self.height()-33:
                rate=self.doc.data['timeline']['sample_rate']
                nearest=min(self.doc.data.get('chunk_boundaries',[]),key=lambda c:abs(self.x(c/rate)-event.position().x()),default=None)
                if nearest is not None and abs(self.x(nearest/rate)-event.position().x())<=8:
                    self.cut_drag=nearest; self.cutSelected.emit(nearest)

    def mouseMoveEvent(self,event):
        if not self.doc:
            return
        t=self.t(event.position().x())
        if self.press:
            self.drag_time=t
            self.update()
        else:
            region=next((r for r in self.doc.analysis.get('regions',[]) if r['start']<=t<r['end']),None)
            self.setToolTip(timestamp(t)+(f" • {region['kind'].title()} (estimated)" if region else ''))

    def mouseReleaseEvent(self,event):
        if self.press is None:
            return
        x,start,y=self.press
        end=self.t(event.position().x())
        self.press=self.drag_time=None
        if self.cut_drag is not None:
            old=self.cut_drag; self.cut_drag=None
            if abs(event.position().x()-x)>4: self.cutMoved.emit(old,round(end*self.doc.data['timeline']['sample_rate']))
        elif not self.overview and self.scene_lane() and y>=self.height()-33:
            rate=self.doc.data['timeline']['sample_rate']; bounds=[0]+self.doc.data.get('chunk_boundaries',[])+[self.doc.data['timeline']['frames']]
            index=next((i for i,(a,b) in enumerate(zip(bounds,bounds[1:])) if a<=end*rate<b),len(bounds)-2)
            self.sceneSelected.emit(index)
        elif self.overview:
            self.viewChanged.emit(max(0.,min(end-self.span/2,self.duration-self.span)),self.span)
            self.seek.emit(end)
        elif abs(event.position().x()-x)>4:
            self.rangeSelected.emit(min(start,end),max(start,end))
        elif y>=self.height()-(61 if self.scene_lane() else 31):
            candidates=[i for i,s in enumerate(self.doc.phrases) if playable(s,self.duration) and s['start']<=end<=s['end']]
            if candidates:
                self.audition.emit(candidates[0])
        else:
            self.seek.emit(end)
        self.update()

    def wheelEvent(self,event):
        if not self.doc:
            return
        if event.modifiers() & Qt.ControlModifier:
            self.zoom(.75 if event.angleDelta().y()>0 else 1.333333)
        else:
            direction=-1 if event.angleDelta().y()>0 else 1
            self.viewChanged.emit(max(0.,min(self.start+direction*self.span*.15,self.duration-self.span)),self.span)
        event.accept()
