"""Waveform with a manual marker lane; no phrase or region boundary behavior."""
import math
import numpy as np
from PySide6.QtCore import Qt,QRectF,QPointF
from PySide6.QtGui import QPainter,QColor,QPen
from .waveform import Waveform,timestamp
from .music_overlay import MusicOverlay,LANE_HEIGHT


class MarkerWaveform(Waveform):
    def __init__(self,overview=False):
        super().__init__(overview)
        self.markers=[]; self.selected_marker=None; self.marker_drag=None; self.drag_preview=None
        self.music_overlay=MusicOverlay()
        self.setAccessibleName('Song overview' if overview else 'Waveform and manual markers')

    def paintEvent(self,event):
        p=QPainter(self); p.fillRect(self.rect(),QColor('#131720'))
        if not self.doc or self.peaks is None:
            p.setPen(QColor('#a6adbb')); p.drawText(self.rect(),Qt.AlignCenter,'Load a song or open a project'); return
        a,span=self.visible_window(); width=max(1,self.width()-16)
        top=23; marker_top=self.height()-35
        lane_height=LANE_HEIGHT if not self.overview and self.doc.data.get('music_analysis') else 0
        bottom=self.height()-8 if self.overview else marker_top-lane_height
        if self.context:
            left,right=self.context
            p.fillRect(QRectF(self.x(left),top,self.x(right)-self.x(left),bottom-top),QColor('#26364c'))
        low,high,step=self.peaks; center=(top+bottom)/2; amp=(bottom-top)*.46
        p.setPen(QPen(QColor('#69b9bd'),1))
        self.draw_cached_samples(p, center, amp)
        self.music_overlay.draw(p,self,top,bottom)
        target=span/max(2,width//110)
        tick=next((t for t in [.1,.2,.5,1,2,5,10,15,30,60,120,300,600,1800] if t>=target),3600)
        value=math.ceil(a/tick)*tick; p.setPen(QColor('#a6adbb'))
        while value<=a+span:
            p.drawText(QRectF(self.x(value)+3,0,105,20),Qt.AlignLeft|Qt.AlignVCenter,timestamp(value)); value+=tick
        if self.overview:
            p.setPen(QPen(QColor('#8da9e8'),2)); p.drawRect(QRectF(self.x(self.start),top,self.span/self.duration*width,bottom-top))
        else:
            p.fillRect(QRectF(8,marker_top+4,width,26),QColor('#30291e'))
            p.setPen(QColor('#e8bd73')); p.drawText(QRectF(12,marker_top+4,80,24),Qt.AlignVCenter,'Markers')
        rate=self.doc.data['timeline']['sample_rate']
        for i,frame in enumerate([0]+self.markers+[self.doc.data['timeline']['frames']]):
            seconds=frame/rate
            if a<=seconds<=a+span:
                x=self.x(seconds); selected=frame==self.selected_marker
                p.setPen(QPen(QColor('#f0b65d'),3 if selected else 1))
                p.drawLine(QPointF(x,top),QPointF(x,self.height()-4))
                if not self.overview:
                    label='Start' if frame==0 else 'End' if frame==self.doc.data['timeline']['frames'] else f'M{i}'
                    p.drawText(QRectF(x+4,marker_top+4,70,24),Qt.AlignVCenter,label)
        if self.drag_preview is not None:
            p.setPen(QPen(QColor('#f0b65d'),2,Qt.DashLine)); x=self.x(self.drag_preview)
            p.drawLine(QPointF(x,top),QPointF(x,self.height()-4))
        if a<=self.position<=a+span:
            p.setPen(QPen(QColor('#ff4b4b'),2)); x=self.x(self.position)
            p.drawLine(QPointF(x,top),QPointF(x,self.height()-3))

    def mousePressEvent(self,event):
        if not self.doc or event.button()!=Qt.LeftButton: return
        x=event.position().x(); self.press=(x,self.t(x)); self.marker_drag=None
        if not self.overview:
            rate=self.doc.data['timeline']['sample_rate']
            near=min(self.markers,key=lambda m:abs(self.x(m/rate)-x),default=None)
            if near is not None and abs(self.x(near/rate)-x)<8:
                self.marker_drag=near; self.cutSelected.emit(near)

    def mouseMoveEvent(self,event):
        if not self.doc: return
        seconds=self.t(event.position().x())
        if self.marker_drag is not None:
            self.drag_preview=seconds; self.update()
        chord_lane=(not self.overview and self.doc.data.get('music_analysis') and
                    self.height()-35-LANE_HEIGHT <= event.position().y() < self.height()-35)
        self.setToolTip(self.music_overlay.tooltip(seconds) if chord_lane else f'{seconds:.6f} seconds')

    def mouseReleaseEvent(self,event):
        if self.press is None: return
        x,_=self.press; seconds=self.t(event.position().x())
        if self.marker_drag is not None:
            if abs(event.position().x()-x)>3:
                self.cutMoved.emit(self.marker_drag,round(seconds*self.doc.data['timeline']['sample_rate']))
        else:
            if self.overview: self.viewChanged.emit(max(0,min(seconds-self.span/2,self.duration-self.span)),self.span)
            self.seek.emit(seconds)
        self.press=self.marker_drag=self.drag_preview=None; self.update()
